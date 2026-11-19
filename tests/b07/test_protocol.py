from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from knowledge_weaver.evaluation.contracts import (
    ActionCostMedian,
    RidgePolicyWeights,
    policy_fingerprint,
)
from knowledge_weaver.evaluation.protocol import EvaluationProtocol, load_protocol

REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = REPO_ROOT / "benchmarks" / "configs" / "b07_protocol_v1.json"


def test_frozen_protocol_has_nine_systems_three_budgets_ten_ablations() -> None:
    protocol = load_protocol(PROTOCOL_PATH)

    assert len(protocol.systems) == 9
    assert len(protocol.budgets) == 3
    assert len(protocol.ablations) == 10
    assert protocol.computed_suite_count == 111
    assert protocol.formal_suite_count == 111
    assert protocol.final_status == "BLOCKED_ON_PREP_DATA_UPSTREAMS_PROVIDER_BUDGET"
    assert protocol.metric_plan.retrieval_k == [5, 10, 20]
    assert protocol.metric_plan.bootstrap_confidence_level == 0.95


def test_protocol_rejects_changed_budget_or_suite_count() -> None:
    raw = PROTOCOL_PATH.read_text(encoding="utf-8")
    value = EvaluationProtocol.model_validate_json(raw).model_dump(mode="json")
    value["budgets"][0]["max_steps"] = 4

    with pytest.raises(ValueError, match="differs from the frozen evaluation plan"):
        EvaluationProtocol.model_validate(value)


def test_exported_schemas_are_valid_and_match_frozen_prep_manifests() -> None:
    manifests_dir = REPO_ROOT / "benchmarks" / "manifests"
    schema_paths = sorted(manifests_dir.glob("*.schema.json"))
    assert len(schema_paths) == 7
    for schema_path in schema_paths:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)

    protocol_schema = json.loads(
        (manifests_dir / "evaluation_protocol.schema.json").read_text(encoding="utf-8")
    )
    protocol_value = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(protocol_value, protocol_schema)

    dataset_schema = json.loads(
        (manifests_dir / "dataset_manifest.schema.json").read_text(encoding="utf-8")
    )
    dataset_value = json.loads(
        (manifests_dir / "dataset.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(dataset_value, dataset_schema)


def test_policy_weights_fingerprint_discloses_prior_only_fallback() -> None:
    families = ["bm25", "dense", "symbol", "entity", "graph", "rerank"]
    values = {
        "schema_version": 1,
        "algorithm": "segr_ridge_v1",
        "policy_mode": "prior_only",
        "feature_order": [
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
        ],
        "action_coefficients": {},
        "action_sample_counts": dict.fromkeys(families, 0),
        "prior_action_families": families,
        "cost_medians": {
            family: ActionCostMedian(estimated_tokens=10, elapsed_ms=100)
            for family in families
        },
        "ridge_lambda": 1.0,
        "lambda_cost": 0.3,
        "stop_threshold": 0.05,
        "training_split": "train",
        "selection_split": "dev",
        "train_dataset_sha256": "a" * 64,
        "train_manifest_sha256": "b" * 64,
        "train_action_log_sha256": "c" * 64,
        "feature_schema_sha256": "d" * 64,
        "policy_fingerprint": "0" * 64,
    }
    provisional = RidgePolicyWeights.model_construct(**values)
    values["policy_fingerprint"] = policy_fingerprint(provisional)

    weights = RidgePolicyWeights.model_validate(values)

    assert weights.policy_mode == "prior_only"
    assert weights.policy_fingerprint == policy_fingerprint(weights)
    schema = json.loads(
        (REPO_ROOT / "benchmarks" / "manifests" / "segr_policy_weights.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.validate(weights.model_dump(mode="json"), schema)
