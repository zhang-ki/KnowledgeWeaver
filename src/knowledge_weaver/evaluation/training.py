"""Train-only bounded exploration logs and SEGR ridge artifact fitting."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from knowledge_weaver.agent.weights import (
    ACTION_FAMILIES,
    FEATURE_ORDER,
    ActionCostMedian,
    SegrPolicyWeights,
    feature_schema_fingerprint,
    policy_fingerprint,
)
from knowledge_weaver.evaluation.dataset import LoadedDataset
from knowledge_weaver.models.contracts import Action

SHA256_PATTERN = r"^[0-9a-f]{64}$"
RIDGE_GRID = (0.1, 1.0, 10.0)
LAMBDA_COST_GRID = (0.1, 0.3, 0.6)
STOP_THRESHOLD_GRID = (0.0, 0.05, 0.1)
FIXED_SEEDS = (17, 29, 43)
MAX_TRAIN_STATES_PER_CASE = 6
MAX_BRANCHES_PER_STATE = 8
CONSERVATIVE_FALLBACK_TOKENS = 16_000
CONSERVATIVE_FALLBACK_MS = 90_000
JsonModelT = TypeVar("JsonModelT", bound=BaseModel)


class TrainingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ActionExplorationRecord(TrainingModel):
    case_id: str
    split: Literal["train", "dev", "test"]
    source_family_ids: list[str] = Field(min_length=1)
    trajectory_id: str = Field(min_length=1)
    state_index: int = Field(ge=0, lt=MAX_TRAIN_STATES_PER_CASE)
    action_id: str = Field(min_length=1)
    action_family: Literal["bm25", "dense", "symbol", "entity", "graph", "rerank"]
    top_k: Literal[5, 10, 20]
    hops: Literal[1, 2] | None = None
    starting_state_sha256: str = Field(pattern=SHA256_PATTERN)
    branch_initial_state_sha256: str = Field(pattern=SHA256_PATTERN)
    features: list[float] = Field(min_length=12, max_length=12)
    gold_coverage_before: float = Field(ge=0.0, le=1.0)
    gold_coverage_after: float = Field(ge=0.0, le=1.0)
    source_coverage_before: float = Field(ge=0.0, le=1.0)
    source_coverage_after: float = Field(ge=0.0, le=1.0)
    resolved_conflict_fraction_before: float = Field(ge=0.0, le=1.0)
    resolved_conflict_fraction_after: float = Field(ge=0.0, le=1.0)
    estimated_tokens: int = Field(gt=0)
    observed_tokens: int = Field(ge=0)
    count_kind: Literal["exact", "estimated", "unavailable"]
    observed_cost_usd: Decimal | None = Field(default=None, ge=Decimal(0))
    observed_cost_cny: Decimal | None = Field(default=None, ge=Decimal(0))
    elapsed_ms: int = Field(gt=0)
    reviewer_id: str = Field(min_length=1)
    reviewed_at: datetime
    annotation_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("reviewed_at")
    @classmethod
    def reviewed_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reviewed_at must include a timezone")
        return value

    @field_validator("features")
    @classmethod
    def features_are_finite(cls, value: list[float]) -> list[float]:
        if any(not math.isfinite(item) for item in value):
            raise ValueError("feature values must be finite")
        if value[0] != 1.0:
            raise ValueError("the frozen feature vector must start with bias=1.0")
        return value

    @model_validator(mode="after")
    def action_shape_matches(self) -> ActionExplorationRecord:
        if (self.action_family == "graph") != (self.hops is not None):
            raise ValueError("only graph action records require hops")
        if self.branch_initial_state_sha256 != self.starting_state_sha256:
            raise ValueError("each action branch must start from the same untouched state")
        if len(set(self.source_family_ids)) != len(self.source_family_ids):
            raise ValueError("source_family_ids must be unique")
        return self

    @property
    def observed_gain(self) -> float:
        gain = (
            0.7 * (self.gold_coverage_after - self.gold_coverage_before)
            + 0.2 * (self.source_coverage_after - self.source_coverage_before)
            + 0.1
            * (
                self.resolved_conflict_fraction_after
                - self.resolved_conflict_fraction_before
            )
        )
        return max(-1.0, min(1.0, gain))


class DevPolicyOutcome(TrainingModel):
    case_id: str
    split: Literal["dev"]
    source_family_ids: list[str] = Field(min_length=1)
    seed: int
    lambda_cost: float
    stop_threshold: float
    evidence_recall: float = Field(ge=0.0, le=1.0)
    answer_correctness: Literal[0, 1, 2]
    total_tokens: int = Field(ge=0)
    count_kind: Literal["exact", "estimated", "unavailable"] = "unavailable"
    currency_cost_usd: Decimal | None = Field(default=None, ge=Decimal(0))
    currency_cost_cny: Decimal | None = Field(default=None, ge=Decimal(0))
    end_to_end_latency_ms: int = Field(ge=0)
    human_reviewer_id: str = Field(min_length=1)
    human_reviewed_at: datetime
    annotation_sha256: str = Field(pattern=SHA256_PATTERN)
    status: Literal["complete"] = "complete"

    @field_validator("human_reviewed_at")
    @classmethod
    def review_time_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("human_reviewed_at must include a timezone")
        return value

    @model_validator(mode="after")
    def frozen_parameter_values(self) -> DevPolicyOutcome:
        if self.lambda_cost not in LAMBDA_COST_GRID:
            raise ValueError("lambda_cost must be selected from the frozen grid")
        if self.stop_threshold not in STOP_THRESHOLD_GRID:
            raise ValueError("stop_threshold must be selected from the frozen grid")
        if len(set(self.source_family_ids)) != len(self.source_family_ids):
            raise ValueError("source_family_ids must be unique")
        return self


@dataclass(frozen=True)
class PolicyFitContext:
    dataset_status: str
    dataset_sha256: str
    manifest_sha256: str
    train_case_ids: frozenset[str]
    dev_case_ids: frozenset[str]
    synthetic_fixture: bool = False


@dataclass(frozen=True)
class ParameterSelection:
    ridge_lambda: float
    lambda_cost: float
    stop_threshold: float
    dev_quality: float | None
    dev_mean_tokens: float | None
    dev_mean_latency_ms: float | None
    selection_reason: str


@dataclass(frozen=True)
class PolicyFitResult:
    artifact: SegrPolicyWeights
    formal_ready: bool
    synthetic_fixture: bool
    parameter_selection: ParameterSelection
    train_action_counts: dict[str, int]
    train_action_log_sha256: str
    dev_action_log_sha256: str
    dev_outcomes_sha256: str
    training_observed_tokens: int
    training_cost_usd: Decimal | None
    training_cost_cny: Decimal | None
    training_elapsed_ms: int
    training_count_kind: Literal["exact", "estimated", "unavailable"]
    dev_total_tokens: int
    dev_cost_usd: Decimal | None
    dev_cost_cny: Decimal | None
    dev_elapsed_ms: int
    dev_count_kind: Literal["exact", "estimated", "unavailable"]
    ridge_dev_mse: dict[float, float]


def policy_training_summary(result: PolicyFitResult) -> dict[str, Any]:
    """Return cost-separated train/dev metadata suitable for the experiment manifest."""
    return {
        "formal_ready": result.formal_ready,
        "synthetic_fixture": result.synthetic_fixture,
        "policy_mode": result.artifact.policy_mode,
        "policy_fingerprint": result.artifact.policy_fingerprint,
        "train_action_log_sha256": result.train_action_log_sha256,
        "dev_action_log_sha256": result.dev_action_log_sha256,
        "dev_outcomes_sha256": result.dev_outcomes_sha256,
        "train_action_counts": dict(result.train_action_counts),
        "training_tokens": result.training_observed_tokens,
        "training_count_kind": result.training_count_kind,
        "training_cost_usd": (
            str(result.training_cost_usd) if result.training_cost_usd is not None else None
        ),
        "training_cost_cny": (
            str(result.training_cost_cny) if result.training_cost_cny is not None else None
        ),
        "training_elapsed_ms": result.training_elapsed_ms,
        "dev_tokens": result.dev_total_tokens,
        "dev_count_kind": result.dev_count_kind,
        "dev_cost_usd": str(result.dev_cost_usd) if result.dev_cost_usd is not None else None,
        "dev_cost_cny": str(result.dev_cost_cny) if result.dev_cost_cny is not None else None,
        "dev_elapsed_ms": result.dev_elapsed_ms,
        "selected_ridge_lambda": result.parameter_selection.ridge_lambda,
        "selected_lambda_cost": result.parameter_selection.lambda_cost,
        "selected_stop_threshold": result.parameter_selection.stop_threshold,
    }


def build_policy_fit_context(
    repository_root: Path,
    dataset: LoadedDataset,
    *,
    manifest_relative_path: str = "benchmarks/manifests/dataset.json",
) -> PolicyFitContext:
    """Derive split membership and hashes from a validated FROZEN Gold dataset."""
    if not dataset.ready or dataset.manifest.cases_sha256 is None:
        raise ValueError("policy fitting requires a validated FROZEN Gold dataset")
    if ":" in manifest_relative_path or "\\" in manifest_relative_path:
        raise ValueError("dataset manifest path must use relative POSIX syntax")
    root = repository_root.resolve()
    manifest_path = (root / Path(*manifest_relative_path.split("/"))).resolve(strict=True)
    try:
        manifest_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("dataset manifest path escapes the repository root") from exc
    manifest_bytes = manifest_path.read_bytes()
    train_cases = sorted(
        (item for item in dataset.cases if item.case.split.value == "train"),
        key=lambda item: item.case.id,
    )
    dev_cases = tuple(item for item in dataset.cases if item.case.split.value == "dev")
    if not train_cases or not dev_cases:
        raise ValueError("a frozen dataset must include train and dev cases")
    train_case_hash = _hash_jsonl(train_cases)
    return PolicyFitContext(
        dataset_status=dataset.manifest.status.value,
        dataset_sha256=train_case_hash,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        train_case_ids=frozenset(item.case.id for item in train_cases),
        dev_case_ids=frozenset(item.case.id for item in dev_cases),
        synthetic_fixture=False,
    )


def select_stratified_actions(actions: Sequence[Action], *, limit: int = MAX_BRANCHES_PER_STATE) -> tuple[Action, ...]:
    """Select at most eight stable actions, first covering each available action family."""
    if not 1 <= limit <= MAX_BRANCHES_PER_STATE:
        raise ValueError("exploration branch limit must be between 1 and 8")
    family_order = {family: index for index, family in enumerate(ACTION_FAMILIES)}
    grouped: dict[str, list[Action]] = defaultdict(list)
    action_ids: set[str] = set()
    for action in actions:
        if action.id in action_ids:
            raise ValueError("candidate action IDs must be unique")
        action_ids.add(action.id)
        grouped[action.tool.value].append(action)
    for candidates in grouped.values():
        candidates.sort(key=lambda item: (item.top_k, item.hops or 0, item.id))

    selected: list[Action] = []
    seen: set[str] = set()
    for family in ACTION_FAMILIES:
        candidates = grouped.get(family, [])
        if candidates and len(selected) < limit:
            selected.append(candidates[0])
            seen.add(candidates[0].id)

    remaining = sorted(
        (action for action in actions if action.id not in seen),
        key=lambda item: (
            item.top_k,
            item.hops or 0,
            family_order[item.tool.value],
            item.id,
        ),
    )
    selected.extend(remaining[: max(0, limit - len(selected))])
    return tuple(selected)


def validate_exploration_records(
    records: Sequence[ActionExplorationRecord], *, expected_split: Literal["train", "dev"]
) -> None:
    if any(record.split != expected_split for record in records):
        raise ValueError(f"{expected_split} pipeline cannot consume another split")
    grouped: dict[tuple[str, str], list[ActionExplorationRecord]] = defaultdict(list)
    case_states: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for record in records:
        grouped[(record.case_id, record.trajectory_id)].append(record)
        case_states[record.case_id].add((record.trajectory_id, record.state_index))
    if any(len(states) > MAX_TRAIN_STATES_PER_CASE for states in case_states.values()):
        raise ValueError("each query may contribute at most six explored states")
    for rows in grouped.values():
        by_state: dict[int, list[ActionExplorationRecord]] = defaultdict(list)
        for row in rows:
            by_state[row.state_index].append(row)
        for state_rows in by_state.values():
            if len(state_rows) > MAX_BRANCHES_PER_STATE:
                raise ValueError("each state may have at most eight action branches")
            if len({row.action_id for row in state_rows}) != len(state_rows):
                raise ValueError("an action may appear only once per explored state")
            if len({row.starting_state_sha256 for row in state_rows}) != 1:
                raise ValueError("all candidates in one state must share the same starting state")


def _hash_jsonl(records: Sequence[JsonModelT]) -> str:
    rows = [
        json.dumps(item.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for item in records
    ]
    payload = "\n".join(rows) + ("\n" if rows else "")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    size = len(vector)
    augmented = [matrix[row][:] + [vector[row]] for row in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("ridge normal matrix is singular")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor:
                augmented[row] = [
                    value - factor * pivot_value
                    for value, pivot_value in zip(augmented[row], augmented[column], strict=True)
                ]
    return [augmented[row][-1] for row in range(size)]


def _fit_coefficients(rows: Sequence[ActionExplorationRecord], ridge_lambda: float) -> list[float]:
    dimensions = len(FEATURE_ORDER)
    gram = [[0.0] * dimensions for _ in range(dimensions)]
    target = [0.0] * dimensions
    for row in rows:
        for i, left in enumerate(row.features):
            target[i] += left * row.observed_gain
            for j, right in enumerate(row.features):
                gram[i][j] += left * right
    for index in range(dimensions):
        gram[index][index] += ridge_lambda
    return _solve(gram, target)


def _predict(coefficients: Sequence[float], features: Sequence[float]) -> float:
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(coefficients, features, strict=True))))


def _round_up_median(values: Sequence[int]) -> int:
    if not values:
        raise ValueError("median requires at least one observation")
    return math.ceil(statistics.median(values))


def _observed_token_count(row: ActionExplorationRecord) -> int:
    return row.observed_tokens if row.count_kind != "unavailable" and row.observed_tokens > 0 else row.estimated_tokens


def _sum_known_decimals(values: Sequence[Decimal | None]) -> Decimal | None:
    if not values or any(value is None for value in values):
        return None
    total = Decimal(0)
    for value in values:
        assert value is not None
        total += value
    return total


def _select_ridge_lambda(
    train_rows: Sequence[ActionExplorationRecord],
    dev_rows: Sequence[ActionExplorationRecord],
) -> tuple[float, dict[float, float], dict[str, list[float]]]:
    train_by_family: dict[str, list[ActionExplorationRecord]] = defaultdict(list)
    dev_by_family: dict[str, list[ActionExplorationRecord]] = defaultdict(list)
    for row in train_rows:
        train_by_family[row.action_family].append(row)
    for row in dev_rows:
        dev_by_family[row.action_family].append(row)
    fitted_families = {
        family for family, rows in train_by_family.items() if len(rows) >= 24
    }
    if not fitted_families:
        return 1.0, {}, {}

    mse_by_lambda: dict[float, float] = {}
    coefficients_by_lambda: dict[float, dict[str, list[float]]] = {}
    for ridge_lambda in RIDGE_GRID:
        family_coefficients = {
            family: _fit_coefficients(train_by_family[family], ridge_lambda)
            for family in sorted(fitted_families)
        }
        validation = [
            (row.observed_gain - _predict(family_coefficients[row.action_family], row.features)) ** 2
            for row in dev_rows
            if row.action_family in family_coefficients
        ]
        if not validation:
            raise ValueError("dev action records must cover at least one fitted train action family")
        mse_by_lambda[ridge_lambda] = sum(validation) / len(validation)
        coefficients_by_lambda[ridge_lambda] = family_coefficients
    selected = min(RIDGE_GRID, key=lambda value: (mse_by_lambda[value], value))
    return selected, mse_by_lambda, coefficients_by_lambda[selected]


def select_dev_parameters(
    outcomes: Sequence[DevPolicyOutcome], *, selected_ridge_lambda: float
) -> tuple[float, float, float, float, float, float]:
    """Select lambda_cost/threshold only from a complete, paired dev grid."""
    if not outcomes:
        raise ValueError("dev rollout outcomes are required to select policy parameters")
    if selected_ridge_lambda not in RIDGE_GRID:
        raise ValueError("ridge_lambda must come from the frozen grid")
    if len({(row.case_id, row.seed, row.lambda_cost, row.stop_threshold) for row in outcomes}) != len(
        outcomes
    ):
        raise ValueError("dev parameter grid contains duplicate case/seed observations")
    combinations = {
        (row.lambda_cost, row.stop_threshold) for row in outcomes
    }
    expected = {
        (lambda_cost, threshold)
        for lambda_cost in LAMBDA_COST_GRID
        for threshold in STOP_THRESHOLD_GRID
    }
    if combinations != expected:
        raise ValueError("dev runs must cover the full frozen lambda_cost/threshold grid")
    paired_keys = {(row.case_id, row.seed) for row in outcomes}
    if {seed for _case_id, seed in paired_keys} != set(FIXED_SEEDS):
        raise ValueError("dev tuning must include the three frozen seeds 17, 29, and 43")
    if any(
        {seed for case_id, seed in paired_keys if case_id == case}
        != set(FIXED_SEEDS)
        for case in {case_id for case_id, _seed in paired_keys}
    ):
        raise ValueError("every dev case must be evaluated with all three frozen seeds")
    for combination in expected:
        keys = {
            (row.case_id, row.seed)
            for row in outcomes
            if (row.lambda_cost, row.stop_threshold) == combination
        }
        if keys != paired_keys:
            raise ValueError("all dev parameter candidates must use the same cases and seeds")
    family_set_by_case: dict[str, tuple[str, ...]] = {}
    for row in outcomes:
        prior = family_set_by_case.setdefault(row.case_id, tuple(sorted(row.source_family_ids)))
        if prior != tuple(sorted(row.source_family_ids)):
            raise ValueError("a dev case cannot change source-family attribution across candidates")

    candidate_rows: list[tuple[tuple[float, float, float, float, float], float, float]] = []
    for lambda_cost, threshold in sorted(expected):
        rows = [
            row
            for row in outcomes
            if row.lambda_cost == lambda_cost and row.stop_threshold == threshold
        ]
        quality = sum(
            (row.evidence_recall + row.answer_correctness / 2.0) / 2.0 for row in rows
        ) / len(rows)
        mean_tokens = sum(row.total_tokens for row in rows) / len(rows)
        mean_latency = sum(row.end_to_end_latency_ms for row in rows) / len(rows)
        # Quality is primary; lower observed token and latency use break exact ties.
        ranking = (quality, -mean_tokens, -mean_latency, -lambda_cost, -threshold)
        candidate_rows.append((ranking, lambda_cost, threshold))
    _ranking, selected_cost, selected_threshold = max(candidate_rows)
    selected_rows = [
        row
        for row in outcomes
        if row.lambda_cost == selected_cost and row.stop_threshold == selected_threshold
    ]
    quality = sum(
        (row.evidence_recall + row.answer_correctness / 2.0) / 2.0 for row in selected_rows
    ) / len(selected_rows)
    mean_tokens = sum(row.total_tokens for row in selected_rows) / len(selected_rows)
    mean_latency = sum(row.end_to_end_latency_ms for row in selected_rows) / len(selected_rows)
    return selected_ridge_lambda, selected_cost, selected_threshold, quality, mean_tokens, mean_latency


def fit_segr_policy(
    train_records: Sequence[ActionExplorationRecord],
    dev_action_records: Sequence[ActionExplorationRecord],
    dev_outcomes: Sequence[DevPolicyOutcome],
    *,
    context: PolicyFitContext,
) -> PolicyFitResult:
    """Fit B06-compatible weights from train rows and select settings on dev only."""
    validate_exploration_records(train_records, expected_split="train")
    validate_exploration_records(dev_action_records, expected_split="dev")
    if not context.train_case_ids or not context.dev_case_ids:
        raise ValueError("the fitted policy requires frozen train and dev case IDs")
    if any(row.case_id not in context.train_case_ids for row in train_records):
        raise ValueError("training action rows must refer only to train split cases")
    if any(row.case_id not in context.dev_case_ids for row in dev_action_records):
        raise ValueError("dev action rows must refer only to dev split cases")
    if any(row.case_id not in context.dev_case_ids for row in dev_outcomes):
        raise ValueError("policy parameter selection may use dev split cases only")
    if context.synthetic_fixture and context.dataset_status != "SYNTHETIC_FIXTURE":
        raise ValueError("synthetic fixtures must carry explicit synthetic dataset status")

    train_hash = _hash_jsonl(
        sorted(
            train_records,
            key=lambda row: (row.case_id, row.trajectory_id, row.state_index, row.action_id),
        )
    )
    dev_action_hash = _hash_jsonl(
        sorted(
            dev_action_records,
            key=lambda row: (row.case_id, row.trajectory_id, row.state_index, row.action_id),
        )
    )
    dev_outcome_hash = _hash_jsonl(
        sorted(
            dev_outcomes,
            key=lambda row: (row.lambda_cost, row.stop_threshold, row.case_id, row.seed),
        )
    )
    ridge_lambda, mse_by_lambda, coefficients = _select_ridge_lambda(
        train_records, dev_action_records
    )
    (
        ridge_lambda,
        lambda_cost,
        threshold,
        dev_quality,
        dev_tokens,
        dev_latency,
    ) = select_dev_parameters(dev_outcomes, selected_ridge_lambda=ridge_lambda)

    rows_by_family: dict[str, list[ActionExplorationRecord]] = defaultdict(list)
    for row in train_records:
        rows_by_family[row.action_family].append(row)
    counts = {family: len(rows_by_family[family]) for family in ACTION_FAMILIES}
    prior_families = [family for family in ACTION_FAMILIES if counts[family] < 24]
    global_tokens = max(
        (row.estimated_tokens for row in train_records), default=CONSERVATIVE_FALLBACK_TOKENS
    )
    global_ms = max((row.elapsed_ms for row in train_records), default=CONSERVATIVE_FALLBACK_MS)
    cost_medians: dict[str, ActionCostMedian] = {}
    for family in ACTION_FAMILIES:
        family_rows = rows_by_family[family]
        if family_rows:
            token_values = [_observed_token_count(row) for row in family_rows]
            time_values = [row.elapsed_ms for row in family_rows]
            if counts[family] < 24:
                token_values = [max(token_values)]
                time_values = [max(time_values)]
            estimated_tokens = _round_up_median(token_values)
            elapsed_ms = _round_up_median(time_values)
        else:
            estimated_tokens = global_tokens
            elapsed_ms = global_ms
        cost_medians[family] = ActionCostMedian(
            estimated_tokens=max(1, estimated_tokens), elapsed_ms=max(1, elapsed_ms)
        )

    learned_coefficients = {
        family: coefficients[family] for family in ACTION_FAMILIES if family in coefficients
    }
    policy_mode: Literal["learned", "mixed", "prior_only"] = (
        "prior_only"
        if not learned_coefficients
        else "mixed"
        if prior_families
        else "learned"
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm": "segr_ridge_v1",
        "policy_mode": policy_mode,
        "feature_order": list(FEATURE_ORDER),
        "action_coefficients": learned_coefficients,
        "action_sample_counts": counts,
        "prior_action_families": prior_families,
        "cost_medians": {key: value.model_dump(mode="json") for key, value in cost_medians.items()},
        "ridge_lambda": ridge_lambda,
        "lambda_cost": lambda_cost,
        "stop_threshold": threshold,
        "training_split": "train",
        "selection_split": "dev",
        "train_dataset_sha256": context.dataset_sha256,
        "train_manifest_sha256": context.manifest_sha256,
        "train_action_log_sha256": train_hash,
        "feature_schema_sha256": feature_schema_fingerprint(),
    }
    provisional_values = {**payload, "cost_medians": cost_medians}
    provisional = SegrPolicyWeights.model_construct(
        **provisional_values,
        policy_fingerprint="0" * 64,
    )
    payload["policy_fingerprint"] = policy_fingerprint(provisional)
    artifact = SegrPolicyWeights.model_validate(payload)
    formal_ready = context.dataset_status == "FROZEN" and not context.synthetic_fixture
    selection = ParameterSelection(
        ridge_lambda=ridge_lambda,
        lambda_cost=lambda_cost,
        stop_threshold=threshold,
        dev_quality=dev_quality,
        dev_mean_tokens=dev_tokens,
        dev_mean_latency_ms=dev_latency,
        selection_reason=(
            (
                "dev action-reward MSE selected ridge lambda; "
                if mse_by_lambda
                else "no train action family reached 24 samples; prior-only fallback used ridge lambda 1.0; "
            )
            + "complete paired dev outcomes selected lambda_cost and stop threshold; "
            "quality ties use lower token then latency use"
        ),
    )
    return PolicyFitResult(
        artifact=artifact,
        formal_ready=formal_ready,
        synthetic_fixture=context.synthetic_fixture,
        parameter_selection=selection,
        train_action_counts=counts,
        train_action_log_sha256=train_hash,
        dev_action_log_sha256=dev_action_hash,
        dev_outcomes_sha256=dev_outcome_hash,
        training_observed_tokens=sum(_observed_token_count(row) for row in train_records),
        training_cost_usd=_sum_known_decimals([row.observed_cost_usd for row in train_records]),
        training_cost_cny=_sum_known_decimals([row.observed_cost_cny for row in train_records]),
        training_elapsed_ms=sum(row.elapsed_ms for row in train_records),
        training_count_kind=(
            "exact"
            if train_records and all(row.count_kind == "exact" for row in train_records)
            else "estimated"
            if any(row.count_kind in {"exact", "estimated"} for row in train_records)
            else "unavailable"
        ),
        dev_total_tokens=sum(row.total_tokens for row in dev_outcomes),
        dev_cost_usd=_sum_known_decimals([row.currency_cost_usd for row in dev_outcomes]),
        dev_cost_cny=_sum_known_decimals([row.currency_cost_cny for row in dev_outcomes]),
        dev_elapsed_ms=sum(row.end_to_end_latency_ms for row in dev_outcomes),
        dev_count_kind=(
            "exact"
            if dev_outcomes and all(row.count_kind == "exact" for row in dev_outcomes)
            else "estimated"
            if any(row.count_kind in {"exact", "estimated"} for row in dev_outcomes)
            else "unavailable"
        ),
        ridge_dev_mse=mse_by_lambda,
    )


def write_policy_artifact(
    result: PolicyFitResult,
    *,
    repository_root: Path,
    relative_path: str = "benchmarks/policies/segr_policy_v1.json",
) -> Path:
    """Persist only a real, frozen-Gold, dev-selected artifact under benchmarks/policies."""
    if not result.formal_ready or result.synthetic_fixture:
        raise ValueError("synthetic or non-frozen policy weights cannot be written as formal artifacts")
    if ":" in relative_path or "\\" in relative_path:
        raise ValueError("policy artifact path must use normalized relative POSIX syntax")
    target_root = (repository_root / "benchmarks" / "policies").resolve()
    target = (repository_root / Path(*relative_path.split("/"))).resolve()
    try:
        target.relative_to(target_root)
    except ValueError as exc:
        raise ValueError("policy artifacts must stay under benchmarks/policies") from exc
    if target.suffix.casefold() != ".json":
        raise ValueError("policy artifacts must use .json")
    payload = json.dumps(
        result.artifact.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"
    artifact_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    selection_path = target.with_name(f"{target.stem}.selection.json")
    selection_payload = json.dumps(
        {
            "schema_version": 1,
            "evidence_kind": "real_frozen_gold",
            "artifact_sha256": artifact_hash,
            "train_action_log_sha256": result.train_action_log_sha256,
            "dev_action_log_sha256": result.dev_action_log_sha256,
            "dev_outcomes_sha256": result.dev_outcomes_sha256,
            "selection_split": "dev",
            "test_split_accessed": False,
            "ridge_lambda": result.parameter_selection.ridge_lambda,
            "lambda_cost": result.parameter_selection.lambda_cost,
            "stop_threshold": result.parameter_selection.stop_threshold,
            "dev_quality": result.parameter_selection.dev_quality,
            "dev_mean_tokens": result.parameter_selection.dev_mean_tokens,
            "dev_mean_latency_ms": result.parameter_selection.dev_mean_latency_ms,
            "train_action_counts": result.train_action_counts,
            "training_observed_tokens": result.training_observed_tokens,
            "training_cost_usd": (
                str(result.training_cost_usd) if result.training_cost_usd is not None else None
            ),
            "training_cost_cny": (
                str(result.training_cost_cny) if result.training_cost_cny is not None else None
            ),
            "training_elapsed_ms": result.training_elapsed_ms,
            "training_count_kind": result.training_count_kind,
            "dev_total_tokens": result.dev_total_tokens,
            "dev_cost_usd": str(result.dev_cost_usd) if result.dev_cost_usd is not None else None,
            "dev_cost_cny": str(result.dev_cost_cny) if result.dev_cost_cny is not None else None,
            "dev_elapsed_ms": result.dev_elapsed_ms,
            "dev_count_kind": result.dev_count_kind,
            "selection_reason": result.parameter_selection.selection_reason,
        },
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"
    if target.exists() or selection_path.exists():
        if (
            target.exists()
            and selection_path.exists()
            and target.read_text(encoding="utf-8") == payload
            and selection_path.read_text(encoding="utf-8") == selection_payload
        ):
            return target
        raise FileExistsError("refusing to replace an existing policy artifact")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8", newline="\n")
    selection_path.write_text(selection_payload, encoding="utf-8", newline="\n")
    return target
