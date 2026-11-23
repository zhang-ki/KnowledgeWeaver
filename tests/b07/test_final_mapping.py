from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from knowledge_weaver.evaluation.contracts import (
    GoldPathEdge,
    HumanReview,
    PermissionScope,
    PreparedBenchmarkCase,
    ReviewChecklist,
    ReviewedGoldPath,
    SourceAsset,
    SourceLicense,
    human_review_digest,
)
from knowledge_weaver.evaluation.generation_binding import GenerationBindingError
from knowledge_weaver.evaluation.gold_mapping import (
    GoldMappingStatus,
    map_gold_to_generation,
    mapped_evidence_group_recall,
    strict_chunk_recall,
)
from knowledge_weaver.evaluation.metrics import CitationJudgment
from knowledge_weaver.evaluation.query_metrics import compute_query_measurements
from knowledge_weaver.models.contracts import (
    BenchmarkCase,
    BenchmarkCategory,
    BenchmarkGold,
    Chunk,
    ChunkSpan,
    Document,
    Evidence,
    Generation,
    GenerationState,
    Language,
    MarkdownLocator,
    Path,
    PathStep,
    Polarity,
    Relation,
    RelationPredicate,
    SourceKind,
    Split,
    StructureType,
)

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
SOURCE_ID = "00000000-0000-4000-8000-000000000001"
FAMILY_ID = "00000000-0000-4000-8000-000000000002"
CASE_ID = "00000000-0000-4000-8000-000000000003"
DOCUMENT_ID = "00000000-0000-4000-8000-000000000004"
CHUNK_ONE_ID = "00000000-0000-4000-8000-000000000005"
CHUNK_TWO_ID = "00000000-0000-4000-8000-000000000006"
EVIDENCE_ONE_ID = "00000000-0000-4000-8000-000000000007"
EVIDENCE_TWO_ID = "00000000-0000-4000-8000-000000000008"
RELATION_ID = "00000000-0000-4000-8000-000000000009"
SUBJECT_ID = "00000000-0000-4000-8000-000000000010"
OBJECT_ID = "00000000-0000-4000-8000-000000000011"
PROVENANCE_ID = "00000000-0000-4000-8000-000000000012"


