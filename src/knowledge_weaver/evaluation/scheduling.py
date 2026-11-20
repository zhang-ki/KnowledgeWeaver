"""Deterministic scheduler for the frozen 111-suite B07.final matrix."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Literal

from knowledge_weaver.evaluation.dataset import LoadedDataset
from knowledge_weaver.evaluation.protocol import EvaluationProtocol

REQUIRED_DEPENDENCIES: dict[str, str] = {
    "B07.prep": "5aedfdb22548dca2fcd1347b8578d5ec56211616",
    "B04": "44355d73c1bb20078495f7a8b06610e914ab9d7c",
    "B05": "2e20666dc3a0913fa3feb863272c1771d3fd2348",
    "B06": "8a4ede85c363f4cf18e772dd57be9984f265153f",
}
MINIMUM_TEST_CASES = 40
_GENERATION_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def valid_generation_binding(generation_id: str | None, generation_sha256: str | None) -> bool:
    return bool(
        generation_id
        and generation_id.strip()
        and generation_sha256
        and _GENERATION_SHA256.fullmatch(generation_sha256)
    )


class SuiteStatus(StrEnum):
    planned = "PLANNED"
    blocked = "BLOCKED"
    running = "RUNNING"
    complete = "COMPLETE"
    failed = "FAILED"


@dataclass(frozen=True)
class ExperimentSuite:
    suite_id: str
    kind: str
    system_id: str
    budget_id: str
    seed: int
    ablation_id: str | None
    status: SuiteStatus
    blockers: tuple[str, ...] = ()
    cache_states: tuple[Literal["cold", "warm"], ...] = ("cold", "warm")
    expected_query_runs: int = MINIMUM_TEST_CASES * 2

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


@dataclass(frozen=True)
class MatrixSchedule:
    protocol_id: str
    protocol_sha256: str
    dataset_sha256: str
    dataset_status: str
    generation_id: str | None
    generation_sha256: str | None
    dependency_commits: dict[str, str]
    provider_profiles: tuple[str, ...]
    budget_approved: bool
    policy_artifact_ready: bool
    policy_artifact_sha256: str | None
    status: SuiteStatus
    expected_query_runs: int
    expected_quality_query_runs: int
    expected_runs_are_lower_bound: bool
    suites: tuple[ExperimentSuite, ...]
    blockers: tuple[str, ...]

    @property
    def missing_runs(self) -> tuple[str, ...]:
        return tuple(suite.suite_id for suite in self.suites if suite.status is SuiteStatus.blocked)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "protocol_id": self.protocol_id,
            "protocol_sha256": self.protocol_sha256,
            "dataset_sha256": self.dataset_sha256,
            "dataset_status": self.dataset_status,
            "generation_id": self.generation_id,
            "generation_sha256": self.generation_sha256,
            "dependency_commits": dict(sorted(self.dependency_commits.items())),
            "provider_profiles": list(self.provider_profiles),
            "budget_approved": self.budget_approved,
            "policy_artifact_ready": self.policy_artifact_ready,
            "policy_artifact_sha256": self.policy_artifact_sha256,
            "status": self.status.value,
            "formal_suite_count": len(self.suites),
            "expected_query_runs": self.expected_query_runs,
            "expected_quality_query_runs": self.expected_quality_query_runs,
            "expected_runs_are_lower_bound": self.expected_runs_are_lower_bound,
            "suites": [suite.to_dict() for suite in self.suites],
            "missing_runs": list(self.missing_runs),
            "blockers": list(self.blockers),
        }


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _suite_id(kind: str, item_id: str, budget_id: str, seed: int) -> str:
    return f"{kind}/{item_id}/{budget_id}/seed-{seed}"


def build_formal_matrix(
    protocol: EvaluationProtocol,
    dataset: LoadedDataset,
    *,
    dependency_commits: Mapping[str, str],
    dependencies_integrated: bool,
    provider_profiles: tuple[str, ...],
    provider_ready: bool,
    budget_approved: bool,
    policy_artifact_ready: bool = False,
    policy_artifact_sha256: str | None = None,
    generation_id: str | None = None,
    generation_sha256: str | None = None,
) -> MatrixSchedule:
    """Emit every frozen suite, retaining blocked groups instead of shrinking the matrix."""
    blockers: list[str] = []
    if not dataset.ready:
        blockers.extend(
            f"BLOCKED_INPUTS: {item}" for item in dataset.manifest.blockers
        )
        if not dataset.manifest.blockers:
            blockers.append("BLOCKED_INPUTS: the prepared Gold dataset is not FROZEN")

    if not generation_id or not generation_sha256:
        blockers.append("BLOCKED_GENERATION: actual generation ID and SHA-256 are required")
    elif not valid_generation_binding(generation_id, generation_sha256):
        blockers.append("BLOCKED_GENERATION: actual generation ID or SHA-256 is invalid")

    for name, expected_sha in REQUIRED_DEPENDENCIES.items():
        actual_sha = dependency_commits.get(name)
        if not dependencies_integrated or actual_sha != expected_sha:
            blockers.append(
                f"BLOCKED_DEPENDENCY: {name} expected {expected_sha}, observed {actual_sha or 'missing'}"
            )
    if not provider_ready:
        blockers.append("BLOCKED_PROVIDER: one or more frozen provider capabilities are unavailable")
    if not provider_profiles:
        blockers.append("BLOCKED_PROVIDER: no provider profile revisions were recorded")
    if not budget_approved:
        blockers.append("BLOCKED_BUDGET: approved experiment budget is not recorded")
    if not policy_artifact_ready or not policy_artifact_sha256:
        blockers.append("BLOCKED_POLICY: a frozen train/dev-selected policy artifact is unavailable")

    ready = not blockers
    suite_status = SuiteStatus.planned if ready else SuiteStatus.blocked
    suite_blockers = tuple(blockers)
    cache_states = tuple(protocol.metric_plan.latency_cache_states)
    runs_per_case = len(cache_states)
    suites: list[ExperimentSuite] = []

    for system in protocol.systems:
        for budget in protocol.budgets:
            for seed in protocol.seeds:
                suites.append(
                    ExperimentSuite(
                        suite_id=_suite_id("system", system.id, budget.id, seed),
                        kind="system",
                        system_id=system.id,
                        budget_id=budget.id,
                        seed=seed,
                        ablation_id=None,
                        status=suite_status,
                        blockers=suite_blockers,
                        cache_states=cache_states,
                        expected_query_runs=MINIMUM_TEST_CASES * runs_per_case,
                    )
                )
    for ablation in protocol.ablations:
        for seed in protocol.seeds:
            suites.append(
                ExperimentSuite(
                    suite_id=_suite_id("ablation", ablation.id, "standard", seed),
                    kind="ablation",
                    system_id="segr",
                    budget_id="standard",
                    seed=seed,
                    ablation_id=ablation.id,
                    status=suite_status,
                    blockers=suite_blockers,
                    cache_states=cache_states,
                    expected_query_runs=MINIMUM_TEST_CASES * runs_per_case,
                )
            )

    if len(suites) != protocol.formal_suite_count:
        raise ValueError("the generated matrix does not match the frozen formal suite count")
    if dataset.ready:
        assert dataset.summary is not None
        test_count = dataset.summary.split_counts.get("test", 0)
        if test_count < MINIMUM_TEST_CASES:
            raise ValueError("a FROZEN dataset is below the frozen minimum test size")
        suites = [
            ExperimentSuite(
                **{
                    **suite.to_dict(),
                    "status": suite.status,
                    "expected_query_runs": test_count * runs_per_case,
                }
            )
            for suite in suites
        ]
        expected_quality_query_runs = len(suites) * test_count
        expected_query_runs = expected_quality_query_runs * runs_per_case
        lower_bound = False
    else:
        expected_quality_query_runs = len(suites) * MINIMUM_TEST_CASES
        expected_query_runs = expected_quality_query_runs * runs_per_case
        lower_bound = True

    status = SuiteStatus.planned if ready else SuiteStatus.blocked
    return MatrixSchedule(
        protocol_id=protocol.protocol_id,
        protocol_sha256=_stable_hash(protocol.model_dump(mode="json")),
        dataset_sha256=dataset.manifest.cases_sha256
        or _stable_hash(dataset.manifest.model_dump(mode="json")),
        dataset_status=dataset.manifest.status.value,
        generation_id=generation_id,
        generation_sha256=generation_sha256,
        dependency_commits=dict(dependency_commits),
        provider_profiles=tuple(sorted(set(provider_profiles))),
        budget_approved=budget_approved,
        policy_artifact_ready=policy_artifact_ready,
        policy_artifact_sha256=policy_artifact_sha256,
        status=status,
        expected_query_runs=expected_query_runs,
        expected_quality_query_runs=expected_quality_query_runs,
        expected_runs_are_lower_bound=lower_bound,
        suites=tuple(suites),
        blockers=tuple(blockers),
    )
