from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest
from test_final_mapping import _fixture

from knowledge_weaver.agent.weights import ACTION_FAMILIES
from knowledge_weaver.evaluation.contracts import RidgePolicyWeights
from knowledge_weaver.evaluation.dataset import load_dataset
from knowledge_weaver.evaluation.protocol import load_protocol
from knowledge_weaver.evaluation.redaction import canonical_json_line
from knowledge_weaver.evaluation.reporting import summarize_query_runs
from knowledge_weaver.evaluation.runner import _seeded_case_order
from knowledge_weaver.evaluation.runs import (
    AttemptOutcome,
    QueryMeasurements,
    QueryRunRecord,
    run_query_with_retries,
)
from knowledge_weaver.evaluation.scheduling import (
    REQUIRED_DEPENDENCIES,
    ExperimentSuite,
    SuiteStatus,
    build_formal_matrix,
)
from knowledge_weaver.evaluation.statistics import (
    ParetoPoint,
    paired_source_family_bootstrap,
    pareto_frontier,
)
from knowledge_weaver.evaluation.training import (
    ActionExplorationRecord,
    DevPolicyOutcome,
    PolicyFitContext,
    fit_segr_policy,
    select_stratified_actions,
    validate_exploration_records,
    write_policy_artifact,
)
from knowledge_weaver.models.contracts import Action, ActionTool

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def _uuid(index: int) -> str:
    return f"00000000-0000-4000-8000-{index:012d}"


def _record(
    case_id: str,
    index: int,
    *,
    split: str = "train",
    action_family: str = "bm25",
) -> ActionExplorationRecord:
    state_hash = hashlib.sha256(f"{case_id}:{index // 4}".encode()).hexdigest()
    family_index = ACTION_FAMILIES.index(action_family)
    features = [
        1.0,
        index / 30.0,
        0.2,
        0.1,
        0.0,
        0.3,
        0.5,
        0.4,
        0.0,
        0.0,
        family_index / 10.0,
        0.7,
    ]
    return ActionExplorationRecord(
        case_id=case_id,
        split=split,  # type: ignore[arg-type]
        source_family_ids=[_uuid(100 + int(case_id[-1]))],
        trajectory_id=f"trajectory-{case_id}",
        state_index=index % 6,
        action_id=f"action-{case_id}-{index}",
        action_family=action_family,  # type: ignore[arg-type]
        top_k=(5, 10, 20)[index % 3],
        hops=1 if action_family == "graph" else None,
        starting_state_sha256=state_hash,
        branch_initial_state_sha256=state_hash,
        features=features,
        gold_coverage_before=0.2,
        gold_coverage_after=0.3 + (index % 4) / 20.0,
        source_coverage_before=0.1,
        source_coverage_after=0.2,
        resolved_conflict_fraction_before=0.0,
        resolved_conflict_fraction_after=0.0,
        estimated_tokens=120 + index,
        observed_tokens=100 + index,
        count_kind="exact",
        observed_cost_usd="0.0001",
        observed_cost_cny=None,
        elapsed_ms=20 + index,
        reviewer_id="fixture-reviewer",
        reviewed_at=NOW,
        annotation_sha256=hashlib.sha256(f"annotation-{case_id}-{index}".encode()).hexdigest(),
    )


def _dev_outcomes() -> list[DevPolicyOutcome]:
    outcomes: list[DevPolicyOutcome] = []
    for case_number in range(3):
        case_id = f"dev{case_number}"
        for seed in (17, 29, 43):
            for lambda_cost in (0.1, 0.3, 0.6):
                for threshold in (0.0, 0.05, 0.1):
                    outcomes.append(
                        DevPolicyOutcome(
                            case_id=case_id,
                            split="dev",
                            source_family_ids=[_uuid(200 + case_number)],
                            seed=seed,
                            lambda_cost=lambda_cost,
                            stop_threshold=threshold,
                            evidence_recall=0.8,
                            answer_correctness=2,
                            total_tokens=int(lambda_cost * 1000),
                            end_to_end_latency_ms=int(threshold * 10_000),
                            human_reviewer_id="fixture-reviewer",
                            human_reviewed_at=NOW,
                            annotation_sha256=hashlib.sha256(
                                f"dev-{case_id}-{seed}-{lambda_cost}-{threshold}".encode()
                            ).hexdigest(),
                        )
                    )
    return outcomes