def _fixture(
    text: str = "prefix: accepted evidence. suffix",
    *,
    overlap: bool = True,
    include_evidence: bool = True,
    include_path: bool = False,
    claim_groups: tuple[str, ...] = ("claim-1",),
) -> tuple[PreparedBenchmarkCase, SourceAsset, Document, list[Chunk], list[Evidence], list[Relation]]:
    quote = "accepted evidence."
    quote_start = text.index(quote)
    locator = MarkdownLocator(kind="markdown", path="docs/guide.md", line_start=1, line_end=1)
    raw_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    quote_hash = hashlib.sha256(quote.encode("utf-8")).hexdigest()
    source_record_hash = hashlib.sha256(b"synthetic source record").hexdigest()
    source = SourceAsset(
        id=SOURCE_ID,
        source_family_id=FAMILY_ID,
        source_path="docs/guide.md",
        material_path="fixtures/guide.md",
        kind=SourceKind.markdown,
        raw_sha256=raw_hash,
        license=SourceLicense(
            license_name="synthetic test fixture",
            license_uri="https://example.invalid/license",
            permission_scope=PermissionScope.local_evaluation_only,
            permission_evidence="in-memory synthetic fixture",
            verified_by="fixture",
            verified_at=NOW,
        ),
    )
    reviewed_paths = (
        [
            ReviewedGoldPath(
                edges=[
                    GoldPathEdge(
                        relation_id=RELATION_ID,
                        traversed_reverse=False,
                        evidence_quote_sha256=[quote_hash],
                    )
                ]
            )
        ]
        if include_path
        else []
    )
    case = BenchmarkCase(
        id=CASE_ID,
        split=Split.test,
        source_family_ids=[FAMILY_ID],
        category=BenchmarkCategory.multihop if include_path else BenchmarkCategory.single,
        language=Language.en,
        question="What does the source say?",
        answerable=True,
        gold=[
            BenchmarkGold(
                source_path="docs/guide.md",
                raw_hash=raw_hash,
                locator=locator,
                quote=quote,
                grade=2,
                claim_group=claim_group,
            )
            for claim_group in claim_groups
        ],
        gold_paths=[[RELATION_ID]] if include_path else [],
        reviewer="fixture",
        reviewed_at=NOW,
    )
    checklist = ReviewChecklist(
        source_license_verified=True,
        quote_verified=True,
        locator_verified=True,
        answer_and_claim_groups_verified=True,
        gold_paths_verified=True,
        answerability_verified=True,
        split_and_family_verified=True,
    )
    review = HumanReview(
        reviewer_id="fixture",
        reviewed_at=NOW,
        checklist=checklist,
        reviewed_record_sha256=human_review_digest(
            case,
            [SOURCE_ID],
            [quote_hash for _ in claim_groups],
            "fixture",
            NOW,
            checklist,
            reviewed_paths,
            [source_record_hash],
        ),
    )
    prepared = PreparedBenchmarkCase(
        case=case,
        source_ids=[SOURCE_ID],
        source_record_sha256=[source_record_hash],
        gold_quote_sha256=[quote_hash for _ in claim_groups],
        gold_paths=reviewed_paths,
        human_review=review,
    )
    document = Document(
        id=DOCUMENT_ID,
        source_id=SOURCE_ID,
        relative_path="docs/guide.md",
        raw_hash=raw_hash,
        parser_fingerprint="synthetic-parser-v1",
        normalized_text=text,
    )

    def make_chunk(chunk_id: str, chunk_text: str, start: int) -> Chunk:
        span = ChunkSpan(
            chunk_start=0,
            chunk_end=len(chunk_text),
            document_start=start,
            document_end=start + len(chunk_text),
            locator=locator,
        )
        return Chunk(
            id=chunk_id,
            document_id=DOCUMENT_ID,
            is_leaf=True,
            text=chunk_text,
            text_hash=hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
            spans=[span],
            structure_type=StructureType.paragraph,
            chunker_fingerprint="synthetic-chunker-v1",
        )

    chunks = [make_chunk(CHUNK_ONE_ID, text, 0)]
    if overlap:
        chunks.append(make_chunk(CHUNK_TWO_ID, quote, quote_start))
    evidence: list[Evidence] = []
    if include_evidence:
        for index, chunk in enumerate(chunks):
            evidence.append(
                Evidence(
                    id=EVIDENCE_ONE_ID if index == 0 else EVIDENCE_TWO_ID,
                    chunk_id=chunk.id,
                    start=0,
                    end=len(chunk.text),
                    quote=chunk.text,
                    locator=locator,
                    provenance_id=PROVENANCE_ID,
                )
            )
    relations = (
        [
            Relation(
                id=RELATION_ID,
                subject_id=SUBJECT_ID,
                predicate=RelationPredicate.DESCRIBES,
                object_id=OBJECT_ID,
                polarity=Polarity.positive,
                provenance_id=PROVENANCE_ID,
                evidence_ids=[item.id for item in evidence],
            )
        ]
        if include_path and evidence
        else []
    )
    return prepared, source, document, chunks, evidence, relations


