"""Safe loading and completeness checks for frozen B07 Gold datasets."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from knowledge_weaver.evaluation.contracts import (
    DatasetManifest,
    DatasetStatus,
    PreparedBenchmarkCase,
    SourceAsset,
    canonical_json_hash,
)
from knowledge_weaver.models.contracts import BenchmarkCategory, Split


class DatasetValidationError(ValueError):
    """Raised when a dataset does not satisfy its frozen integrity or sampling rules."""


@dataclass(frozen=True)
class DatasetSummary:
    case_count: int
    category_counts: dict[str, int]
    language_counts: dict[str, int]
    split_counts: dict[str, int]
    split_family_counts: dict[str, int]
    unanswerable_count: int


@dataclass(frozen=True)
class LoadedDataset:
    manifest: DatasetManifest
    cases: tuple[PreparedBenchmarkCase, ...]
    summary: DatasetSummary | None

    @property
    def ready(self) -> bool:
        return self.manifest.status is DatasetStatus.frozen and self.summary is not None


def _resolve_inside(root: Path, relative_path: str) -> Path:
    root_resolved = root.resolve()
    candidate = (root_resolved / Path(*relative_path.split("/"))).resolve(strict=True)
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise DatasetValidationError(f"path escapes dataset root: {relative_path}") from exc
    if not candidate.is_file():
        raise DatasetValidationError(f"expected a regular file: {relative_path}")
    return candidate


def _verify_source_hashes(root: Path, sources: Sequence[SourceAsset]) -> None:
    for source in sources:
        try:
            path = _resolve_inside(root, source.material_path)
        except (OSError, RuntimeError) as exc:
            raise DatasetValidationError(
                f"source material is unavailable: {source.material_path}"
            ) from exc
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != source.raw_sha256:
            raise DatasetValidationError(f"source hash mismatch: {source.source_path}")


def validate_dataset(
    manifest: DatasetManifest, cases: Sequence[PreparedBenchmarkCase]
) -> DatasetSummary:
    if manifest.status is not DatasetStatus.frozen:
        raise DatasetValidationError("only a FROZEN dataset can pass the prep gate")
    if len(cases) != manifest.expected_case_count or len(cases) != manifest.case_count:
        raise DatasetValidationError("frozen dataset must contain exactly 100 declared cases")
    if len({case.case.id for case in cases}) != len(cases):
        raise DatasetValidationError("case IDs must be unique")

    source_by_id = {source.id: source for source in manifest.sources}
    if len(source_by_id) != len(manifest.sources):
        raise DatasetValidationError("source asset IDs must be unique")
    family_ids = {source.source_family_id for source in manifest.sources}
    if any(not source.license.permission_evidence.strip() for source in manifest.sources):
        raise DatasetValidationError(
            "every source must have a recorded rights evidence reference"
        )

    split_families: dict[Split, set[str]] = defaultdict(set)
    family_split: dict[str, Split] = {}
    category_counts = Counter(case.case.category.value for case in cases)
    language_counts = Counter(case.case.language.value for case in cases)
    split_counts = Counter(case.case.split.value for case in cases)
    unanswerable_count = 0

    for prepared in cases:
        case = prepared.case
        if not case.source_family_ids:
            raise DatasetValidationError(f"case {case.id} has no source family")
        if len(set(case.source_family_ids)) != len(case.source_family_ids):
            raise DatasetValidationError(f"case {case.id} repeats a source family")
        if not set(prepared.source_ids).issubset(source_by_id):
            raise DatasetValidationError(f"case {case.id} references an unknown source asset")
        referenced_sources = [source_by_id[source_id] for source_id in prepared.source_ids]
        expected_source_hashes = [
            canonical_json_hash(source.model_dump(mode="json")) for source in referenced_sources
        ]
        if prepared.source_record_sha256 != expected_source_hashes:
            raise DatasetValidationError(
                f"case {case.id} source/license record hash does not match the manifest"
            )
        referenced_family_ids = {source.source_family_id for source in referenced_sources}
        if not referenced_family_ids.issubset(set(case.source_family_ids)):
            raise DatasetValidationError(f"case {case.id} omits a referenced source family")
        if not set(case.source_family_ids).issubset(family_ids):
            raise DatasetValidationError(f"case {case.id} references an unknown source family")
        for source in referenced_sources:
            if source.license.permission_scope.value not in {
                "redistributable",
                "local_evaluation_only",
            }:
                raise DatasetValidationError(f"source {source.id} lacks evaluation permission")
        source_by_path_and_hash = {
            (source.source_path, source.raw_sha256, source.source_family_id): source
            for source in referenced_sources
        }
        for gold in case.gold:
            if not any(
                source_path == gold.source_path and raw_hash == gold.raw_hash
                for source_path, raw_hash, _family in source_by_path_and_hash
            ):
                raise DatasetValidationError(
                    f"case {case.id} Gold does not match a licensed original source and hash"
                )
        for family_id in case.source_family_ids:
            previous_split = family_split.setdefault(family_id, case.split)
            if previous_split is not case.split:
                raise DatasetValidationError(
                    f"source family {family_id} leaks across train/dev/test splits"
                )
            split_families[case.split].add(family_id)
        if not case.answerable:
            unanswerable_count += 1

    if len(family_split) < 15:
        raise DatasetValidationError("the frozen dataset requires at least 15 used source families")

    expected_categories = {category.value: 20 for category in BenchmarkCategory}
    if dict(category_counts) != expected_categories:
        raise DatasetValidationError("the five benchmark categories must each contain 20 cases")
    if any(language_counts.get(language, 0) < 40 for language in ("zh", "en")):
        raise DatasetValidationError(
            "the dataset must contain at least 40 Chinese and 40 English cases"
        )
    if sum(language_counts.values()) != 100:
        raise DatasetValidationError("dataset language counts must cover all 100 cases")
    if unanswerable_count < 10:
        raise DatasetValidationError("the dataset must include at least 10 unanswerable cases")
    for split in Split:
        if len(split_families[split]) < 5:
            raise DatasetValidationError(f"split {split.value} requires at least 5 source families")
    if split_counts.get(Split.test.value, 0) < 40:
        raise DatasetValidationError("test split must contain at least 40 cases")
    test_categories = Counter(
        prepared.case.category.value for prepared in cases if prepared.case.split is Split.test
    )
    if any(test_categories.get(category, 0) < 6 for category in expected_categories):
        raise DatasetValidationError("test split must contain at least 6 cases from each category")

    return DatasetSummary(
        case_count=len(cases),
        category_counts={
            category: category_counts.get(category, 0) for category in expected_categories
        },
        language_counts={language: language_counts.get(language, 0) for language in ("zh", "en")},
        split_counts={split.value: split_counts.get(split.value, 0) for split in Split},
        split_family_counts={split.value: len(split_families[split]) for split in Split},
        unanswerable_count=unanswerable_count,
    )


def load_dataset(
    root: Path, manifest_path: str = "benchmarks/manifests/dataset.json"
) -> LoadedDataset:
    """Load a manifest and its JSONL cases; source paths are confined to ``root``."""
    try:
        manifest_file = _resolve_inside(root, manifest_path)
        raw_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        manifest = DatasetManifest.model_validate(raw_manifest)
    except (OSError, json.JSONDecodeError, ValidationError, RuntimeError) as exc:
        raise DatasetValidationError(f"dataset manifest is invalid: {exc}") from exc

    if manifest.status is DatasetStatus.blocked_inputs:
        return LoadedDataset(manifest=manifest, cases=(), summary=None)

    assert manifest.cases_file is not None
    assert manifest.cases_sha256 is not None
    try:
        cases_file = _resolve_inside(root, manifest.cases_file)
        cases_bytes = cases_file.read_bytes()
        if hashlib.sha256(cases_bytes).hexdigest() != manifest.cases_sha256:
            raise DatasetValidationError("cases JSONL hash does not match the manifest")
        rows = cases_bytes.splitlines()
        if any(not line.strip() for line in rows):
            raise DatasetValidationError("cases JSONL cannot contain blank rows")
        cases = tuple(PreparedBenchmarkCase.model_validate_json(line) for line in rows)
        if len(cases) != manifest.case_count:
            raise DatasetValidationError("cases JSONL count does not match the manifest")
        _verify_source_hashes(root, manifest.sources)
        summary = validate_dataset(manifest, cases)
    except (OSError, ValidationError, RuntimeError) as exc:
        raise DatasetValidationError(f"dataset content is invalid: {exc}") from exc
    return LoadedDataset(manifest=manifest, cases=cases, summary=summary)