def test_blocked_gold_keeps_all_111_suites_and_minimum_run_count() -> None:
    root = Path(__file__).resolve().parents[2]
    protocol = load_protocol(root / "benchmarks/configs/b07_protocol_v1.json")
    dataset = load_dataset(root)
    schedule = build_formal_matrix(
        protocol,
        dataset,
        dependency_commits=REQUIRED_DEPENDENCIES,
        dependencies_integrated=True,
        provider_profiles=("deepseek", "qwen-embedding"),
        provider_ready=False,
        budget_approved=False,
        policy_artifact_ready=False,
    )
    assert schedule.status is SuiteStatus.blocked
    assert len(schedule.suites) == 111
    assert schedule.expected_query_runs == 8880
    assert schedule.expected_quality_query_runs == 4440
    assert schedule.expected_runs_are_lower_bound
    assert len(schedule.missing_runs) == 111
    assert all(item.status is SuiteStatus.blocked for item in schedule.suites)
    assert any(item.startswith("BLOCKED_INPUTS:") for item in schedule.blockers)
    assert any(item.startswith("BLOCKED_POLICY:") for item in schedule.blockers)


def test_train_only_fit_selects_on_dev_and_cannot_write_fixture_weights(tmp_path: Path) -> None:
    train_ids = {f"train{index}" for index in range(4)}
    dev_ids = {f"dev{index}" for index in range(3)}
    train = [
        _record(case_id, index)
        for case_id in sorted(train_ids)
        for index in range(6)
    ]
    dev_actions = [
        _record(case_id, index, split="dev")
        for case_id in sorted(dev_ids)
        for index in range(3)
    ]
    context = PolicyFitContext(
        dataset_status="SYNTHETIC_FIXTURE",
        dataset_sha256="a" * 64,
        manifest_sha256="b" * 64,
        train_case_ids=frozenset(train_ids),
        dev_case_ids=frozenset(dev_ids),
        synthetic_fixture=True,
    )
    result = fit_segr_policy(train, dev_actions, _dev_outcomes(), context=context)
    assert not result.formal_ready
    assert result.synthetic_fixture
    assert result.artifact.policy_mode == "mixed"
    assert result.artifact.action_sample_counts["bm25"] == 24
    assert set(result.artifact.prior_action_families) == set(ACTION_FAMILIES) - {"bm25"}
    assert result.parameter_selection.lambda_cost == 0.1
    assert result.parameter_selection.stop_threshold == 0.0
    assert RidgePolicyWeights.model_validate(result.artifact.model_dump(mode="json"))
    with pytest.raises(ValueError, match="synthetic or non-frozen"):
        write_policy_artifact(result, repository_root=tmp_path)


def test_action_log_rejects_test_rows_and_stratified_plan_is_bounded() -> None:
    test_row = _record("test0", 0, split="test")
    with pytest.raises(ValueError, match="another split"):
        validate_exploration_records([test_row], expected_split="train")

    actions = []
    for index, family in enumerate(ACTION_FAMILIES):
        for variant, top_k in enumerate((5, 10, 20)[:2]):
            actions.append(
                Action(
                    id=_uuid(index * 2 + variant + 300),
                    tool=ActionTool(family),
                    top_k=top_k,
                    hops=variant + 1 if family == "graph" else None,
                )
            )
    selected = select_stratified_actions(actions)
    assert len(selected) == 8
    assert {action.tool.value for action in selected} == set(ACTION_FAMILIES)


def test_family_bootstrap_is_deterministic_and_pareto_is_cost_aware() -> None:
    left = {f"case-{index}": 0.8 for index in range(6)}
    right = {f"case-{index}": 0.5 for index in range(6)}
    families = {
        "case-0": ["f1"],
        "case-1": ["f1"],
        "case-2": ["f2"],
        "case-3": ["f3"],
        "case-4": ["f4"],
        "case-5": ["f4", "f5"],
    }
    first = paired_source_family_bootstrap(left, right, families, resamples=250, seed=29)
    second = paired_source_family_bootstrap(left, right, families, resamples=250, seed=29)
    assert first == second
    assert first.mean_difference == pytest.approx(0.3)
    assert first.family_cluster_count == 4
    assert not first.confirmatory_family_count

    frontier = pareto_frontier(
        [
            ParetoPoint("A", quality=0.7, total_tokens=100, latency_ms=100),
            ParetoPoint("B", quality=0.8, total_tokens=200, latency_ms=200),
            ParetoPoint("C", quality=0.6, total_tokens=300, latency_ms=300),
            ParetoPoint("D", quality=0.7, total_tokens=100, latency_ms=100, currency_cost=3.0),
            ParetoPoint("E", quality=0.7, total_tokens=100, latency_ms=100, currency_cost=2.0),
        ]
    )
    assert {point.system_id for point in frontier} == {"A", "B", "E"}