def _generation_for(
    document: Document,
    chunks: list[Chunk],
    evidence: list[Evidence],
    relations: list[Relation],
) -> Generation:
    members = {
        "source_version": ["00000000-0000-4000-8000-000000000013"],
        "document": [document.id],
        "chunk": [item.id for item in chunks],
        "evidence": [item.id for item in evidence],
        "provenance": sorted({item.provenance_id for item in evidence}),
        "entity": sorted(
            {entity_id for item in relations for entity_id in (item.subject_id, item.object_id)}
        ),
        "mention": [],
        "relation": [item.id for item in relations],
    }
    member_hashes = {
        kind: hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()
        for kind, ids in members.items()
    }
    index_manifest: dict[str, object] = {
        "member_hashes": member_hashes,
        "indexes": {"fixture": "mapping-test"},
    }
    source_version_ids = members["source_version"]
    generation_sha256 = hashlib.sha256(
        json.dumps(
            {
                "source_version_ids": source_version_ids,
                "member_hashes": member_hashes,
                "index_manifest": index_manifest,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    index_manifest["generation_hash"] = generation_sha256
    return Generation(
        id="00000000-0000-4000-8000-000000000014",
        corpus_id="00000000-0000-4000-8000-000000000015",
        state=GenerationState.READY,
        source_version_ids=source_version_ids,
        chunk_ids=members["chunk"],
        entity_ids=members["entity"],
        relation_ids=members["relation"],
        index_manifest=index_manifest,
        embedding_fingerprint="synthetic-mapping-test-v1",
        published_at=NOW,
    )


def _map(fixture: tuple[PreparedBenchmarkCase, SourceAsset, Document, list[Chunk], list[Evidence], list[Relation]]):
    case, source, document, chunks, evidence, relations = fixture
    return map_gold_to_generation(
        [case],
        [source],
        [document],
        chunks,
        evidence,
        relations,
        generation=_generation_for(document, chunks, evidence, relations),
        dataset_sha256="b" * 64,
    )


def _with_gold_groups(
    prepared: PreparedBenchmarkCase,
    groups: list[tuple[str, MarkdownLocator]],
    *,
    source_path: str | None = None,
) -> PreparedBenchmarkCase:
    template = prepared.case.gold[0]
    gold = [
        template.model_copy(
            update={
                "claim_group": claim_group,
                "locator": locator,
                "source_path": source_path or template.source_path,
            }
        )
        for claim_group, locator in groups
    ]
    case = prepared.case.model_copy(update={"gold": gold})
    quote_hashes = [hashlib.sha256(item.quote.encode("utf-8")).hexdigest() for item in gold]
    review_digest = human_review_digest(
        case,
        prepared.source_ids,
        quote_hashes,
        prepared.human_review.reviewer_id,
        prepared.human_review.reviewed_at,
        prepared.human_review.checklist,
        list(prepared.gold_paths),
        prepared.source_record_sha256,
    )
    review = prepared.human_review.model_copy(update={"reviewed_record_sha256": review_digest})
    return PreparedBenchmarkCase(
        case=case,
        source_ids=prepared.source_ids,
        source_record_sha256=prepared.source_record_sha256,
        gold_quote_sha256=quote_hashes,
        gold_paths=prepared.gold_paths,
        human_review=review,
    )


def test_gold_span_maps_to_overlapping_leaf_group_and_strict_chunk_recall() -> None:
    report = _map(_fixture())
    assert report.ready
    mapping = report.span_mappings[0]
    assert mapping.status is GoldMappingStatus.mapped
    assert len(mapping.chunk_ids) == 2
    assert len(mapping.evidence_ids) == 2
    assert mapped_evidence_group_recall([mapping.chunk_ids[0]], [mapping]) == 1.0
    assert strict_chunk_recall([mapping.chunk_ids[0]], [mapping]) == 0.5


def test_gold_path_maps_multiple_claim_groups_for_the_same_physical_quote() -> None:
    report = _map(
        _fixture(include_path=True, claim_groups=("claim-1", "claim-2"))
    )

    assert report.ready
    assert [item.claim_group for item in report.span_mappings] == ["claim-1", "claim-2"]
    assert report.span_mappings[0].evidence_group_id != report.span_mappings[1].evidence_group_id
    assert report.span_mappings[0].evidence_ids == report.span_mappings[1].evidence_ids
    assert report.path_mappings[0].status is GoldMappingStatus.mapped
    assert mapped_evidence_group_recall(
        [report.span_mappings[0].chunk_ids[0]], report.span_mappings
    ) == 1.0
    assert strict_chunk_recall(
        [report.span_mappings[0].chunk_ids[0]], report.span_mappings
    ) == 0.5


def test_gold_path_rejects_same_quote_at_different_document_intervals() -> None:
    quote = "accepted evidence."
    text = f"{quote}\n{quote}"
    prepared, source, document, chunks, evidence, relations = _fixture(
        text, overlap=False, include_path=True
    )
    source_path = "docs/guide.txt"
    first_locator = MarkdownLocator(path=source_path, line_start=1, line_end=1)
    second_locator = MarkdownLocator(path=source_path, line_start=2, line_end=2)
    broad_locator = MarkdownLocator(path=source_path, line_start=1, line_end=2)
    prepared = _with_gold_groups(
        prepared,
        [("claim-1", first_locator), ("claim-2", second_locator)],
        source_path=source_path,
    )
    source = source.model_copy(update={"source_path": source_path})
    document = document.model_copy(update={"relative_path": source_path})
    chunk = chunks[0].model_copy(
        update={"spans": [chunks[0].spans[0].model_copy(update={"locator": broad_locator})]}
    )
    proof = evidence[0].model_copy(update={"locator": broad_locator})

    report = _map((prepared, source, document, [chunk], [proof], relations))

    assert all(item.status is GoldMappingStatus.mapped for item in report.span_mappings)
    assert report.span_mappings[0].evidence_ids == report.span_mappings[1].evidence_ids
    assert report.span_mappings[0].document_intervals != report.span_mappings[1].document_intervals
    assert report.path_mappings[0].status is GoldMappingStatus.unmapped
    assert "does not identify exactly one reviewed source span" in (
        report.path_mappings[0].reason or ""
    )


def test_gold_path_rejects_same_interval_with_different_evidence_sets() -> None:
    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    first_locator = MarkdownLocator(path="docs/guide.md", line_start=1, line_end=1)
    second_locator = MarkdownLocator(path="docs/guide.md", line_start=2, line_end=2)
    broad_locator = MarkdownLocator(path="docs/guide.md", line_start=1, line_end=2)
    prepared = _with_gold_groups(
        prepared, [("claim-1", first_locator), ("claim-2", second_locator)]
    )
    chunk = chunks[0].model_copy(
        update={"spans": [chunks[0].spans[0].model_copy(update={"locator": broad_locator})]}
    )
    first_evidence = evidence[0].model_copy(update={"locator": first_locator})
    second_evidence = evidence[0].model_copy(
        update={"id": EVIDENCE_TWO_ID, "locator": second_locator}
    )
    relation = relations[0].model_copy(
        update={"evidence_ids": [EVIDENCE_ONE_ID, EVIDENCE_TWO_ID]}
    )

    report = _map(
        (prepared, source, document, [chunk], [first_evidence, second_evidence], [relation])
    )

    assert all(item.status is GoldMappingStatus.mapped for item in report.span_mappings)
    assert report.span_mappings[0].document_intervals == report.span_mappings[1].document_intervals
    assert report.span_mappings[0].evidence_ids != report.span_mappings[1].evidence_ids
    assert report.path_mappings[0].status is GoldMappingStatus.unmapped


def test_gold_path_does_not_coalesce_unmapped_claim_groups() -> None:
    prepared, source, document, chunks, _evidence, relations = _fixture(
        include_path=True, claim_groups=("claim-1", "claim-2")
    )

    report = _map((prepared, source, document, chunks, [], relations))

    assert all(
        item.status is GoldMappingStatus.missing_evidence for item in report.span_mappings
    )
    assert report.path_mappings[0].status is GoldMappingStatus.unmapped
    assert "does not identify exactly one reviewed source span" in (
        report.path_mappings[0].reason or ""
    )


def test_gold_path_still_requires_present_relation_and_evidence_intersection() -> None:
    fixture = _fixture(
        overlap=False, include_path=True, claim_groups=("claim-1", "claim-2")
    )
    report_without_relation = _map((*fixture[:5], []))
    assert report_without_relation.path_mappings[0].status is GoldMappingStatus.unmapped
    assert "is absent from generation" in (
        report_without_relation.path_mappings[0].reason or ""
    )

    prepared, source, document, chunks, evidence, relations = fixture
    relation = relations[0].model_copy(update={"evidence_ids": [EVIDENCE_TWO_ID]})
    report_without_intersection = _map(
        (prepared, source, document, chunks, evidence, [relation])
    )
    assert report_without_intersection.path_mappings[0].status is GoldMappingStatus.unmapped
    assert "does not close over mapped Evidence" in (
        report_without_intersection.path_mappings[0].reason or ""
    )


def test_gold_mapping_never_uses_parent_chunks() -> None:
    case, source, document, chunks, evidence, relations = _fixture()
    parent_chunks = [chunk.model_copy(update={"is_leaf": False}) for chunk in chunks]

    report = _map((case, source, document, parent_chunks, evidence, relations))

    assert not report.ready
    assert report.span_mappings[0].status is GoldMappingStatus.unmapped


def test_gold_mapping_rejects_wrong_hash_path_locator_and_quote() -> None:
    variants = (
        {"raw_hash": "a" * 64},
        {
            "source_path": "docs/missing.md",
            "locator": MarkdownLocator(
                kind="markdown", path="docs/missing.md", line_start=1, line_end=1
            ),
        },
        {
            "locator": MarkdownLocator(
                kind="markdown", path="docs/guide.md", line_start=2, line_end=2
            )
        },
        {"quote": "text that does not occur in the source"},
    )

    for updates in variants:
        prepared, source, document, chunks, evidence, relations = _fixture()
        gold = prepared.case.gold[0].model_copy(update=updates)
        case = prepared.case.model_copy(update={"gold": [gold]})
        prepared = prepared.model_copy(update={"case": case})
        report = _map((prepared, source, document, chunks, evidence, relations))

        assert not report.ready
        assert report.span_mappings[0].status is GoldMappingStatus.unmapped


def test_gold_mapping_identity_is_derived_from_generation_manifest() -> None:
    case, source, document, chunks, evidence, relations = _fixture()
    generation = _generation_for(document, chunks, evidence, relations)
    report = map_gold_to_generation(
        [case],
        [source],
        [document],
        chunks,
        evidence,
        relations,
        generation=generation,
        dataset_sha256="b" * 64,
    )

    assert report.generation_id == generation.id
    assert report.generation_sha256 == generation.index_manifest["generation_hash"]


def test_gold_mapping_rejects_evidence_not_in_the_generation_manifest() -> None:
    case, source, document, chunks, evidence, relations = _fixture()
    generation = _generation_for(document, chunks, evidence, relations)

    with pytest.raises(GenerationBindingError, match="membership"):
        map_gold_to_generation(
            [case],
            [source],
            [document],
            chunks,
            evidence[:-1],
            relations,
            generation=generation,
            dataset_sha256="b" * 64,
        )


def test_repeated_quote_in_same_locator_is_ambiguous() -> None:
    report = _map(_fixture("accepted evidence. and accepted evidence.", overlap=False))
    assert not report.ready
    assert report.span_mappings[0].status is GoldMappingStatus.ambiguous
    assert "multiple source intervals" in (report.span_mappings[0].reason or "")


def test_gold_mapping_requires_generation_evidence() -> None:
    report = _map(_fixture(include_evidence=False))
    assert not report.ready
    assert report.span_mappings[0].status is GoldMappingStatus.missing_evidence


def test_mapped_path_preserves_direction_and_closes_over_gold_evidence() -> None:
    prepared, _source, _document, _chunks, evidence, relations = _fixture(include_path=True)
    report = _map((prepared, _source, _document, _chunks, evidence, relations))
    assert report.ready
    path = report.path_mappings[0]
    assert path.status is GoldMappingStatus.mapped
    assert path.relation_ids == (RELATION_ID,)
    assert path.traversed_reverse == (False,)
    actual_path = Path(
        id="00000000-0000-4000-8000-000000000013",
        entity_ids=[SUBJECT_ID, OBJECT_ID],
        steps=[
            PathStep(
                relation_id=RELATION_ID,
                from_id=SUBJECT_ID,
                to_id=OBJECT_ID,
                traversed_reverse=False,
                evidence_ids=[item.id for item in evidence],
            )
        ],
    )
    measurements = compute_query_measurements(
        prepared.case,
        report,
        ranked_chunk_ids=[CHUNK_ONE_ID],
        retrieved_paths=[actual_path],
        citations=[CitationJudgment(supports_claim=True, resolves_to_original=True)],
        citation_count=1,
        abstained=False,
        answer_correctness=2,
        human_reviewer_id="blind-reviewer-1",
        human_reviewed_at=NOW,
    )
    assert measurements.evidence_recall == 1.0
    assert measurements.gold_evidence_group_recall_at_k[5] == 1.0
    assert measurements.strict_chunk_recall_at_k[5] == 0.5
    assert measurements.complete_path_accuracy == 1.0
    assert measurements.gold_path_coverage == 1.0


def test_txt_quote_must_be_inside_declared_source_lines_even_in_a_broad_leaf() -> None:
    from knowledge_weaver.evaluation.gold_mapping import _span_candidates

    text = "accepted evidence.\f\naccepted evidence."
    prepared, source, document, chunks, evidence, _ = _fixture(text, overlap=False)
    path = "docs/guide.txt"
    document = document.model_copy(update={"relative_path": path})
    source = source.model_copy(update={"source_path": path})
    locator = MarkdownLocator(path=path, line_start=1, line_end=3)
    chunk = chunks[0].model_copy(update={
        "spans": [chunks[0].spans[0].model_copy(update={"locator": locator})],
    })
    proof = evidence[0].model_copy(update={"locator": locator})

    def locate(line: int) -> set[tuple[str, int, int]]:
        gold = prepared.case.gold[0].model_copy(update={
            "source_path": path,
            "locator": MarkdownLocator(path=path, line_start=line, line_end=line),
        })
        case = prepared.model_copy(update={"case": prepared.case.model_copy(update={"gold": [gold]})})
        candidates, intervals = _span_candidates(
            case,
            0,
            source_assets={source.id: source},
            documents={document.id: document},
            chunks_by_document={document.id: [chunk]},
            evidence_by_chunk={chunk.id: [proof]},
        )
        assert all(candidate.evidence_ids == (proof.id,) for candidate in candidates)
        return intervals

    quote = "accepted evidence."
    last = text.rindex(quote)
    assert locate(1) == {(document.id, 0, len(quote))}
    assert locate(2) == set()
    assert locate(3) == {(document.id, last, last + len(quote))}
