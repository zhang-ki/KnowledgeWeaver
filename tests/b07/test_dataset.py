from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from knowledge_weaver.evaluation.contracts import (
    DatasetManifest,
    DatasetStatus,
    GoldEndpointExpectation,
    GoldEvidenceBasis,
    GoldOutcomeKind,
    GoldPathEdge,
    GoldQualifierReviewState,
    GoldRelationExpectation,
    GoldRelationExpectationManifest,
    HumanReview,
    PermissionScope,
    PreparedBenchmarkCase,
    ReviewChecklist,
    ReviewedGoldPath,
    SourceAsset,
    SourceLicense,
    canonical_json_hash,
    human_review_digest,
)
from knowledge_weaver.evaluation.dataset import (
    DatasetValidationError,
    load_dataset,
    validate_dataset,
)
from knowledge_weaver.models.contracts import (
    BenchmarkCase,
    BenchmarkCategory,
    BenchmarkGold,
    EntityKind,
    Language,
    Polarity,
    RelationPredicate,
    Split,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REVIEWED_AT = datetime(2026, 11, 19, 4, 0, tzinfo=UTC)
TEST_SOURCE = b"Test source evidence.\n"
TEST_SOURCE_HASH = hashlib.sha256(TEST_SOURCE).hexdigest()
CHECKLIST = ReviewChecklist(
    source_license_verified=True,
    quote_verified=True,
    locator_verified=True,
    answer_and_claim_groups_verified=True,
    gold_paths_verified=True,
    answerability_verified=True,
    split_and_family_verified=True,
)


def _uuid(index: int) -> str:
    return f"00000000-0000-4000-8000-{index:012d}"


def _source(family_number: int) -> SourceAsset:
    family_id = _uuid(1000 + family_number)
    return SourceAsset(
        id=_uuid(2000 + family_number),
        source_family_id=family_id,
        source_path=f"sources/family_{family_number}.md",
        material_path=f"benchmarks/data/sources/family_{family_number}.md",
        kind="markdown",
        raw_sha256=TEST_SOURCE_HASH,
        license=SourceLicense(
            license_name="Synthetic test fixture license",
            license_uri="https://example.invalid/license",
            permission_scope=PermissionScope.redistributable,
            permission_evidence="tests/b07 only; synthetic content",
            verified_by="test-reviewer",
            verified_at=REVIEWED_AT,
        ),
    )


def _prepared_case(
    *,
    index: int,
    family_number: int,
    split: Split,
    category: BenchmarkCategory,
    language: Language,
    answerable: bool,
) -> PreparedBenchmarkCase:
    source = _source(family_number)
    gold = []
    quote_hashes = []
    if answerable:
        quote = "Test source evidence."
        gold = [
            BenchmarkGold(
                source_path=source.source_path,
                raw_hash=source.raw_sha256,
                locator={
                    "kind": "markdown",
                    "path": source.source_path,
                    "line_start": 1,
                    "line_end": 1,
                },
                quote=quote,
                grade=2,
                claim_group=f"claim-{index}",
            )
        ]
        quote_hashes = [hashlib.sha256(quote.encode("utf-8")).hexdigest()]
    case = BenchmarkCase(
        id=_uuid(3000 + index),
        split=split,
        source_family_ids=[source.source_family_id],
        category=category,
        language=language,
        question=f"Synthetic test question {index}?",
        answerable=answerable,
        gold=gold,
        gold_paths=[],
        reference_claims=[f"Synthetic claim {index}"] if answerable else [],
        reviewer="test-reviewer",
        reviewed_at=REVIEWED_AT,
    )
    source_ids = [source.id]
    source_hashes = [canonical_json_hash(source.model_dump(mode="json"))]
    review_hash = human_review_digest(
        case,
        source_ids,
        quote_hashes,
        "test-reviewer",
        REVIEWED_AT,
        CHECKLIST,
        source_record_sha256=source_hashes,
    )
    review = HumanReview(
        reviewer_id="test-reviewer",
        reviewed_at=REVIEWED_AT,
        checklist=CHECKLIST,
        reviewed_record_sha256=review_hash,
    )
    return PreparedBenchmarkCase(
        case=case,
        source_ids=source_ids,
        source_record_sha256=source_hashes,
        gold_quote_sha256=quote_hashes,
        human_review=review,
    )


def _valid_frozen_dataset() -> tuple[DatasetManifest, list[PreparedBenchmarkCase]]:
    categories = list(BenchmarkCategory)
    cases = []
    sources_by_family: dict[int, SourceAsset] = {}
    for index in range(100):
        if index < 30:
            split = Split.train
            family_number = index // 6
        elif index < 50:
            split = Split.dev
            family_number = 5 + (index - 30) // 4
        else:
            split = Split.test
            family_number = 10 + (index - 50) // 10
        case = _prepared_case(
            index=index,
            family_number=family_number,
            split=split,
            category=categories[index % len(categories)],
            language=Language.zh if index % 2 == 0 else Language.en,
            answerable=index % 10 != 0,
        )
        cases.append(case)
        sources_by_family.setdefault(family_number, _source(family_number))
    manifest = DatasetManifest(
        dataset_id="synthetic-test-only",
        dataset_version="test",
        status=DatasetStatus.frozen,
        case_count=100,
        cases_file="benchmarks/data/cases.jsonl",
        cases_sha256="a" * 64,
        sources=list(sources_by_family.values()),
    )
    return manifest, cases


def test_dataset_validator_checks_balances_and_frozen_splits() -> None:
    manifest, cases = _valid_frozen_dataset()

    summary = validate_dataset(manifest, cases)

    assert summary.case_count == 100
    assert set(summary.category_counts.values()) == {20}
    assert summary.language_counts == {"zh": 50, "en": 50}
    assert summary.split_counts == {"train": 30, "dev": 20, "test": 50}
    assert summary.split_family_counts == {"train": 5, "dev": 5, "test": 5}
    assert summary.unanswerable_count == 10


def test_dataset_validator_rejects_source_family_leakage() -> None:
    manifest, cases = _valid_frozen_dataset()
    leaked_case = cases[30]
    train_source = manifest.sources[0]
    changed_case = leaked_case.case.model_copy(
        update={"source_family_ids": [train_source.source_family_id]}
    )
    review_hash = human_review_digest(
        changed_case,
        [train_source.id],
        [],
        "test-reviewer",
        REVIEWED_AT,
        CHECKLIST,
        source_record_sha256=[canonical_json_hash(train_source.model_dump(mode="json"))],
    )
    cases[30] = PreparedBenchmarkCase(
        case=changed_case,
        source_ids=[train_source.id],
        source_record_sha256=[canonical_json_hash(train_source.model_dump(mode="json"))],
        gold_quote_sha256=[],
        human_review=HumanReview(
            reviewer_id="test-reviewer",
            reviewed_at=REVIEWED_AT,
            checklist=CHECKLIST,
            reviewed_record_sha256=review_hash,
        ),
    )

    with pytest.raises(DatasetValidationError, match="leaks across"):
        validate_dataset(manifest, cases)


def test_dataset_validator_rejects_duplicate_case_ids() -> None:
    manifest, cases = _valid_frozen_dataset()
    cases[1] = cases[1].model_copy(update={"case": cases[0].case})

    with pytest.raises(DatasetValidationError, match="case IDs must be unique"):
        validate_dataset(manifest, cases)


def test_prepared_case_rejects_quote_hash_or_manual_field_mismatch() -> None:
    _manifest, cases = _valid_frozen_dataset()
    record = cases[1].model_dump(mode="json")
    record["gold_quote_sha256"] = ["0" * 64]

    with pytest.raises(ValidationError, match="Gold quote hash"):
        PreparedBenchmarkCase.model_validate(record)


def test_prepared_case_preserves_exact_gold_quote_whitespace() -> None:
    prepared = _prepared_case(
        index=110,
        family_number=0,
        split=Split.dev,
        category=BenchmarkCategory.single,
        language=Language.en,
        answerable=True,
    )
    quote = "        decoded = self.decode_complete(token)\n            return decoded, token\n"
    gold = BenchmarkGold.model_validate(
        {**prepared.case.gold[0].model_dump(mode="json"), "quote": quote}
    )
    case = BenchmarkCase.model_validate(
        {**prepared.case.model_dump(mode="json"), "gold": [gold.model_dump(mode="json")]}
    )
    quote_hash = hashlib.sha256(quote.encode("utf-8")).hexdigest()
    review_hash = human_review_digest(
        case,
        prepared.source_ids,
        [quote_hash],
        prepared.human_review.reviewer_id,
        prepared.human_review.reviewed_at,
        prepared.human_review.checklist,
        prepared.gold_paths,
        prepared.source_record_sha256,
    )
    review = prepared.human_review.model_copy(
        update={"reviewed_record_sha256": review_hash}
    )
    exact_prepared = PreparedBenchmarkCase.model_validate(
        {
            **prepared.model_dump(mode="json"),
            "case": case.model_dump(mode="json"),
            "gold_quote_sha256": [quote_hash],
            "human_review": review.model_dump(mode="json"),
        }
    )

    assert exact_prepared.case.gold[0].quote == quote
    assert exact_prepared.gold_quote_sha256 == [quote_hash]


def test_benchmark_gold_rejects_whitespace_only_quote_without_normalizing() -> None:
    gold = _prepared_case(
        index=111,
        family_number=0,
        split=Split.dev,
        category=BenchmarkCategory.single,
        language=Language.en,
        answerable=True,
    ).case.gold[0]

    with pytest.raises(ValidationError, match="Gold quote must contain non-whitespace"):
        BenchmarkGold.model_validate({**gold.model_dump(mode="json"), "quote": "   \n\t"})


def test_reviewed_gold_paths_bind_direction_and_original_quote_evidence() -> None:
    source = _source(0)
    quote = "Test source evidence."
    quote_hash = hashlib.sha256(quote.encode("utf-8")).hexdigest()
    relation_id = _uuid(5000)
    case = BenchmarkCase(
        id=_uuid(5001),
        split=Split.train,
        source_family_ids=[source.source_family_id],
        category=BenchmarkCategory.multihop,
        language=Language.en,
        question="Synthetic path question?",
        answerable=True,
        gold=[
            BenchmarkGold(
                source_path=source.source_path,
                raw_hash=source.raw_sha256,
                locator={
                    "kind": "markdown",
                    "path": source.source_path,
                    "line_start": 1,
                    "line_end": 1,
                },
                quote=quote,
                grade=2,
                claim_group="path-edge-claim",
            )
        ],
        gold_paths=[[relation_id]],
        reference_claims=["A reviewed relation is supported by the source quote."],
        reviewer="test-reviewer",
        reviewed_at=REVIEWED_AT,
    )
    gold_paths = [
        ReviewedGoldPath(
            edges=[
                GoldPathEdge(
                    relation_id=relation_id,
                    traversed_reverse=True,
                    evidence_quote_sha256=[quote_hash],
                )
            ]
        )
    ]
    source_ids = [source.id]
    source_hashes = [canonical_json_hash(source.model_dump(mode="json"))]
    review_hash = human_review_digest(
        case,
        source_ids,
        [quote_hash],
        "test-reviewer",
        REVIEWED_AT,
        CHECKLIST,
        gold_paths,
        source_hashes,
    )
    prepared = PreparedBenchmarkCase(
        case=case,
        source_ids=source_ids,
        source_record_sha256=source_hashes,
        gold_quote_sha256=[quote_hash],
        gold_paths=gold_paths,
        human_review=HumanReview(
            reviewer_id="test-reviewer",
            reviewed_at=REVIEWED_AT,
            checklist=CHECKLIST,
            reviewed_record_sha256=review_hash,
        ),
    )

    assert prepared.gold_paths[0].edges[0].traversed_reverse is True
    assert prepared.gold_paths[0].edges[0].evidence_quote_sha256 == [quote_hash]


def test_review_checklist_cannot_claim_unreviewed_gold() -> None:
    values = CHECKLIST.model_dump()
    values["locator_verified"] = False

    with pytest.raises(ValidationError, match="all human-review checklist"):
        ReviewChecklist.model_validate(values)


def test_blocked_manifest_is_reported_without_fabricating_cases() -> None:
    dataset = load_dataset(REPO_ROOT)

    assert dataset.manifest.status is DatasetStatus.blocked_inputs
    assert dataset.cases == ()
    assert dataset.summary is None
    assert dataset.ready is False
    assert len(dataset.manifest.blockers) >= 2


def test_frozen_loader_checks_case_and_original_source_hashes(tmp_path: Path) -> None:
    manifest, cases = _valid_frozen_dataset()
    assert manifest.cases_file is not None
    for source in manifest.sources:
        material = tmp_path / Path(*source.material_path.split("/"))
        material.parent.mkdir(parents=True, exist_ok=True)
        material.write_bytes(TEST_SOURCE)
    cases_bytes = (
        "\n".join(case.model_dump_json() for case in cases) + "\n"
    ).encode("utf-8")
    cases_file = tmp_path / Path(*manifest.cases_file.split("/"))
    cases_file.parent.mkdir(parents=True, exist_ok=True)
    cases_file.write_bytes(cases_bytes)
    frozen_manifest = manifest.model_copy(
        update={"cases_sha256": hashlib.sha256(cases_bytes).hexdigest()}
    )
    manifest_file = tmp_path / "benchmarks" / "manifests" / "dataset.json"
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(
        json.dumps(frozen_manifest.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )

    loaded = load_dataset(tmp_path)

    assert loaded.ready
    assert loaded.summary is not None
    assert loaded.summary.case_count == 100
    assert loaded.summary.unanswerable_count == 10

    first_source = manifest.sources[0]
    (tmp_path / Path(*first_source.material_path.split("/"))).write_bytes(b"tampered")
    with pytest.raises(DatasetValidationError, match="source hash mismatch"):
        load_dataset(tmp_path)


def test_frozen_loader_reads_versioned_relation_expectation_sidecar(tmp_path: Path) -> None:
    manifest, cases = _valid_frozen_dataset()
    prepared = cases[1]
    relation_id = _uuid(9200)
    missing_relation_id = _uuid(9202)
    reviewed_path = ReviewedGoldPath(
        edges=[
            GoldPathEdge(
                relation_id=relation_id,
                traversed_reverse=False,
                evidence_quote_sha256=[prepared.gold_quote_sha256[0]],
            ),
            GoldPathEdge(
                relation_id=missing_relation_id,
                traversed_reverse=True,
                evidence_quote_sha256=[prepared.gold_quote_sha256[0]],
            ),
        ]
    )
    case = prepared.case.model_copy(
        update={"gold_paths": [[relation_id, missing_relation_id]]}
    )
    review_hash = human_review_digest(
        case,
        prepared.source_ids,
        prepared.gold_quote_sha256,
        prepared.human_review.reviewer_id,
        prepared.human_review.reviewed_at,
        prepared.human_review.checklist,
        [reviewed_path],
        prepared.source_record_sha256,
    )
    cases[1] = PreparedBenchmarkCase(
        case=case,
        source_ids=prepared.source_ids,
        source_record_sha256=prepared.source_record_sha256,
        gold_quote_sha256=prepared.gold_quote_sha256,
        gold_paths=[reviewed_path],
        human_review=prepared.human_review.model_copy(
            update={"reviewed_record_sha256": review_hash}
        ),
    )
    source = next(item for item in manifest.sources if item.id == prepared.source_ids[0])
    source_version_id = _uuid(9201)
    expectation = GoldRelationExpectation(
        relation_id=relation_id,
        predicate=RelationPredicate.RETURNS,
        polarity=Polarity.positive,
        subject=GoldEndpointExpectation(
            source_uri="fixture://family-0",
            source_version_id=source_version_id,
            raw_file_sha256=source.raw_sha256,
            relative_path=source.source_path,
            qualified_symbol="package.factory",
            entity_kind=EntityKind.function,
        ),
        object=GoldEndpointExpectation(
            source_uri="fixture://family-0",
            source_version_id=source_version_id,
            raw_file_sha256=source.raw_sha256,
            relative_path=source.source_path,
            qualified_symbol="result value",
            entity_kind=EntityKind.concept,
        ),
        qualifier_review_state=GoldQualifierReviewState.reviewed_empty,
        qualifier_values={},
        result_role="return_value",
        outcome_kind=GoldOutcomeKind.return_value,
        evidence_basis=GoldEvidenceBasis.source_code,
    )
    expectation_manifest = GoldRelationExpectationManifest(
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.dataset_version,
        expectations=[
            expectation,
            expectation.model_copy(update={"relation_id": missing_relation_id}),
        ],
    )
    expectation_bytes = expectation_manifest.model_dump_json().encode("utf-8")
    cases_bytes = ("\n".join(case.model_dump_json() for case in cases) + "\n").encode(
        "utf-8"
    )
    assert manifest.cases_file is not None
    assert manifest.relation_expectations_file is None
    for source_asset in manifest.sources:
        material = tmp_path / Path(*source_asset.material_path.split("/"))
        material.parent.mkdir(parents=True, exist_ok=True)
        material.write_bytes(TEST_SOURCE)
    cases_file = tmp_path / Path(*manifest.cases_file.split("/"))
    cases_file.parent.mkdir(parents=True, exist_ok=True)
    cases_file.write_bytes(cases_bytes)
    expectation_path = "benchmarks/manifests/relation-expectations-v1.json"
    expectation_file = tmp_path / Path(*expectation_path.split("/"))
    expectation_file.parent.mkdir(parents=True, exist_ok=True)
    expectation_file.write_bytes(expectation_bytes)
    frozen_manifest = manifest.model_copy(
        update={
            "cases_sha256": hashlib.sha256(cases_bytes).hexdigest(),
            "relation_expectations_file": expectation_path,
            "relation_expectations_sha256": hashlib.sha256(expectation_bytes).hexdigest(),
        }
    )
    manifest_file = tmp_path / "benchmarks" / "manifests" / "dataset.json"
    manifest_file.write_text(
        json.dumps(frozen_manifest.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )

    loaded = load_dataset(tmp_path)

    assert loaded.ready
    assert set(loaded.relation_expectations) == {relation_id, missing_relation_id}
    assert loaded.required_relation_ids == frozenset({relation_id, missing_relation_id})
    assert len(loaded.manifest_sha256 or "") == 64
    assert len(loaded.required_relation_queue_sha256) == 64

    normative_obligation = expectation.model_copy(
        update={
            "predicate": RelationPredicate.INVALIDATES,
            "qualifier_review_state": GoldQualifierReviewState.reviewed_values,
            "qualifier_values": {"normative_modality": "SHALL"},
            "result_role": "invalidated_target",
            "outcome_kind": GoldOutcomeKind.normative_obligation,
            "evidence_basis": GoldEvidenceBasis.normative_specification,
        }
    )
    assert GoldRelationExpectation.model_validate(
        normative_obligation.model_dump(mode="json")
    ) == normative_obligation
    invalid_runtime_event = normative_obligation.model_copy(
        update={"outcome_kind": GoldOutcomeKind.state_change}
    )
    with pytest.raises(ValidationError, match="NORMATIVE_OBLIGATION"):
        GoldRelationExpectation.model_validate(
            invalid_runtime_event.model_dump(mode="json")
        )

    missing_expectation_bytes = GoldRelationExpectationManifest(
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.dataset_version,
        expectations=[expectation],
    ).model_dump_json().encode("utf-8")
    expectation_file.write_bytes(missing_expectation_bytes)
    missing_expectation_manifest = frozen_manifest.model_copy(
        update={
            "relation_expectations_sha256": hashlib.sha256(
                missing_expectation_bytes
            ).hexdigest()
        }
    )
    manifest_file.write_text(
        json.dumps(missing_expectation_manifest.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(DatasetValidationError, match="exactly cover"):
        load_dataset(tmp_path)

    expectation_file.write_bytes(expectation_bytes)
    manifest_file.write_text(
        json.dumps(frozen_manifest.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    expectation_file.write_bytes(expectation_bytes + b" ")
    with pytest.raises(DatasetValidationError, match="relation expectation hash"):
        load_dataset(tmp_path)


def test_source_license_requires_scope_and_rights_evidence() -> None:
    with pytest.raises(ValidationError):
        SourceLicense(
            license_name="",
            license_uri="",
            verified_by="",
            verified_at=REVIEWED_AT,
        )


def test_frozen_manifest_requires_hash_and_full_case_count() -> None:
    with pytest.raises(ValidationError, match="exactly 100"):
        DatasetManifest(
            dataset_id="broken",
            dataset_version="1",
            status=DatasetStatus.frozen,
            case_count=99,
            cases_file="benchmarks/data/cases.jsonl",
            cases_sha256="a" * 64,
            sources=[_source(0)],
        )