def test_formal_quality_cannot_skip_required_answerable_human_review() -> None:
    record = _drive_immediate(
        run_query_with_retries(
            _SyntheticExecutor(fail_until=2),
            _fixture()[0],
            _suite(),
            formal_dataset=False,
            retry_delay_seconds=0,
        )
    )
    payload = record.model_dump(mode="json")
    payload.update(
        {
            "provider_kind": "real",
            "synthetic_fixture": False,
            "formal_quality_eligible": True,
            "split": "test",
        }
    )
    for attempt in payload["attempts"]:
        attempt["provider_kind"] = "real"
    with pytest.raises(ValueError, match="required human review"):
        QueryRunRecord.model_validate(payload)


def test_suite_case_order_is_seeded_and_repeatable() -> None:
    cases = tuple(range(40))
    assert _seeded_case_order(cases, 17) == _seeded_case_order(cases, 17)
    assert _seeded_case_order(cases, 17) != _seeded_case_order(cases, 29)


def _drive_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("test coroutine unexpectedly yielded to an event loop")


class _SyntheticExecutor:
    provider_kind = "synthetic"

    def __init__(self, *, fail_until: int = 2, permanent: bool = False) -> None:
        self.calls = 0
        self.fail_until = fail_until
        self.permanent = permanent

    async def execute_query(
        self,
        case,
        suite,
        *,
        attempt: int,
        cache_state: Literal["cold", "warm"] = "cold",
    ) -> AttemptOutcome:
        self.calls += 1
        if attempt <= self.fail_until:
            return AttemptOutcome(
                attempt=attempt,
                status="failed",
                retryable=not self.permanent,
                provider_kind="synthetic",
                provider_profile="fixture",
                model_revision="synthetic-v1",
                failure_code="RATE_LIMITED" if not self.permanent else "CONFIG_INVALID",
                input_tokens=2,
                output_tokens=1,
                tool_calls=1,
                llm_calls=1,
                count_kind="estimated",
                elapsed_ms=10,
                cache_state=cache_state,
            )
        return AttemptOutcome(
            attempt=attempt,
            status="complete",
            provider_kind="synthetic",
            provider_profile="fixture",
            model_revision="synthetic-v1",
            input_tokens=3,
            output_tokens=2,
            tool_calls=1,
            llm_calls=1,
            steps=1,
            count_kind="exact",
            elapsed_ms=15,
            strategy_latency_ms=4,
            cache_state=cache_state,
            measurements=QueryMeasurements(evidence_recall=0.9),
        )


def _suite() -> ExperimentSuite:
    return ExperimentSuite(
        suite_id="system/bm25/low/seed-17",
        kind="system",
        system_id="bm25",
        budget_id="low",
        seed=17,
        ablation_id=None,
        status=SuiteStatus.planned,
        expected_query_runs=1,
    )


def test_runner_retains_retry_usage_and_marks_fixture_quality_ineligible() -> None:
    case = _fixture()[0]
    executor = _SyntheticExecutor(fail_until=2)
    record = _drive_immediate(
        run_query_with_retries(
            executor,
            case,
            _suite(),
            formal_dataset=False,
            retry_delay_seconds=0,
        )
    )
    assert executor.calls == 3
    assert record.status == "complete"
    assert record.total_tokens == 11
    assert record.tool_calls == 3
    assert record.count_kind == "estimated"
    assert record.synthetic_fixture
    assert not record.formal_quality_eligible
    summary = summarize_query_runs([record])["suite_summaries"][0]
    assert summary["mean_evidence_recall"] is None
    assert summary["total_tokens"] == 11


def test_runner_does_not_retry_configuration_failures() -> None:
    case = _fixture()[0]
    executor = _SyntheticExecutor(fail_until=1, permanent=True)
    record = _drive_immediate(
        run_query_with_retries(
            executor,
            case,
            _suite(),
            formal_dataset=False,
            retry_delay_seconds=0,
        )
    )
    assert executor.calls == 1
    assert record.status == "failed"
    assert record.attempts[0].failure_code == "CONFIG_INVALID"
    assert record.input_tokens == 2


def test_secret_redaction_covers_fields_bearer_url_password_and_key() -> None:
    line = canonical_json_line(
        {
            "api_key": "sk-12345678901234567890",
            "message": "Authorization: Bearer abcdef123456 https://user:password@host/path",
            "note": "sk-abcdefghijklmno",
        }
    )
    assert "12345678901234567890" not in line
    assert "abcdef123456" not in line
    assert "password@" not in line
    assert "sk-abcdefghijklmno" not in line
    assert "[REDACTED]" in line
