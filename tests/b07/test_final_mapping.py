from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import pytest

from knowledge_weaver.evaluation.contracts import (
    GoldEndpointExpectation,
    GoldEvidenceBasis,
    GoldOutcomeKind,
    GoldPathEdge,
    GoldQualifierReviewState,
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
    GoldRelationExpectation,
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
    Entity,
    EntityKind,
    Evidence,
    FileManifestEntry,
    Generation,
    GenerationState,
    Language,
    MarkdownLocator,
    Mention,
    Path,
    PathStep,
    Polarity,
    Provenance,
    ProvenanceMethod,
    Relation,
    RelationPredicate,
    Source,
    SourceKind,
    SourceVersion,
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
RULE_PROVENANCE_ID = "00000000-0000-4000-8000-000000000015"
RUNTIME_RELATION_TWO_ID = "00000000-0000-4000-8000-000000000016"
RUNTIME_RELATION_THREE_ID = "00000000-0000-4000-8000-000000000017"
OTHER_SUBJECT_ID = "00000000-0000-4000-8000-000000000018"
GENERATION_CORPUS_ID = "00000000-0000-4000-8000-000000000001"
DATA_RELATION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SOURCE_VERSION_ID = "00000000-0000-4000-8000-000000000013"
SOURCE_URI = "fixture://knowledge-weaver/docs/guide.md"
FIXTURE_TEXT = "Config.get_loop_factory returns None loop factory; prefix: accepted evidence. suffix"


def _fixture(
    text: str = FIXTURE_TEXT,
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
    *,
    mentions: list[Mention] | None = None,
    provenance: list[Provenance] | None = None,
) -> Generation:
    members = {
        "source_version": ["00000000-0000-4000-8000-000000000013"],
        "document": [document.id],
        "chunk": [item.id for item in chunks],
        "evidence": [item.id for item in evidence],
        "provenance": sorted(
            {item.id for item in provenance}
            if provenance is not None
            else {item.provenance_id for item in evidence}
        ),
        "entity": sorted(
            {entity_id for item in relations for entity_id in (item.subject_id, item.object_id)}
        ),
        "mention": sorted(item.id for item in mentions or []),
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
        corpus_id=GENERATION_CORPUS_ID,
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


def _with_gold_relation_id(
    prepared: PreparedBenchmarkCase, relation_id: str
) -> PreparedBenchmarkCase:
    edge = prepared.gold_paths[0].edges[0].model_copy(update={"relation_id": relation_id})
    return _with_gold_path_edges(prepared, [edge])


def _with_gold_path_edges(
    prepared: PreparedBenchmarkCase, edges: list[GoldPathEdge]
) -> PreparedBenchmarkCase:
    case = prepared.case.model_copy(
        update={"gold_paths": [[edge.relation_id for edge in edges]]}
    )
    path = prepared.gold_paths[0].model_copy(update={"edges": edges})
    paths = [path]
    review_digest = human_review_digest(
        case,
        prepared.source_ids,
        prepared.gold_quote_sha256,
        prepared.human_review.reviewer_id,
        prepared.human_review.reviewed_at,
        prepared.human_review.checklist,
        paths,
        prepared.source_record_sha256,
    )
    review = prepared.human_review.model_copy(update={"reviewed_record_sha256": review_digest})
    return prepared.model_copy(
        update={"case": case, "gold_paths": paths, "human_review": review}
    )


def _with_gold_quotes(
    prepared: PreparedBenchmarkCase, quotes: list[str]
) -> PreparedBenchmarkCase:
    template = prepared.case.gold[0]
    gold = [
        template.model_copy(update={"quote": quote, "claim_group": f"quote-{index}"})
        for index, quote in enumerate(quotes)
    ]
    case = prepared.case.model_copy(update={"gold": gold})
    quote_hashes = [hashlib.sha256(quote.encode("utf-8")).hexdigest() for quote in quotes]
    updated = prepared.model_copy(
        update={"case": case, "gold_quote_sha256": quote_hashes}
    )
    edge = prepared.gold_paths[0].edges[0].model_copy(
        update={"evidence_quote_sha256": quote_hashes}
    )
    return _with_gold_path_edges(updated, [edge])


def _map_with_expectation(
    prepared: PreparedBenchmarkCase,
    source: SourceAsset,
    document: Document,
    chunks: list[Chunk],
    evidence: list[Evidence],
    relations: list[Relation],
    entities: list[Entity],
    expectation: GoldRelationExpectation,
):
    semantic_rows = _semantic_kwargs(
        source,
        document,
        evidence,
        entities,
        expectation.evidence_basis,
    )
    generation = _generation_for(
        document,
        chunks,
        evidence,
        relations,
        mentions=semantic_rows["mentions"],
        provenance=semantic_rows["provenance"],
    )
    return map_gold_to_generation(
        [prepared],
        [source],
        [document],
        chunks,
        evidence,
        relations,
        entities=entities,
        **semantic_rows,
        relation_expectations={expectation.relation_id: expectation},
        generation=generation,
        dataset_sha256="a" * 64,
    )


def _expected_relation_entities() -> list[Entity]:
    return [
        Entity(
            id=SUBJECT_ID,
            corpus_id=GENERATION_CORPUS_ID,
            kind=EntityKind.concept,
            canonical_name="Config.get_loop_factory",
            aliases=["Config.get_loop_factory"],
            namespace="uvicorn",
            identity_key="uvicorn:get_loop_factory",
        ),
        Entity(
            id=OBJECT_ID,
            corpus_id=GENERATION_CORPUS_ID,
            kind=EntityKind.concept,
            canonical_name="None loop factory",
            aliases=[],
            namespace="uvicorn",
            identity_key="uvicorn:none_loop_factory",
        ),
    ]


def _expected_data_relation() -> GoldRelationExpectation:
    raw_hash = hashlib.sha256(FIXTURE_TEXT.encode("utf-8")).hexdigest()
    return GoldRelationExpectation(
        relation_id=DATA_RELATION_ID,
        predicate=RelationPredicate.USES,
        polarity=Polarity.positive,
        subject=GoldEndpointExpectation(
            source_uri=SOURCE_URI,
            source_version_id=SOURCE_VERSION_ID,
            raw_file_sha256=raw_hash,
            relative_path="docs/guide.md",
            qualified_symbol="Config.get_loop_factory",
            entity_kind=EntityKind.concept,
        ),
        object=GoldEndpointExpectation(
            source_uri=SOURCE_URI,
            source_version_id=SOURCE_VERSION_ID,
            raw_file_sha256=raw_hash,
            relative_path="docs/guide.md",
            qualified_symbol="None loop factory",
            entity_kind=EntityKind.concept,
        ),
        qualifier_review_state=GoldQualifierReviewState.reviewed_empty,
        qualifier_values={},
        result_role="relation_object",
        outcome_kind=GoldOutcomeKind.fact,
        evidence_basis=GoldEvidenceBasis.source_code,
    )


def _semantic_expectation(
    relation_id: str = DATA_RELATION_ID,
    *,
    predicate: RelationPredicate = RelationPredicate.USES,
    subject_symbol: str = "Config.get_loop_factory",
    object_symbol: str = "None loop factory",
    qualifier_review_state: GoldQualifierReviewState = GoldQualifierReviewState.reviewed_empty,
    qualifier_values: dict[str, object] | None = None,
) -> GoldRelationExpectation:
    template = _expected_data_relation()
    return template.model_copy(
        update={
            "relation_id": relation_id,
            "predicate": predicate,
            "subject": template.subject.model_copy(update={"qualified_symbol": subject_symbol}),
            "object": template.object.model_copy(update={"qualified_symbol": object_symbol}),
            "qualifier_review_state": qualifier_review_state,
            "qualifier_values": qualifier_values or {},
        }
    )


def _semantic_kwargs(
    source_asset: SourceAsset,
    document: Document,
    evidence: list[Evidence],
    entities: list[Entity],
    evidence_basis: GoldEvidenceBasis,
) -> dict[str, Any]:
    source = Source(
        id=source_asset.id,
        corpus_id=GENERATION_CORPUS_ID,
        kind=source_asset.kind,
        logical_uri=SOURCE_URI,
        display_name="B07 semantic fixture",
        license="fixture",
        created_at=NOW,
    )
    source_version = SourceVersion(
        id=SOURCE_VERSION_ID,
        source_id=source.id,
        manifest_hash=document.raw_hash,
        revision="fixture-v1",
        dirty=False,
        files=[
            FileManifestEntry(
                relative_path=document.relative_path,
                raw_hash=document.raw_hash,
                document_id=document.id,
            )
        ],
    )
    method = (
        ProvenanceMethod.STATIC_ANALYSIS
        if evidence_basis is GoldEvidenceBasis.source_code
        else ProvenanceMethod.NORMATIVE_SPECIFICATION
    )
    provenance = Provenance(
        id=PROVENANCE_ID,
        method=method,
        producer_fingerprint="b07-semantic-fixture-v1",
        source_id=document.source_id,
        document_id=document.id,
    )
    mentions: list[Mention] = []
    for entity in entities:
        symbol = entity.canonical_name
        short_symbol = symbol.rsplit(".", 1)[-1]
        matching_evidence = next(
            (
                item
                for item in evidence
                if symbol in item.quote or short_symbol in item.quote
            ),
            None,
        )
        if matching_evidence is None:
            continue
        surface = symbol if symbol in matching_evidence.quote else short_symbol
        mentions.append(
            Mention(
                id=str(uuid5(NAMESPACE_URL, f"{entity.id}:{matching_evidence.id}:{surface}")),
                entity_id=entity.id,
                evidence_id=matching_evidence.id,
                surface_form=surface,
            )
        )
    return {
        "mentions": mentions,
        "sources": [source],
        "source_versions": [source_version],
        "provenance": [provenance],
    }


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
    assert "does not close over Evidence for every reviewed Gold quote" in (
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


def test_gold_path_resolves_data_relation_via_semantic_expectation_and_rejects_mismatches() -> None:
    from knowledge_weaver.models.contracts import RelationPredicate

    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    data_rel_id = DATA_RELATION_ID
    updated_prepared = _with_gold_relation_id(prepared, data_rel_id)
    runtime_rel = relations[0].model_copy(update={"predicate": RelationPredicate.USES})
    corpus_id = "00000000-0000-4000-8000-000000000001"
    subj = Entity(
        id=runtime_rel.subject_id,
        corpus_id=corpus_id,
        kind=EntityKind.concept,
        canonical_name="Config.get_loop_factory",
        aliases=["Config.get_loop_factory"],
        namespace="uvicorn",
        identity_key="uvicorn:get_loop_factory",
    )
    obj = Entity(
        id=runtime_rel.object_id,
        corpus_id=corpus_id,
        kind=EntityKind.concept,
        canonical_name="None loop factory",
        aliases=[],
        namespace="uvicorn",
        identity_key="uvicorn:none_loop_factory",
    )
    expectation = _semantic_expectation(data_rel_id)
    semantic_rows = _semantic_kwargs(source, document, evidence, [subj, obj], expectation.evidence_basis)
    gen = _generation_for(
        document,
        chunks,
        evidence,
        [runtime_rel],
        mentions=semantic_rows["mentions"],
        provenance=semantic_rows["provenance"],
    )
    ok_report = map_gold_to_generation(
        [updated_prepared],
        [source],
        [document],
        chunks,
        evidence,
        [runtime_rel],
        entities=[subj, obj],
        **semantic_rows,
        relation_expectations={data_rel_id: expectation},
        generation=gen,
        dataset_sha256="a" * 64,
    )
    assert ok_report.ready
    assert ok_report.path_mappings[0].relation_ids == (runtime_rel.id,)

    wrong_pred = _semantic_expectation(data_rel_id, predicate=RelationPredicate.CALLS)
    bad_report = map_gold_to_generation(
        [updated_prepared],
        [source],
        [document],
        chunks,
        evidence,
        [runtime_rel],
        entities=[subj, obj],
        **semantic_rows,
        relation_expectations={data_rel_id: wrong_pred},
        generation=gen,
        dataset_sha256="a" * 64,
    )
    assert not bad_report.ready
    assert bad_report.path_mappings[0].status is GoldMappingStatus.unmapped


def test_strict_calls_requires_exact_direct_source_evidence_not_function_context() -> None:
    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    prepared = _with_gold_relation_id(prepared, DATA_RELATION_ID)
    call_expectation = _semantic_expectation(predicate=RelationPredicate.CALLS)
    broad_only_relation = relations[0].model_copy(
        update={"predicate": RelationPredicate.CALLS, "evidence_ids": [evidence[0].id]}
    )

    broad_only_report = _map_with_expectation(
        prepared,
        source,
        document,
        chunks,
        evidence,
        [broad_only_relation],
        _expected_relation_entities(),
        call_expectation,
    )

    assert broad_only_report.path_mappings[0].status is GoldMappingStatus.unmapped
    assert "exact direct callsite Evidence" in (broad_only_report.path_mappings[0].reason or "")

    quote = prepared.case.gold[0].quote
    quote_start = document.normalized_text.index(quote)
    direct_evidence = Evidence(
        id=EVIDENCE_TWO_ID,
        chunk_id=CHUNK_ONE_ID,
        start=quote_start,
        end=quote_start + len(quote),
        quote=quote,
        locator=evidence[0].locator,
        provenance_id=PROVENANCE_ID,
    )
    evidence_with_direct = [*evidence, direct_evidence]
    relation_with_direct = broad_only_relation.model_copy(
        update={"evidence_ids": [evidence[0].id, direct_evidence.id]}
    )
    exact_report = _map_with_expectation(
        prepared,
        source,
        document,
        chunks,
        evidence_with_direct,
        [relation_with_direct],
        _expected_relation_entities(),
        call_expectation,
    )

    assert exact_report.ready
    assert exact_report.path_mappings[0].evidence_ids_by_edge == ((direct_evidence.id,),)


def test_direct_gold_relation_id_validates_explicit_semantics() -> None:
    from knowledge_weaver.models.contracts import Polarity, RelationPredicate

    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    prepared = _with_gold_relation_id(prepared, DATA_RELATION_ID)
    runtime_relation = relations[0].model_copy(
        update={
            "id": DATA_RELATION_ID,
            "predicate": RelationPredicate.USES,
            "subject_id": SUBJECT_ID,
            "object_id": OBJECT_ID,
            "polarity": Polarity.positive,
            "qualifiers": {},
            "evidence_ids": [EVIDENCE_ONE_ID],
        }
    )
    entities = _expected_relation_entities()
    expectation = _expected_data_relation()

    report = _map_with_expectation(
        prepared,
        source,
        document,
        chunks,
        evidence,
        [runtime_relation],
        entities,
        expectation,
    )

    assert report.ready
    assert report.path_mappings[0].relation_ids == (DATA_RELATION_ID,)


def test_direct_gold_relation_id_rejects_generation_bound_wrong_entity_identity() -> None:
    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    prepared = _with_gold_relation_id(prepared, DATA_RELATION_ID)
    runtime_relation = relations[0].model_copy(
        update={
            "id": DATA_RELATION_ID,
            "predicate": RelationPredicate.USES,
            "subject_id": SUBJECT_ID,
            "object_id": OBJECT_ID,
            "polarity": Polarity.positive,
            "evidence_ids": [EVIDENCE_ONE_ID],
        }
    )
    subject, obj = _expected_relation_entities()
    wrong_subject = subject.model_copy(
        update={
            "canonical_name": "unrelated factory",
            "aliases": [],
            "identity_key": "uvicorn:unrelated_factory",
        }
    )
    wrong_obj = obj.model_copy(
        update={
            "canonical_name": "different loop implementation",
            "identity_key": "uvicorn:different_loop_implementation",
        }
    )
    expectation = _expected_data_relation()
    semantic_rows = _semantic_kwargs(
        source,
        document,
        evidence,
        [wrong_subject, wrong_obj],
        expectation.evidence_basis,
    )
    generation = _generation_for(
        document,
        chunks,
        evidence,
        [runtime_relation],
        mentions=semantic_rows["mentions"],
        provenance=semantic_rows["provenance"],
    )

    assert {SUBJECT_ID, OBJECT_ID}.issubset(generation.entity_ids)
    report = map_gold_to_generation(
        [prepared],
        [source],
        [document],
        chunks,
        evidence,
        [runtime_relation],
        entities=[wrong_subject, wrong_obj],
        **semantic_rows,
        relation_expectations={DATA_RELATION_ID: expectation},
        generation=generation,
        dataset_sha256="a" * 64,
    )

    assert not report.ready
    assert report.path_mappings[0].status is GoldMappingStatus.unmapped
    assert "no candidate matching exact source-bound endpoints" in (
        report.path_mappings[0].reason or ""
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_uri", "fixture://another-source"),
        ("source_version_id", "00000000-0000-4000-8000-000000000999"),
        ("raw_file_sha256", "b" * 64),
        ("relative_path", "docs/other.md"),
        ("entity_kind", EntityKind.function),
    ],
)
def test_semantic_fallback_requires_exact_source_version_and_endpoint_binding(
    field: str, value: object
) -> None:
    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    prepared = _with_gold_relation_id(prepared, DATA_RELATION_ID)
    runtime_relation = relations[0].model_copy(update={"predicate": RelationPredicate.USES})
    expectation = _expected_data_relation()
    wrong_subject = expectation.subject.model_copy(update={field: value})
    wrong_expectation = expectation.model_copy(update={"subject": wrong_subject})

    report = _map_with_expectation(
        prepared,
        source,
        document,
        chunks,
        evidence,
        [runtime_relation],
        _expected_relation_entities(),
        wrong_expectation,
    )

    assert report.path_mappings[0].status is GoldMappingStatus.unmapped
    assert "no candidate matching exact source-bound endpoints" in (
        report.path_mappings[0].reason or ""
    )


@pytest.mark.parametrize("premise_evidence_id", [EVIDENCE_ONE_ID, EVIDENCE_TWO_ID])
def test_rule_inferred_semantic_fallback_requires_source_bound_generation_premises(
    premise_evidence_id: str,
) -> None:
    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    prepared = _with_gold_relation_id(prepared, DATA_RELATION_ID)
    subject, obj = _expected_relation_entities()
    candidate = relations[0].model_copy(
        update={
            "predicate": RelationPredicate.USES,
            "provenance_id": RULE_PROVENANCE_ID,
            "evidence_ids": [EVIDENCE_ONE_ID],
        }
    )
    premise = Relation(
        id=RUNTIME_RELATION_TWO_ID,
        subject_id=SUBJECT_ID,
        predicate=RelationPredicate.CALLS,
        object_id=OBJECT_ID,
        polarity=Polarity.positive,
        provenance_id=PROVENANCE_ID,
        evidence_ids=[premise_evidence_id],
    )
    expectation = _expected_data_relation()
    semantic_rows = _semantic_kwargs(
        source,
        document,
        evidence,
        [subject, obj],
        expectation.evidence_basis,
    )
    rule_provenance = Provenance(
        id=RULE_PROVENANCE_ID,
        method=ProvenanceMethod.RULE_INFERRED,
        producer_fingerprint="b07-rule-fixture-v1",
        source_id=document.source_id,
        document_id=document.id,
        premise_relation_ids=[premise.id],
        rule_id="fixture-rule-v1",
    )
    semantic_rows["provenance"].append(rule_provenance)
    generation = _generation_for(
        document,
        chunks,
        evidence,
        [candidate, premise],
        mentions=semantic_rows["mentions"],
        provenance=semantic_rows["provenance"],
    )

    report = map_gold_to_generation(
        [prepared],
        [source],
        [document],
        chunks,
        evidence,
        [candidate, premise],
        entities=[subject, obj],
        **semantic_rows,
        relation_expectations={DATA_RELATION_ID: expectation},
        generation=generation,
        dataset_sha256="a" * 64,
    )

    if premise_evidence_id == EVIDENCE_ONE_ID:
        assert report.path_mappings[0].status is GoldMappingStatus.mapped
    else:
        assert report.path_mappings[0].status is GoldMappingStatus.unmapped
        assert "expected Generation Provenance chain" in (
            report.path_mappings[0].reason or ""
        )


@pytest.mark.parametrize(
    ("contradiction", "reason"),
    [
        ("predicate", "expected predicate"),
        ("polarity", "expected polarity"),
        ("ordered_endpoints", "endpoint direction mismatch"),
        ("qualifiers", "exact reviewed qualifier set"),
        ("evidence", "does not close over Evidence for every reviewed Gold quote"),
    ],
)
def test_direct_gold_relation_id_contradiction_does_not_fall_back(
    contradiction: str, reason: str
) -> None:
    from knowledge_weaver.models.contracts import Polarity, RelationPredicate

    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    prepared = _with_gold_relation_id(prepared, DATA_RELATION_ID)
    valid_alternative = relations[0].model_copy(
        update={
            "id": RUNTIME_RELATION_TWO_ID,
            "predicate": RelationPredicate.USES,
            "subject_id": SUBJECT_ID,
            "object_id": OBJECT_ID,
            "polarity": Polarity.positive,
            "qualifiers": {},
            "evidence_ids": [EVIDENCE_ONE_ID],
        }
    )
    contradiction_updates = {
        "predicate": {"predicate": RelationPredicate.CALLS},
        "polarity": {"polarity": Polarity.negative},
        "ordered_endpoints": {"subject_id": OBJECT_ID, "object_id": SUBJECT_ID},
        "qualifiers": {"qualifiers": {"modality": "RECOMMENDED"}},
        "evidence": {"evidence_ids": [EVIDENCE_TWO_ID]},
    }[contradiction]
    direct_relation = valid_alternative.model_copy(
        update={"id": DATA_RELATION_ID, **contradiction_updates}
    )
    entities = _expected_relation_entities()
    expectation = _expected_data_relation()

    report = _map_with_expectation(
        prepared,
        source,
        document,
        chunks,
        evidence,
        [direct_relation, valid_alternative],
        entities,
        expectation,
    )

    assert not report.ready
    assert report.path_mappings[0].status is GoldMappingStatus.unmapped
    assert report.path_mappings[0].relation_ids == ()
    assert reason in (report.path_mappings[0].reason or "")


def test_direct_gold_relation_expectation_requires_generation_entity_binding() -> None:
    from knowledge_weaver.models.contracts import Polarity, RelationPredicate

    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    prepared = _with_gold_relation_id(prepared, DATA_RELATION_ID)
    direct_relation = relations[0].model_copy(
        update={
            "id": DATA_RELATION_ID,
            "predicate": RelationPredicate.USES,
            "subject_id": SUBJECT_ID,
            "object_id": OBJECT_ID,
            "polarity": Polarity.positive,
        }
    )
    entities = _expected_relation_entities()
    expectation = _expected_data_relation()

    with pytest.raises(GenerationBindingError, match="membership"):
        _map_with_expectation(
            prepared,
            source,
            document,
            chunks,
            evidence,
            [direct_relation],
            entities[1:],
            expectation,
        )


@pytest.mark.parametrize("row_type", ["mentions", "provenance"])
def test_semantic_expectation_requires_mention_and_provenance_generation_membership(
    row_type: str,
) -> None:
    from knowledge_weaver.models.contracts import RelationPredicate

    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    prepared = _with_gold_relation_id(prepared, DATA_RELATION_ID)
    runtime_relation = relations[0].model_copy(update={"predicate": RelationPredicate.USES})
    entities = _expected_relation_entities()
    expectation = _expected_data_relation()
    semantic_rows = _semantic_kwargs(
        source, document, evidence, entities, expectation.evidence_basis
    )
    generation = _generation_for(
        document,
        chunks,
        evidence,
        [runtime_relation],
        mentions=semantic_rows["mentions"],
        provenance=semantic_rows["provenance"],
    )
    incomplete_rows = dict(semantic_rows)
    incomplete_rows[row_type] = []

    with pytest.raises(GenerationBindingError, match="membership"):
        map_gold_to_generation(
            [prepared],
            [source],
            [document],
            chunks,
            evidence,
            [runtime_relation],
            entities=entities,
            **incomplete_rows,
            relation_expectations={DATA_RELATION_ID: expectation},
            generation=generation,
            dataset_sha256="a" * 64,
        )


def test_gold_path_fallback_does_not_close_on_adjacent_chunk_evidence_or_pick_first_id() -> None:
    from knowledge_weaver.models.contracts import Polarity, RelationPredicate

    prepared, source, document, chunks, evidence, _relations = _fixture(
        overlap=False, include_path=True
    )
    prepared = _with_gold_relation_id(prepared, DATA_RELATION_ID)
    adjacent_evidence = evidence[0].model_copy(
        update={"id": EVIDENCE_TWO_ID, "start": 0, "end": 7, "quote": "prefix:"}
    )
    evidence = [*evidence, adjacent_evidence]
    candidate_one = Relation(
        id=RELATION_ID,
        subject_id=SUBJECT_ID,
        predicate=RelationPredicate.USES,
        object_id=OBJECT_ID,
        polarity=Polarity.positive,
        provenance_id=PROVENANCE_ID,
        evidence_ids=[EVIDENCE_TWO_ID],
    )
    candidate_two = candidate_one.model_copy(update={"id": RUNTIME_RELATION_TWO_ID})
    subject = Entity(
        id=SUBJECT_ID,
        corpus_id="00000000-0000-4000-8000-000000000001",
        kind=EntityKind.concept,
        canonical_name="Config.get_loop_factory",
        aliases=["Config.get_loop_factory"],
        namespace="uvicorn",
        identity_key="uvicorn:get_loop_factory",
    )
    obj = Entity(
        id=OBJECT_ID,
        corpus_id="00000000-0000-4000-8000-000000000001",
        kind=EntityKind.concept,
        canonical_name="None loop factory",
        aliases=[],
        namespace="uvicorn",
        identity_key="uvicorn:none_loop_factory",
    )
    expectation = _semantic_expectation()

    report = _map_with_expectation(
        prepared,
        source,
        document,
        chunks,
        evidence,
        [candidate_one, candidate_two],
        [subject, obj],
        expectation,
    )

    assert not report.ready
    assert report.span_mappings[0].evidence_ids == (EVIDENCE_ONE_ID,)
    assert report.path_mappings[0].status is GoldMappingStatus.unmapped
    assert "does not close over Evidence for every reviewed Gold quote" in (
        report.path_mappings[0].reason or ""
    )


def test_gold_path_fallback_rejects_multiple_semantic_candidates() -> None:
    from knowledge_weaver.models.contracts import RelationPredicate

    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    prepared = _with_gold_relation_id(prepared, DATA_RELATION_ID)
    candidate_one = relations[0].model_copy(
        update={"id": RELATION_ID, "predicate": RelationPredicate.USES}
    )
    candidate_two = candidate_one.model_copy(update={"id": RUNTIME_RELATION_TWO_ID})
    subject = Entity(
        id=SUBJECT_ID,
        corpus_id="00000000-0000-4000-8000-000000000001",
        kind=EntityKind.concept,
        canonical_name="Config.get_loop_factory",
        aliases=["Config.get_loop_factory"],
        namespace="uvicorn",
        identity_key="uvicorn:get_loop_factory",
    )
    obj = Entity(
        id=OBJECT_ID,
        corpus_id="00000000-0000-4000-8000-000000000001",
        kind=EntityKind.concept,
        canonical_name="None loop factory",
        aliases=[],
        namespace="uvicorn",
        identity_key="uvicorn:none_loop_factory",
    )
    expectation = _semantic_expectation()

    report = _map_with_expectation(
        prepared,
        source,
        document,
        chunks,
        evidence,
        [candidate_one, candidate_two],
        [subject, obj],
        expectation,
    )

    assert report.path_mappings[0].status is GoldMappingStatus.unmapped
    assert "has multiple semantic candidates" in (report.path_mappings[0].reason or "")


@pytest.mark.parametrize(
    ("expected_subject", "runtime_subject"),
    [
        ("Config.get_loop_factory", "Config.get_loop_factory_v2"),
        ("exp", "iat"),
    ],
)
def test_gold_path_fallback_rejects_prefix_and_synonym_endpoint_matches(
    expected_subject: str, runtime_subject: str
) -> None:
    from knowledge_weaver.models.contracts import RelationPredicate

    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    prepared = _with_gold_relation_id(prepared, DATA_RELATION_ID)
    candidate = relations[0].model_copy(update={"predicate": RelationPredicate.USES})
    subject = Entity(
        id=SUBJECT_ID,
        corpus_id="00000000-0000-4000-8000-000000000001",
        kind=EntityKind.concept,
        canonical_name=runtime_subject,
        aliases=[],
        namespace="uvicorn",
        identity_key=f"uvicorn:{runtime_subject}",
    )
    obj = Entity(
        id=OBJECT_ID,
        corpus_id="00000000-0000-4000-8000-000000000001",
        kind=EntityKind.concept,
        canonical_name="None loop factory",
        aliases=[],
        namespace="uvicorn",
        identity_key="uvicorn:none_loop_factory",
    )
    expectation = _semantic_expectation(subject_symbol=expected_subject)

    report = _map_with_expectation(
        prepared,
        source,
        document,
        chunks,
        evidence,
        [candidate],
        [subject, obj],
        expectation,
    )

    assert report.path_mappings[0].status is GoldMappingStatus.unmapped
    assert "no candidate matching exact source-bound endpoints" in (report.path_mappings[0].reason or "")


def test_gold_path_fallback_requires_unique_registered_endpoint_identity() -> None:
    from knowledge_weaver.models.contracts import RelationPredicate

    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    prepared = _with_gold_relation_id(prepared, DATA_RELATION_ID)
    candidate = relations[0].model_copy(update={"predicate": RelationPredicate.USES})
    other_entity_relation = candidate.model_copy(
        update={
            "id": RUNTIME_RELATION_TWO_ID,
            "subject_id": OTHER_SUBJECT_ID,
            "predicate": RelationPredicate.CALLS,
        }
    )
    subject = Entity(
        id=SUBJECT_ID,
        corpus_id="00000000-0000-4000-8000-000000000001",
        kind=EntityKind.concept,
        canonical_name="Config.get_loop_factory",
        aliases=["Config.get_loop_factory"],
        namespace="uvicorn",
        identity_key="uvicorn:get_loop_factory",
    )
    duplicate_subject = Entity(
        id=OTHER_SUBJECT_ID,
        corpus_id="00000000-0000-4000-8000-000000000001",
        kind=EntityKind.concept,
        canonical_name="Config.get_loop_factory",
        aliases=["Config.get_loop_factory"],
        namespace="uvicorn",
        identity_key="uvicorn:get_loop_factory",
    )
    obj = Entity(
        id=OBJECT_ID,
        corpus_id="00000000-0000-4000-8000-000000000001",
        kind=EntityKind.concept,
        canonical_name="None loop factory",
        aliases=[],
        namespace="uvicorn",
        identity_key="uvicorn:none_loop_factory",
    )
    expectation = _semantic_expectation()

    report = _map_with_expectation(
        prepared,
        source,
        document,
        chunks,
        evidence,
        [candidate, other_entity_relation],
        [subject, duplicate_subject, obj],
        expectation,
    )

    assert report.path_mappings[0].status is GoldMappingStatus.unmapped
    assert "no candidate matching exact source-bound endpoints" in (report.path_mappings[0].reason or "")


@pytest.mark.parametrize(
    ("qualifiers", "reverse_endpoints", "review_state", "reason"),
    [
        (
            {"modality": "RECOMMENDED"},
            False,
            GoldQualifierReviewState.reviewed_empty,
            "exact reviewed qualifier set",
        ),
        (
            {},
            True,
            GoldQualifierReviewState.reviewed_empty,
            "endpoint direction mismatch",
        ),
        (
            {},
            False,
            GoldQualifierReviewState.unreviewed,
            "UNREVIEWED",
        ),
    ],
)
def test_gold_path_fallback_blocks_unresolved_qualifier_or_direction(
    qualifiers: dict[str, str],
    reverse_endpoints: bool,
    review_state: GoldQualifierReviewState,
    reason: str,
) -> None:
    from knowledge_weaver.models.contracts import RelationPredicate

    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    prepared = _with_gold_relation_id(prepared, DATA_RELATION_ID)
    candidate = relations[0].model_copy(
        update={
            "predicate": RelationPredicate.USES,
            "subject_id": OBJECT_ID if reverse_endpoints else SUBJECT_ID,
            "object_id": SUBJECT_ID if reverse_endpoints else OBJECT_ID,
            "qualifiers": qualifiers,
        }
    )
    subject = Entity(
        id=SUBJECT_ID,
        corpus_id="00000000-0000-4000-8000-000000000001",
        kind=EntityKind.concept,
        canonical_name="Config.get_loop_factory",
        aliases=["Config.get_loop_factory"],
        namespace="uvicorn",
        identity_key="uvicorn:get_loop_factory",
    )
    obj = Entity(
        id=OBJECT_ID,
        corpus_id="00000000-0000-4000-8000-000000000001",
        kind=EntityKind.concept,
        canonical_name="None loop factory",
        aliases=[],
        namespace="uvicorn",
        identity_key="uvicorn:none_loop_factory",
    )
    expectation = _semantic_expectation(qualifier_review_state=review_state)

    report = _map_with_expectation(
        prepared,
        source,
        document,
        chunks,
        evidence,
        [candidate],
        [subject, obj],
        expectation,
    )

    assert report.path_mappings[0].status is GoldMappingStatus.unmapped
    assert reason in (report.path_mappings[0].reason or "")


@pytest.mark.parametrize(
    ("second_subject_id", "second_object_id", "second_reverse", "mapped"),
    [
        (OTHER_SUBJECT_ID, OBJECT_ID, True, True),
        (SUBJECT_ID, OTHER_SUBJECT_ID, False, False),
    ],
)
def test_gold_path_requires_contiguous_directional_endpoints(
    second_subject_id: str,
    second_object_id: str,
    second_reverse: bool,
    mapped: bool,
) -> None:
    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    first_edge = prepared.gold_paths[0].edges[0]
    second_relation = relations[0].model_copy(
        update={
            "id": RUNTIME_RELATION_TWO_ID,
            "subject_id": second_subject_id,
            "object_id": second_object_id,
        }
    )
    second_edge = first_edge.model_copy(
        update={"relation_id": RUNTIME_RELATION_TWO_ID, "traversed_reverse": second_reverse}
    )
    prepared = _with_gold_path_edges(prepared, [first_edge, second_edge])

    report = _map((prepared, source, document, chunks, evidence, [*relations, second_relation]))

    path = report.path_mappings[0]
    if mapped:
        assert path.status is GoldMappingStatus.mapped
        assert path.traversed_reverse == (False, True)
    else:
        assert path.status is GoldMappingStatus.unmapped
        assert "Gold path is discontinuous between edges 0 and 1" in (path.reason or "")


def test_each_gold_edge_quote_needs_relation_evidence_closure() -> None:
    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    quotes = ["accepted evidence.", "suffix"]
    prepared = _with_gold_quotes(prepared, quotes)
    chunk = chunks[0]
    quote_evidence = [
        evidence[0].model_copy(
            update={
                "start": chunk.text.index(quote),
                "end": chunk.text.index(quote) + len(quote),
                "quote": quote,
                "id": EVIDENCE_ONE_ID if index == 0 else EVIDENCE_TWO_ID,
            }
        )
        for index, quote in enumerate(quotes)
    ]
    relation = relations[0].model_copy(update={"evidence_ids": [EVIDENCE_ONE_ID]})

    report = _map((prepared, source, document, chunks, quote_evidence, [relation]))

    assert [item.evidence_ids for item in report.span_mappings] == [
        (EVIDENCE_ONE_ID,),
        (EVIDENCE_TWO_ID,),
    ]
    assert report.path_mappings[0].status is GoldMappingStatus.unmapped
    assert "does not close over Evidence for every reviewed Gold quote" in (
        report.path_mappings[0].reason or ""
    )


def test_semantic_fallback_requires_exact_generation_entity_rows() -> None:
    from knowledge_weaver.models.contracts import RelationPredicate

    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    prepared = _with_gold_relation_id(prepared, DATA_RELATION_ID)
    candidate = relations[0].model_copy(update={"predicate": RelationPredicate.USES})
    subject = Entity(
        id=SUBJECT_ID,
        corpus_id=GENERATION_CORPUS_ID,
        kind=EntityKind.concept,
        canonical_name="Config.get_loop_factory",
        aliases=["Config.get_loop_factory"],
        namespace="uvicorn",
        identity_key="uvicorn:get_loop_factory",
    )
    obj = Entity(
        id=OBJECT_ID,
        corpus_id=GENERATION_CORPUS_ID,
        kind=EntityKind.concept,
        canonical_name="None loop factory",
        aliases=[],
        namespace="uvicorn",
        identity_key="uvicorn:none_loop_factory",
    )
    rogue = Entity(
        id=RUNTIME_RELATION_THREE_ID,
        corpus_id=GENERATION_CORPUS_ID,
        kind=EntityKind.concept,
        canonical_name="unrelated entity",
        aliases=[],
        namespace="uvicorn",
        identity_key="uvicorn:unrelated_entity",
    )
    expectation = _semantic_expectation()

    for entity_rows in ([subject], [subject, obj, rogue]):
        with pytest.raises(GenerationBindingError, match="membership"):
            _map_with_expectation(
                prepared,
                source,
                document,
                chunks,
                evidence,
                [candidate],
                entity_rows,
                expectation,
            )

    wrong_corpus_subject = subject.model_copy(
        update={"corpus_id": "00000000-0000-4000-8000-000000000014"}
    )
    with pytest.raises(GenerationBindingError, match="Entity corpus"):
        _map_with_expectation(
            prepared,
            source,
            document,
            chunks,
            evidence,
            [candidate],
            [wrong_corpus_subject, obj],
            expectation,
        )


def test_semantic_fallback_rejects_duplicate_entity_identity_keys() -> None:
    from knowledge_weaver.models.contracts import RelationPredicate

    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    prepared = _with_gold_relation_id(prepared, DATA_RELATION_ID)
    candidate = relations[0].model_copy(update={"predicate": RelationPredicate.USES})
    subject = Entity(
        id=SUBJECT_ID,
        corpus_id=GENERATION_CORPUS_ID,
        kind=EntityKind.concept,
        canonical_name="Config.get_loop_factory",
        aliases=["Config.get_loop_factory"],
        namespace="uvicorn",
        identity_key="uvicorn:get_loop_factory",
    )
    duplicate_identity = Entity(
        id=OTHER_SUBJECT_ID,
        corpus_id=GENERATION_CORPUS_ID,
        kind=EntityKind.concept,
        canonical_name="another factory",
        aliases=[],
        namespace="uvicorn",
        identity_key="uvicorn:get_loop_factory",
    )
    obj = Entity(
        id=OBJECT_ID,
        corpus_id=GENERATION_CORPUS_ID,
        kind=EntityKind.concept,
        canonical_name="None loop factory",
        aliases=[],
        namespace="uvicorn",
        identity_key="uvicorn:none_loop_factory",
    )
    other_relation = candidate.model_copy(
        update={
            "id": RUNTIME_RELATION_TWO_ID,
            "subject_id": OTHER_SUBJECT_ID,
            "predicate": RelationPredicate.CALLS,
        }
    )
    expectation = _semantic_expectation()

    report = _map_with_expectation(
        prepared,
        source,
        document,
        chunks,
        evidence,
        [candidate, other_relation],
        [subject, duplicate_identity, obj],
        expectation,
    )

    assert report.path_mappings[0].status is GoldMappingStatus.unmapped
    assert "no candidate matching exact source-bound endpoints" in (
        report.path_mappings[0].reason or ""
    )


def test_semantic_fallback_rejects_persisted_non_endpoint_entity_drift() -> None:
    from knowledge_weaver.models.contracts import RelationPredicate

    prepared, source, document, chunks, evidence, relations = _fixture(
        overlap=False, include_path=True
    )
    prepared = _with_gold_relation_id(prepared, DATA_RELATION_ID)
    candidate = relations[0].model_copy(update={"predicate": RelationPredicate.USES})
    other_relation = candidate.model_copy(
        update={
            "id": RUNTIME_RELATION_TWO_ID,
            "subject_id": OTHER_SUBJECT_ID,
            "predicate": RelationPredicate.CALLS,
        }
    )
    subject = Entity(
        id=SUBJECT_ID,
        corpus_id=GENERATION_CORPUS_ID,
        kind=EntityKind.concept,
        canonical_name="Config.get_loop_factory",
        aliases=["Config.get_loop_factory"],
        namespace="uvicorn",
        identity_key="uvicorn:get_loop_factory",
    )
    obj = Entity(
        id=OBJECT_ID,
        corpus_id=GENERATION_CORPUS_ID,
        kind=EntityKind.concept,
        canonical_name="None loop factory",
        aliases=[],
        namespace="uvicorn",
        identity_key="uvicorn:none_loop_factory",
    )
    other_entity = Entity(
        id=OTHER_SUBJECT_ID,
        corpus_id=GENERATION_CORPUS_ID,
        kind=EntityKind.concept,
        canonical_name="unrelated endpoint",
        aliases=[],
        namespace="uvicorn",
        identity_key="uvicorn:unrelated_endpoint",
    )
    entities = [subject, obj, other_entity]
    expectation = _semantic_expectation()
    semantic_rows = _semantic_kwargs(
        source, document, evidence, entities, expectation.evidence_basis
    )
    generation = _generation_for(
        document,
        chunks,
        evidence,
        [candidate, other_relation],
        mentions=semantic_rows["mentions"],
        provenance=semantic_rows["provenance"],
    )
    assert sorted(generation.entity_ids) == sorted(entity.id for entity in entities)
    persisted_ids = [
        RUNTIME_RELATION_THREE_ID if entity_id == OTHER_SUBJECT_ID else entity_id
        for entity_id in generation.entity_ids
    ]
    malformed_generation = generation.model_copy(update={"entity_ids": persisted_ids})
    assert malformed_generation.index_manifest == generation.index_manifest
    assert {SUBJECT_ID, OBJECT_ID}.issubset(persisted_ids)

    with pytest.raises(GenerationBindingError, match="observed Entity IDs differ"):
        map_gold_to_generation(
            [prepared],
            [source],
            [document],
            chunks,
            evidence,
            [candidate, other_relation],
            entities=entities,
            **semantic_rows,
            relation_expectations={DATA_RELATION_ID: expectation},
            generation=malformed_generation,
            dataset_sha256="a" * 64,
        )
