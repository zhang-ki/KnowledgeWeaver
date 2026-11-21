"""Complete-matrix gate and sequential executor for the frozen B07.final suites."""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, TypeVar

from knowledge_weaver.evaluation.dataset import LoadedDataset
from knowledge_weaver.evaluation.generation_binding import (
    GenerationBindingError,
    PersistedGenerationLookup,
    resolve_generation,
    valid_generation_binding,
)
from knowledge_weaver.evaluation.gold_mapping import GoldMappingReport
from knowledge_weaver.evaluation.protocol import BudgetSpec, EvaluationProtocol
from knowledge_weaver.evaluation.redaction import canonical_json_line
from knowledge_weaver.evaluation.runs import (
    BenchmarkExecutor,
    QueryRunRecord,
    run_query_with_retries,
)
from knowledge_weaver.evaluation.scheduling import (
    MatrixSchedule,
    SuiteStatus,
)

CaseT = TypeVar("CaseT")


def _seeded_case_order(cases: tuple[CaseT, ...], seed: int) -> tuple[CaseT, ...]:
    ordered = list(cases)
    random.Random(seed).shuffle(ordered)
    return tuple(ordered)


@dataclass(frozen=True)
class SuiteExecutionStatus:
    suite_id: str
    status: SuiteStatus
    query_count: int
    failed_query_ids: tuple[str, ...] = ()
    incomplete_quality_query_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["failed_query_ids"] = list(self.failed_query_ids)
        value["incomplete_quality_query_ids"] = list(self.incomplete_quality_query_ids)
        return value


@dataclass(frozen=True)
class MatrixExecutionReport:
    schedule: MatrixSchedule
    status: SuiteStatus
    suite_statuses: tuple[SuiteExecutionStatus, ...]
    query_records: tuple[QueryRunRecord, ...]
    blockers: tuple[str, ...] = ()
    policy_training_summary: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if any(
            record.generation_id != self.schedule.generation_id
            or record.generation_sha256 != self.schedule.generation_sha256
            for record in self.query_records
        ):
            raise ValueError("matrix and per-query generation identities must match")

    @property
    def missing_runs(self) -> tuple[str, ...]:
        return tuple(
            suite.suite_id
            for suite in self.suite_statuses
            if suite.status is not SuiteStatus.complete
        ) or self.schedule.missing_runs

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status.value,
            "generation_id": self.schedule.generation_id,
            "generation_sha256": self.schedule.generation_sha256,
            "formal_suite_count": len(self.schedule.suites),
            "expected_query_runs": self.schedule.expected_query_runs,
            "completed_query_records": len(self.query_records),
            "missing_runs": list(self.missing_runs),
            "blockers": list(self.blockers),
            "policy_training_summary": self.policy_training_summary,
            "schedule": self.schedule.to_dict(),
            "suite_statuses": [item.to_dict() for item in self.suite_statuses],
        }


def _block_schedule(schedule: MatrixSchedule, reason: str) -> MatrixSchedule:
    blockers = tuple(dict.fromkeys((*schedule.blockers, reason)))
    suites = tuple(
        replace(suite, status=SuiteStatus.blocked, blockers=blockers)
        for suite in schedule.suites
    )
    return replace(schedule, status=SuiteStatus.blocked, suites=suites, blockers=blockers)


def _blocked_report(schedule: MatrixSchedule, reason: str | None = None) -> MatrixExecutionReport:
    blocked = _block_schedule(schedule, reason) if reason else schedule
    suite_statuses = tuple(
        SuiteExecutionStatus(
            suite_id=suite.suite_id,
            status=SuiteStatus.blocked,
            query_count=0,
            failed_query_ids=(),
        )
        for suite in blocked.suites
    )
    return MatrixExecutionReport(
        schedule=blocked,
        status=SuiteStatus.blocked,
        suite_statuses=suite_statuses,
        query_records=(),
        blockers=blocked.blockers,
    )


