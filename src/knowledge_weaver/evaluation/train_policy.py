"""Fit and persist formal SEGR weights from licensed train/dev artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from knowledge_weaver.evaluation.dataset import load_dataset
from knowledge_weaver.evaluation.training import (
    ActionExplorationRecord,
    DevPolicyOutcome,
    build_policy_fit_context,
    fit_segr_policy,
    write_policy_artifact,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


def _read_jsonl(path: Path, model: type[ModelT]) -> list[ModelT]:
    values: list[ModelT] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            raise ValueError(f"blank JSONL row at line {line_number} in {path.name}")
        values.append(model.model_validate_json(line))
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fit frozen B06-compatible SEGR policy weights")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--train-actions", type=Path, required=True)
    parser.add_argument("--dev-actions", type=Path, required=True)
    parser.add_argument("--dev-outcomes", type=Path, required=True)
    parser.add_argument(
        "--dataset-manifest",
        default="benchmarks/manifests/dataset.json",
    )
    parser.add_argument(
        "--output",
        default="benchmarks/policies/segr_policy_v1.json",
    )
    arguments = parser.parse_args(argv)

    root = arguments.root.resolve()
    dataset = load_dataset(root, arguments.dataset_manifest)
    context = build_policy_fit_context(
        root,
        dataset,
        manifest_relative_path=arguments.dataset_manifest,
    )
    train_rows = _read_jsonl(arguments.train_actions, ActionExplorationRecord)
    dev_rows = _read_jsonl(arguments.dev_actions, ActionExplorationRecord)
    dev_outcomes = _read_jsonl(arguments.dev_outcomes, DevPolicyOutcome)
    result = fit_segr_policy(train_rows, dev_rows, dev_outcomes, context=context)
    if not result.formal_ready:
        raise ValueError("formal policy artifact gate is not ready; no weights were written")

    artifact_path = write_policy_artifact(
        result,
        repository_root=root,
        relative_path=arguments.output,
    )
    selection_path = artifact_path.with_name(f"{artifact_path.stem}.selection.json")
    artifact_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    selection_hash = hashlib.sha256(selection_path.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "policy_artifact": str(artifact_path),
                "policy_sha256": artifact_hash,
                "selection_manifest": str(selection_path),
                "selection_manifest_sha256": selection_hash,
                "policy_mode": result.artifact.policy_mode,
                "ridge_lambda": result.parameter_selection.ridge_lambda,
                "lambda_cost": result.parameter_selection.lambda_cost,
                "stop_threshold": result.parameter_selection.stop_threshold,
                "train_action_counts": result.train_action_counts,
                "train_action_log_sha256": result.train_action_log_sha256,
                "dev_action_log_sha256": result.dev_action_log_sha256,
                "dev_outcomes_sha256": result.dev_outcomes_sha256,
                "training_tokens": result.training_observed_tokens,
                "training_cost_usd": (
                    str(result.training_cost_usd)
                    if result.training_cost_usd is not None
                    else None
                ),
                "training_cost_cny": (
                    str(result.training_cost_cny)
                    if result.training_cost_cny is not None
                    else None
                ),
                "dev_tokens": result.dev_total_tokens,
                "dev_cost_usd": (
                    str(result.dev_cost_usd) if result.dev_cost_usd is not None else None
                ),
                "dev_cost_cny": (
                    str(result.dev_cost_cny) if result.dev_cost_cny is not None else None
                ),
                "test_split_accessed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
