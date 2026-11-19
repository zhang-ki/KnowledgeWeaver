"""B07-specific records layered over the frozen B00 evaluation contracts."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from knowledge_weaver.models.contracts import BenchmarkCase, SHA256Text, SourceKind

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
UUIDText = Annotated[
    str,
    StringConstraints(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    ),
]


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PermissionScope(str, Enum):
    redistributable = "redistributable"
    local_evaluation_only = "local_evaluation_only"


class DatasetStatus(str, Enum):
    blocked_inputs = "BLOCKED_INPUTS"
    frozen = "FROZEN"


class ReviewChecklist(EvaluationModel):
    source_license_verified: bool
    quote_verified: bool
    locator_verified: bool
    answer_and_claim_groups_verified: bool
    gold_paths_verified: bool
    answerability_verified: bool
    split_and_family_verified: bool

    @model_validator(mode="after")
    def all_checks_passed(self) -> Self:
        if not all(self.model_dump().values()):
            raise ValueError("all human-review checklist fields must be true")
        return self


class HumanReview(EvaluationModel):
    reviewer_id: NonEmptyText
    reviewed_at: datetime
    checklist: ReviewChecklist
    reviewed_record_sha256: SHA256Text

    @field_validator("reviewed_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reviewed_at must include a timezone")
        return value


class GoldPathEdge(EvaluationModel):
    relation_id: UUIDText
    traversed_reverse: bool
    evidence_quote_sha256: list[SHA256Text] = Field(min_length=1)


class ReviewedGoldPath(EvaluationModel):
    edges: list[GoldPathEdge] = Field(min_length=1)


class PreparedBenchmarkCase(EvaluationModel):
    case: BenchmarkCase
    source_ids: list[UUIDText] = Field(min_length=1)
    source_record_sha256: list[SHA256Text] = Field(min_length=1)
    gold_quote_sha256: list[SHA256Text]
    gold_paths: list[ReviewedGoldPath] = Field(default_factory=list)
    human_review: HumanReview

    @model_validator(mode="after")
    def review_and_quotes_match(self) -> Self:
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("source_ids must be unique")
        if len(self.source_record_sha256) != len(self.source_ids):
            raise ValueError("source_record_sha256 must align one-to-one with source_ids")
        if len(self.gold_quote_sha256) != len(self.case.gold):
            raise ValueError("gold_quote_sha256 must align one-to-one with case.gold")
        expected_quotes = [
            hashlib.sha256(gold.quote.encode("utf-8")).hexdigest() for gold in self.case.gold
        ]
        if self.gold_quote_sha256 != expected_quotes:
            raise ValueError("a Gold quote hash does not match its exact UTF-8 quote")
        if len(self.gold_paths) != len(self.case.gold_paths):
            raise ValueError("reviewed Gold paths must align one-to-one with case.gold_paths")
        reviewed_quote_hashes = set(self.gold_quote_sha256)
        for index, (reviewed_path, relation_ids) in enumerate(
            zip(self.gold_paths, self.case.gold_paths, strict=True)
        ):
            if [edge.relation_id for edge in reviewed_path.edges] != relation_ids:
                raise ValueError(f"reviewed Gold path {index} relation IDs do not match the case")
            for edge in reviewed_path.edges:
                if not set(edge.evidence_quote_sha256).issubset(reviewed_quote_hashes):
                    raise ValueError("each Gold path edge must cite reviewed original quote hashes")
        if self.case.reviewer != self.human_review.reviewer_id:
            raise ValueError("case reviewer must match the human-review record")
        if self.case.reviewed_at != self.human_review.reviewed_at:
            raise ValueError("case reviewed_at must match the human-review record")
        expected_review_hash = human_review_digest(
            self.case,
            self.source_ids,
            self.gold_quote_sha256,
            self.human_review.reviewer_id,
            self.human_review.reviewed_at,
            self.human_review.checklist,
            self.gold_paths,
            self.source_record_sha256,
        )
        if self.human_review.reviewed_record_sha256 != expected_review_hash:
            raise ValueError("human-review hash does not match the reviewed record")
        return self


class SourceLicense(EvaluationModel):
    license_name: NonEmptyText
    license_uri: NonEmptyText
    permission_scope: PermissionScope
    permission_evidence: NonEmptyText
    verified_by: NonEmptyText
    verified_at: datetime

    @field_validator("verified_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("verified_at must include a timezone")
        return value


class SourceAsset(EvaluationModel):
    id: UUIDText
    source_family_id: UUIDText
    source_path: NonEmptyText
    material_path: NonEmptyText
    kind: SourceKind
    raw_sha256: SHA256Text
    license: SourceLicense

    @field_validator("source_path", "material_path")
    @classmethod
    def relative_posix_path(cls, value: str) -> str:
        if "\\" in value or ":" in value:
            raise ValueError("source paths must use relative POSIX syntax")
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or path.as_posix() != value
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("source paths must be normalized and remain inside the dataset root")
        return value


class DatasetManifest(EvaluationModel):
    schema_version: Literal[1] = 1
    plan_version: Literal["KW-P0-2026-11-18"] = "KW-P0-2026-11-18"
    dataset_id: NonEmptyText
    dataset_version: NonEmptyText
    status: DatasetStatus
    expected_case_count: Literal[100] = 100
    case_count: int = Field(ge=0, le=100)
    cases_file: NonEmptyText | None = None
    cases_sha256: SHA256Text | None = None
    sources: list[SourceAsset] = Field(default_factory=list)
    blockers: list[NonEmptyText] = Field(default_factory=list)

    @model_validator(mode="after")
    def status_fields_match(self) -> Self:
        if self.status is DatasetStatus.blocked_inputs:
            if self.case_count != 0 or self.cases_file is not None or self.cases_sha256 is not None:
                raise ValueError(
                    "BLOCKED_INPUTS manifests cannot claim a partial or frozen dataset"
                )
            if self.sources:
                raise ValueError("BLOCKED_INPUTS manifests cannot claim reviewed source assets")
            if not self.blockers:
                raise ValueError("BLOCKED_INPUTS requires explicit blocker records")
        else:
            if self.case_count != self.expected_case_count:
                raise ValueError("FROZEN datasets must contain exactly 100 cases")
            if self.cases_file is None or self.cases_sha256 is None:
                raise ValueError("FROZEN datasets require a cases file and content hash")
            if self.blockers:
                raise ValueError("FROZEN datasets cannot retain unresolved blockers")
            if not self.sources:
                raise ValueError("FROZEN datasets require licensed source records")
        if self.cases_file is not None:
            if "\\" in self.cases_file or ":" in self.cases_file:
                raise ValueError("cases_file must use relative POSIX syntax")
            path = PurePosixPath(self.cases_file)
            if (
                path.is_absolute()
                or path.as_posix() != self.cases_file
                or any(part in {"", ".", ".."} for part in path.parts)
            ):
                raise ValueError("cases_file must remain inside the repository root")
        if len({source.id for source in self.sources}) != len(self.sources):
            raise ValueError("source asset IDs must be unique")
        return self


class ActionCostMedian(EvaluationModel):
    estimated_tokens: int = Field(gt=0)
    elapsed_ms: int = Field(gt=0)


class RidgePolicyWeights(EvaluationModel):
    """Frozen B06 policy artifact schema; B07.prep does not fit an artifact."""

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
    train_dataset_sha256: SHA256Text
    train_manifest_sha256: SHA256Text
    train_action_log_sha256: SHA256Text
    feature_schema_sha256: SHA256Text
    policy_fingerprint: SHA256Text

    @field_validator("action_coefficients")
    @classmethod
    def coefficient_shape_and_values(cls, value: dict[str, list[float]]) -> dict[str, list[float]]:
        for family, coefficients in value.items():
            if not family or len(coefficients) != 12:
                raise ValueError("each action family must have exactly 12 coefficients")
            if any(not math.isfinite(coefficient) for coefficient in coefficients):
                raise ValueError("policy coefficients must be finite")
        return value

    @field_validator("action_sample_counts")
    @classmethod
    def sample_counts_nonnegative(cls, value: dict[str, int]) -> dict[str, int]:
        if any(count < 0 for count in value.values()):
            raise ValueError("action sample counts cannot be negative")
        return value

    @model_validator(mode="after")
    def frozen_policy_contract(self) -> Self:
        action_families = {"bm25", "dense", "symbol", "entity", "graph", "rerank"}
        expected_features = [
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
        if self.feature_order != expected_features:
            raise ValueError("feature_order must match the frozen 12-dimensional SEGR contract")
        if not set(self.prior_action_families).issubset(self.action_sample_counts):
            raise ValueError("prior action families must have recorded sample counts")
        if not set(self.action_coefficients).issubset(self.action_sample_counts):
            raise ValueError("coefficient families must have recorded sample counts")
        if not self.action_sample_counts:
            raise ValueError("a policy artifact must record at least one action family")
        if any(
            self.action_sample_counts.get(family, 0) < 24
            for family in self.action_coefficients
        ):
            raise ValueError("fitted action families require at least 24 train samples")
        if set(self.action_coefficients) & set(self.prior_action_families):
            raise ValueError("an action family cannot be both fitted and prior-only")
        if len(set(self.prior_action_families)) != len(self.prior_action_families):
            raise ValueError("prior action families must be unique")
        if any(self.action_sample_counts[family] >= 24 for family in self.prior_action_families):
            raise ValueError("action families with 24 or more samples cannot be marked prior-only")
        fitted_and_prior = set(self.action_coefficients) | set(self.prior_action_families)
        if fitted_and_prior != set(self.action_sample_counts):
            raise ValueError("each sampled action family must be fitted or explicitly prior-only")
        if not set(self.action_sample_counts).issubset(action_families):
            raise ValueError("action families must match the frozen SEGR action set")
        expected_mode = (
            "prior_only"
            if not self.action_coefficients
            else "mixed"
            if self.prior_action_families
            else "learned"
        )
        if self.policy_mode != expected_mode:
            raise ValueError("policy_mode must disclose learned, mixed, or prior-only weights")
        if set(self.cost_medians) != set(self.action_sample_counts):
            raise ValueError("cost medians and action sample counts must cover the same families")
        if self.ridge_lambda not in {0.1, 1.0, 10.0}:
            raise ValueError("ridge_lambda must be selected from the frozen grid")
        if self.lambda_cost not in {0.1, 0.3, 0.6}:
            raise ValueError("lambda_cost must be selected from the frozen grid")
        if self.stop_threshold not in {0.0, 0.05, 0.1}:
            raise ValueError("stop_threshold must be selected from the frozen grid")
        expected = policy_fingerprint(self)
        if self.policy_fingerprint != expected:
            raise ValueError("policy_fingerprint does not match the policy artifact content")
        return self


def canonical_json_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def human_review_digest(
    case: BenchmarkCase,
    source_ids: list[str],
    gold_quote_sha256: list[str],
    reviewer_id: str,
    reviewed_at: datetime,
    checklist: ReviewChecklist,
    gold_paths: list[ReviewedGoldPath] | None = None,
    source_record_sha256: list[str] | None = None,
) -> str:
    return canonical_json_hash(
        {
            "case": case.model_dump(mode="json"),
            "source_ids": sorted(source_ids),
            "gold_quote_sha256": gold_quote_sha256,
            "reviewer_id": reviewer_id,
            "reviewed_at": reviewed_at.isoformat(),
            "checklist": checklist.model_dump(mode="json"),
            "gold_paths": [path.model_dump(mode="json") for path in gold_paths or []],
            "source_record_sha256": source_record_sha256 or [],
        }
    )


def policy_fingerprint(policy: RidgePolicyWeights) -> str:
    payload = policy.model_dump(mode="json", exclude={"policy_fingerprint"})
    return canonical_json_hash(payload)