def _budget_violations(record: QueryRunRecord, budget: object) -> list[str]:
    values = {
        "max_steps": record.steps,
        "max_tool_calls": record.tool_calls,
        "max_llm_calls": record.llm_calls,
        "max_total_tokens": record.total_tokens,
        "deadline_seconds": record.end_to_end_latency_ms / 1000.0,
    }
    violations: list[str] = []
    for name, actual in values.items():
        limit = getattr(budget, name)
        if actual > limit:
            violations.append(f"{name} exceeded: observed={actual}, limit={limit}")
    return violations


async def run_formal_matrix(
    schedule: MatrixSchedule,
    protocol: EvaluationProtocol,
    dataset: LoadedDataset,
    gold_mapping: GoldMappingReport | None,
    executor: BenchmarkExecutor,
    *,
    generation_resolver: PersistedGenerationLookup | None = None,
    policy_training_summary: Mapping[str, Any] | None = None,
    retry_delay_seconds: float = 0.25,
) -> MatrixExecutionReport:
    """Execute every scheduled suite serially or return explicit blockers without running."""
    if schedule.status is SuiteStatus.blocked:
        return _blocked_report(schedule)
    if not valid_generation_binding(schedule.generation_id, schedule.generation_sha256):
        return _blocked_report(
            schedule,
            "BLOCKED_GENERATION: scheduled actual generation ID and SHA-256 are missing or invalid",
        )
    generation_id = schedule.generation_id
    generation_sha256 = schedule.generation_sha256
    assert generation_id is not None and generation_sha256 is not None
    if not dataset.ready or dataset.manifest.cases_sha256 != schedule.dataset_sha256:
        return _blocked_report(schedule, "BLOCKED_INPUTS: dataset readiness/hash differs from schedule")
    if generation_resolver is None:
        return _blocked_report(
            schedule,
            "BLOCKED_GENERATION: persisted Generation lookup is unavailable",
        )
    try:
        verified_generation = await resolve_generation(
            generation_resolver, generation_id, generation_sha256
        )
    except GenerationBindingError:
        return _blocked_report(
            schedule,
            "BLOCKED_GENERATION: persisted Generation is missing or its identity/content digest is invalid",
        )
    generation_id = verified_generation.id
    generation_sha256 = verified_generation.generation_sha256
    if gold_mapping is None or not gold_mapping.ready:
        details = "Gold span/path mapping is unavailable"
        if gold_mapping is not None and gold_mapping.blockers:
            details = "; ".join(gold_mapping.blockers[:10])
        return _blocked_report(schedule, f"BLOCKED_GOLD_MAPPING: {details}")
    if gold_mapping.dataset_sha256 != schedule.dataset_sha256:
        return _blocked_report(schedule, "BLOCKED_GOLD_MAPPING: mapping dataset hash differs from schedule")
    if gold_mapping.generation_id != generation_id:
        return _blocked_report(
            schedule,
            "BLOCKED_GENERATION: Gold mapping generation ID differs from the scheduled actual generation",
        )
    if gold_mapping.generation_sha256 != generation_sha256:
        return _blocked_report(
            schedule,
            "BLOCKED_GENERATION: Gold mapping generation SHA-256 differs from the scheduled actual generation",
        )
    if executor.provider_kind != "real":
        return _blocked_report(schedule, "BLOCKED_PROVIDER: mock/synthetic executors cannot run formal suites")
    if not schedule.policy_artifact_ready or not schedule.policy_artifact_sha256:
        return _blocked_report(schedule, "BLOCKED_POLICY: train/dev policy artifact is not frozen")
    if not schedule.budget_approved:
        return _blocked_report(schedule, "BLOCKED_BUDGET: experiment budget is not approved")
    if (
        policy_training_summary is None
        or policy_training_summary.get("formal_ready") is not True
        or policy_training_summary.get("synthetic_fixture") is not False
    ):
        return _blocked_report(
            schedule,
            "BLOCKED_POLICY: formal train/dev cost and selection evidence is unavailable",
        )

    test_cases = tuple(item for item in dataset.cases if item.case.split.value == "test")
    if len(test_cases) < protocol.test_case_minimum:
        return _blocked_report(schedule, "BLOCKED_INPUTS: frozen test split is below 40 cases")
    if any(
        suite.expected_query_runs != len(test_cases) * len(suite.cache_states)
        for suite in schedule.suites
    ):
        return _blocked_report(schedule, "BLOCKED_INPUTS: scheduled case count differs from frozen test split")

    budget_by_id: dict[str, BudgetSpec] = {item.id: item for item in protocol.budgets}
    records: list[QueryRunRecord] = []
    suite_statuses: list[SuiteExecutionStatus] = []
    for suite in schedule.suites:
        suite_records: list[QueryRunRecord] = []
        ordered_cases = _seeded_case_order(test_cases, suite.seed)
        for cache_state in suite.cache_states:
            for case in ordered_cases:
                record = await run_query_with_retries(
                    executor,
                    case,
                    suite,
                    formal_dataset=True,
                    cache_state=cache_state,
                    generation_binding=verified_generation,
                    retry_delay_seconds=retry_delay_seconds,
                )
                violations = _budget_violations(record, budget_by_id[suite.budget_id])
                if violations:
                    record = record.model_copy(
                        update={
                            "status": "failed",
                            "formal_quality_eligible": False,
                            "budget_violations": violations,
                        }
                    )
                suite_records.append(record)
                records.append(record)
        failed_cases = tuple(
            f"{item.case_id}@{item.cache_state}"
            for item in suite_records
            if item.status != "complete"
        )
        incomplete_quality_cases = tuple(
            f"{item.case_id}@{item.cache_state}"
            for item in suite_records
            if not item.formal_quality_eligible
        )
        suite_statuses.append(
            SuiteExecutionStatus(
                suite_id=suite.suite_id,
                status=(
                    SuiteStatus.failed
                    if failed_cases or incomplete_quality_cases
                    else SuiteStatus.complete
                ),
                query_count=len(suite_records),
                failed_query_ids=failed_cases,
                incomplete_quality_query_ids=incomplete_quality_cases,
            )
        )
    status = (
        SuiteStatus.complete
        if all(item.status is SuiteStatus.complete for item in suite_statuses)
        else SuiteStatus.failed
    )
    return MatrixExecutionReport(
        schedule=schedule,
        status=status,
        suite_statuses=tuple(suite_statuses),
        query_records=tuple(records),
        blockers=(),
        policy_training_summary=policy_training_summary,
    )


