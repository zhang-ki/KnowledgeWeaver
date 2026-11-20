from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FEATURE_ORDER = [
    "bias",
    "unresolved_ratio",
    "source_match",
    "support_coverage",
    "conflict_ratio",
    "independent_source_gap",
    "recent_gain_ema",
    "lexical_specificity",
    "exact_symbol",
    "seed_available",
    "action_family_count_norm",
    "remaining_budget_ratio",
]
ACTION_FAMILIES = ("bm25", "dense", "symbol", "entity", "graph", "rerank")
MAX_POLICY_BYTES = 2_000_000


class ActionCostMedian(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    estimated_tokens: int = Field(gt=0)
    elapsed_ms: int = Field(gt=0)


class SegrPolicyWeights(BaseModel):
    """Local mirror of B07's frozen artifact contract; agent never imports evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    algorithm: Literal["segr_ridge_v1"] = "segr_ridge_v1"
    policy_mode: Literal["learned", "mixed", "prior_only"]
    feature_order: list[str]
    action_coefficients: dict[str, list[float]]
    action_sample_counts: dict[str, int]
    prior_action_families: list[str] = Field(default_factory=list)
    cost_medians: dict[str, ActionCostMedian]
    ridge_lambda: float
    lambda_cost: float
    stop_threshold: float
    training_split: Literal["train"] = "train"
    selection_split: Literal["dev"] = "dev"
    train_dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    train_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    train_action_log_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("action_coefficients")
    @classmethod
    def coefficients_are_finite(cls, value: dict[str, list[float]]) -> dict[str, list[float]]:
        for family, coefficients in value.items():
            if not family or len(coefficients) != 12:
                raise ValueError("each action family must have exactly 12 coefficients")
            if any(not isinstance(item, (int, float)) or not math.isfinite(item) for item in coefficients):
                raise ValueError("policy coefficients must be finite")
        return value

    @model_validator(mode="after")
    def frozen_contract(self) -> Self:
        counts = self.action_sample_counts
        families = set(counts)
        if self.feature_order != FEATURE_ORDER:
            raise ValueError("feature_order must match the frozen 12-dimensional SEGR contract")
        if not families or not families.issubset(ACTION_FAMILIES):
            raise ValueError("action sample counts must cover known action families")
        if set(self.cost_medians) != families:
            raise ValueError("cost medians and action sample counts must cover the same families")
        if not set(self.action_coefficients).issubset(families):
            raise ValueError("coefficient families must have recorded sample counts")
        if not set(self.prior_action_families).issubset(families):
            raise ValueError("prior families must have recorded sample counts")
        if set(self.action_coefficients) & set(self.prior_action_families):
            raise ValueError("an action family cannot be both fitted and prior-only")
        if set(self.action_coefficients) | set(self.prior_action_families) != families:
            raise ValueError("every sampled family must be fitted or explicitly prior-only")
        if len(set(self.prior_action_families)) != len(self.prior_action_families):
            raise ValueError("prior action families must be unique")
        if any(count < 0 for count in counts.values()):
            raise ValueError("action sample counts cannot be negative")
        if any(counts[family] < 24 for family in self.action_coefficients):
            raise ValueError("fitted action families require at least 24 train samples")
        if any(counts[family] >= 24 for family in self.prior_action_families):
            raise ValueError("action families with 24 or more samples cannot be marked prior-only")
        expected_mode = (
            "prior_only"
            if not self.action_coefficients
            else "mixed"
            if self.prior_action_families
            else "learned"
        )
        if self.policy_mode != expected_mode:
            raise ValueError("policy_mode must disclose learned, mixed, or prior-only weights")
        if self.ridge_lambda not in {0.1, 1.0, 10.0}:
            raise ValueError("ridge_lambda must be selected from the frozen grid")
        if self.lambda_cost not in {0.1, 0.3, 0.6}:
            raise ValueError("lambda_cost must be selected from the frozen grid")
        if self.stop_threshold not in {0.0, 0.05, 0.1}:
            raise ValueError("stop_threshold must be selected from the frozen grid")
        if self.policy_fingerprint != policy_fingerprint(self):
            raise ValueError("policy_fingerprint does not match the artifact content")
        return self


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def feature_schema_fingerprint() -> str:
    return hashlib.sha256(canonical_json(FEATURE_ORDER).encode("utf-8")).hexdigest()


def policy_fingerprint(weights: SegrPolicyWeights) -> str:
    value = weights.model_dump(mode="json", exclude={"policy_fingerprint"})
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_policy_weights(path: str | Path) -> SegrPolicyWeights:
    """Read one bounded local JSON artifact. URLs are deliberately not supported."""
    raw_path = str(path)
    if "://" in raw_path:
        raise ValueError("policy artifacts must be local JSON files")
    artifact = Path(path)
    if artifact.suffix.casefold() != ".json":
        raise ValueError("policy artifacts must use the .json extension")
    raw = artifact.read_bytes()
    if len(raw) > MAX_POLICY_BYTES:
        raise ValueError("policy artifact exceeds the 2 MB limit")
    value = json.loads(raw.decode("utf-8"))
    weights = SegrPolicyWeights.model_validate(value)
    if weights.feature_schema_sha256 != feature_schema_fingerprint():
        raise ValueError("policy feature schema fingerprint does not match B06")
    return weights