def write_execution_artifacts(
    report: MatrixExecutionReport,
    *,
    repository_root: Path,
    relative_directory: str = "artifacts/b07-final",
) -> dict[str, str]:
    """Write a redacted run manifest and JSONL under the ignored B07 artifact directory."""
    if ":" in relative_directory or "\\" in relative_directory:
        raise ValueError("artifact directory must use normalized relative POSIX syntax")
    artifact_root = (repository_root / "artifacts" / "b07-final").resolve()
    target = (repository_root / Path(*relative_directory.split("/"))).resolve()
    try:
        target.relative_to(artifact_root)
    except ValueError as exc:
        raise ValueError("B07 runtime artifacts must remain under artifacts/b07-final") from exc
    target.mkdir(parents=True, exist_ok=True)

    manifest_path = target / "matrix_manifest.json"
    per_query_path = target / "per_query.jsonl"
    manifest_text = json.dumps(
        json.loads(canonical_json_line(report.to_dict())),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"
    query_text = "".join(
        canonical_json_line(item.model_dump(mode="json")) + "\n"
        for item in report.query_records
    )
    manifest_path.write_text(manifest_text, encoding="utf-8", newline="\n")
    per_query_path.write_text(query_text, encoding="utf-8", newline="\n")
    return {
        "matrix_manifest": str(manifest_path),
        "matrix_manifest_sha256": hashlib.sha256(manifest_text.encode("utf-8")).hexdigest(),
        "per_query": str(per_query_path),
        "per_query_sha256": hashlib.sha256(query_text.encode("utf-8")).hexdigest(),
    }
