"""Resolve reviewed source spans to source-backed leaf chunks in one generation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from knowledge_weaver.evaluation.contracts import (
    GoldEndpointExpectation,
    GoldEvidenceBasis,
    GoldOutcomeKind,
    GoldQualifierReviewState,
    GoldRelationExpectation,
    PreparedBenchmarkCase,
    SourceAsset,
)
from knowledge_weaver.evaluation.generation_binding import (
    GenerationBindingError,
    verify_generation_manifest,
)
from knowledge_weaver.ingestion.text_coordinates import TextCoordinates
from knowledge_weaver.models.contracts import (
    Chunk,
    Document,
    Entity,
    Evidence,
    Generation,
    MarkdownLocator,
    Mention,
    Provenance,
    ProvenanceMethod,
    Relation,
    RelationPredicate,
    Source,
    SourceVersion,
)


class GoldMappingStatus(StrEnum):
    mapped = "MAPPED"
    unmapped = "UNMAPPED"
    ambiguous = "AMBIGUOUS"
    missing_evidence = "MISSING_GENERATION_EVIDENCE"


@dataclass(frozen=True)
class GoldSpanMapping:
    case_id: str
    gold_index: int
    evidence_group_id: str
    claim_group: str
    grade: int
    quote_sha256: str
    status: GoldMappingStatus
    chunk_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    document_intervals: tuple[tuple[str, int, int], ...] = ()
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


@dataclass(frozen=True)
class GoldPathMapping:
    case_id: str
    path_index: int
    status: GoldMappingStatus
    relation_ids: tuple[str, ...] = ()
    traversed_reverse: tuple[bool, ...] = ()
    evidence_ids_by_edge: tuple[tuple[str, ...], ...] = ()
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


@dataclass(frozen=True)
class GoldMappingReport:
    generation_id: str
    generation_sha256: str
    dataset_sha256: str
    span_mappings: tuple[GoldSpanMapping, ...]
    path_mappings: tuple[GoldPathMapping, ...]
    blockers: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "generation_id": self.generation_id,
            "generation_sha256": self.generation_sha256,
            "dataset_sha256": self.dataset_sha256,
            "span_mappings": [item.to_dict() for item in self.span_mappings],
            "path_mappings": [item.to_dict() for item in self.path_mappings],
            "blockers": list(self.blockers),
            "ready": self.ready,
        }


@dataclass(frozen=True)
class _Candidate:
    chunk_id: str
    evidence_ids: tuple[str, ...]
    document_id: str
    document_start: int
    document_end: int


@dataclass(frozen=True)
class _SemanticProjectionContext:
    generation: Generation
    entities_by_id: Mapping[str, Entity]
    mentions_by_entity: Mapping[str, Sequence[Mention]]
    evidence_by_id: Mapping[str, Evidence]
    chunks_by_id: Mapping[str, Chunk]
    documents_by_id: Mapping[str, Document]
    sources_by_id: Mapping[str, Source]
    source_versions_by_id: Mapping[str, SourceVersion]
    provenance_by_id: Mapping[str, Provenance]
    relations_by_id: Mapping[str, Relation]


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _locator_overlaps(left: object, right: object) -> bool:
    """Conservatively compare frozen locator fields; absent precision never invents overlap."""
    left_data = left.model_dump(mode="json")  # type: ignore[attr-defined]
    right_data = right.model_dump(mode="json")  # type: ignore[attr-defined]
    if left_data.get("kind") != right_data.get("kind"):
        return False

    for key in ("path", "page", "block", "table", "row", "cell", "qualname"):
        a, b = left_data.get(key), right_data.get(key)
        if a is not None and b is not None and a != b:
            return False

    left_start, left_end = left_data.get("line_start"), left_data.get("line_end")
    right_start, right_end = right_data.get("line_start"), right_data.get("line_end")
    if (
        None not in (left_start, left_end, right_start, right_end)
        and max(left_start, right_start) > min(left_end, right_end)
    ):
        return False

    left_col_start, left_col_end = left_data.get("col_start"), left_data.get("col_end")
    right_col_start, right_col_end = right_data.get("col_start"), right_data.get("col_end")
    if (
        None not in (left_col_start, left_col_end, right_col_start, right_col_end)
        and max(left_col_start, right_col_start) > min(left_col_end, right_col_end)
    ):
        return False

    left_box, right_box = left_data.get("bbox"), right_data.get("bbox")
    if left_box is not None and right_box is not None:
        lx0, ly0, lx1, ly1 = left_box
        rx0, ry0, rx1, ry1 = right_box
        if max(lx0, rx0) > min(lx1, rx1) or max(ly0, ry0) > min(ly1, ry1):
            return False
    return True


def _quote_occurrences(text: str, quote: str) -> list[int]:
    found: list[int] = []
    offset = 0
    while True:
        index = text.find(quote, offset)
        if index < 0:
            return found
        found.append(index)
        offset = index + 1


def _span_candidates(
    case: PreparedBenchmarkCase,
    gold_index: int,
    *,
    source_assets: Mapping[str, SourceAsset],
    documents: Mapping[str, Document],
    chunks_by_document: Mapping[str, Sequence[Chunk]],
    evidence_by_chunk: Mapping[str, Sequence[Evidence]],
) -> tuple[list[_Candidate], set[tuple[str, int, int]]]:
    gold = case.case.gold[gold_index]
    matching_documents: list[Document] = []
    for document in documents.values():
        asset = source_assets.get(document.source_id)
        if asset is None or asset.raw_sha256 != gold.raw_hash:
            continue
        same_source = asset.source_path == gold.source_path
        same_file = document.relative_path == gold.source_path
        if (same_source or same_file) and document.raw_hash == gold.raw_hash:
            matching_documents.append(document)

    candidates: list[_Candidate] = []
    intervals: set[tuple[str, int, int]] = set()
    for document in matching_documents:
        txt_coordinates = (
            TextCoordinates.from_text(document.normalized_text)
            if PurePosixPath(document.relative_path).suffix.casefold() == ".txt"
            else None
        )
        for chunk in chunks_by_document.get(document.id, ()):
            if not chunk.is_leaf:
                continue
            for local_start in _quote_occurrences(chunk.text, gold.quote):
                local_end = local_start + len(gold.quote)
                for span in chunk.spans:
                    if not (
                        span.chunk_start <= local_start
                        and local_end <= span.chunk_end
                        and _locator_overlaps(gold.locator, span.locator)
                    ):
                        continue
                    document_start = span.document_start + local_start - span.chunk_start
                    document_end = document_start + len(gold.quote)
                    if document.normalized_text[document_start:document_end] != gold.quote:
                        continue
                    if txt_coordinates is not None and (
                        not isinstance(gold.locator, MarkdownLocator)
                        or not isinstance(span.locator, MarkdownLocator)
                        or gold.locator.path != span.locator.path
                        or txt_coordinates.at(document_start)[0] < gold.locator.line_start
                        or txt_coordinates.at(document_end - 1)[0] > gold.locator.line_end
                    ):
                        continue
                    intervals.add((document.id, document_start, document_end))

                    evidence_ids: list[str] = []
                    for evidence in evidence_by_chunk.get(chunk.id, ()):
                        try:
                            evidence.assert_matches_chunk(chunk)
                        except ValueError:
                            continue
                        if (
                            evidence.start <= local_start
                            and local_end <= evidence.end
                            and _locator_overlaps(gold.locator, evidence.locator)
                        ):
                            evidence_ids.append(evidence.id)
                    candidates.append(
                        _Candidate(
                            chunk_id=chunk.id,
                            evidence_ids=tuple(sorted(set(evidence_ids))),
                            document_id=document.id,
                            document_start=document_start,
                            document_end=document_end,
                        )
                    )
    return candidates, intervals


def _make_span_mapping(
    case: PreparedBenchmarkCase,
    gold_index: int,
    *,
    source_assets: Mapping[str, SourceAsset],
    documents: Mapping[str, Document],
    chunks_by_document: Mapping[str, Sequence[Chunk]],
    evidence_by_chunk: Mapping[str, Sequence[Evidence]],
) -> GoldSpanMapping:
    gold = case.case.gold[gold_index]
    quote_hash = hashlib.sha256(gold.quote.encode("utf-8")).hexdigest()
    group_id = _canonical_hash(
        {
            "case_id": case.case.id,
            "source_path": gold.source_path,
            "raw_hash": gold.raw_hash,
            "locator": gold.locator.model_dump(mode="json"),
            "quote_sha256": quote_hash,
            "claim_group": gold.claim_group,
        }
    )
    candidates, intervals = _span_candidates(
        case,
        gold_index,
        source_assets=source_assets,
        documents=documents,
        chunks_by_document=chunks_by_document,
        evidence_by_chunk=evidence_by_chunk,
    )
    chunk_ids = tuple(sorted({candidate.chunk_id for candidate in candidates}))
    evidence_ids = tuple(sorted({item for candidate in candidates for item in candidate.evidence_ids}))
    interval_rows = tuple(sorted(intervals))

    if not candidates:
        return GoldSpanMapping(
            case_id=case.case.id,
            gold_index=gold_index,
            evidence_group_id=group_id,
            claim_group=gold.claim_group,
            grade=gold.grade,
            quote_sha256=quote_hash,
            status=GoldMappingStatus.unmapped,
            reason="no leaf chunk span matched source path, raw hash, locator, and exact quote",
        )
    if len(intervals) != 1:
        return GoldSpanMapping(
            case_id=case.case.id,
            gold_index=gold_index,
            evidence_group_id=group_id,
            claim_group=gold.claim_group,
            grade=gold.grade,
            quote_sha256=quote_hash,
            status=GoldMappingStatus.ambiguous,
            chunk_ids=chunk_ids,
            evidence_ids=evidence_ids,
            document_intervals=interval_rows,
            reason="the same reviewed quote maps to multiple source intervals",
        )
    if not evidence_ids:
        return GoldSpanMapping(
            case_id=case.case.id,
            gold_index=gold_index,
            evidence_group_id=group_id,
            claim_group=gold.claim_group,
            grade=gold.grade,
            quote_sha256=quote_hash,
            status=GoldMappingStatus.missing_evidence,
            chunk_ids=chunk_ids,
            document_intervals=interval_rows,
            reason="the generation has no source-backed Evidence span covering the Gold quote",
        )
    return GoldSpanMapping(
        case_id=case.case.id,
        gold_index=gold_index,
        evidence_group_id=group_id,
        claim_group=gold.claim_group,
        grade=gold.grade,
        quote_sha256=quote_hash,
        status=GoldMappingStatus.mapped,
        chunk_ids=chunk_ids,
        evidence_ids=evidence_ids,
        document_intervals=interval_rows,
    )


def _same_physical_quote_groups(
    mappings: Sequence[GoldSpanMapping],
    *,
    source_assets: Mapping[str, SourceAsset],
    documents: Mapping[str, Document],
    generation: Generation,
) -> bool:
    """Prove that distinct claim groups resolve to the same physical source span."""
    if len({item.evidence_group_id for item in mappings}) < 2:
        return False
    if len({(item.case_id, item.quote_sha256) for item in mappings}) != 1:
        return False
    if not generation.source_version_ids:
        return False

    generation_scope = (generation.id, tuple(sorted(generation.source_version_ids)))
    physical_identity: tuple[object, ...] | None = None
    evidence_identity: frozenset[str] | None = None
    for item in mappings:
        if (
            item.status is not GoldMappingStatus.mapped
            or len(item.document_intervals) != 1
            or not item.evidence_ids
        ):
            return False
        document_id, start, end = item.document_intervals[0]
        document = documents.get(document_id)
        if document is None or document.id != document_id:
            return False
        source_asset = source_assets.get(document.source_id)
        if (
            source_asset is None
            or source_asset.id != document.source_id
            or document.raw_hash != source_asset.raw_sha256
        ):
            return False

        # A persistent Document ID is the source-version membership key within
        # this verified Generation; equal IDs therefore carry the same version.
        current_physical_identity = (
            generation_scope,
            document.id,
            document.source_id,
            document.relative_path,
            source_asset.source_path,
            source_asset.raw_sha256,
            document.raw_hash,
            start,
            end,
        )
        if physical_identity is None:
            physical_identity = current_physical_identity
        elif current_physical_identity != physical_identity:
            return False

        current_evidence_identity = frozenset(item.evidence_ids)
        if evidence_identity is None:
            evidence_identity = current_evidence_identity
        elif current_evidence_identity != evidence_identity:
            return False

    return physical_identity is not None and evidence_identity is not None


def _relation_evidence_closure(
    relation: Relation, mapped_evidence_groups: Sequence[set[str]]
) -> tuple[str, ...]:
    relation_evidence_ids = set(relation.evidence_ids)
    intersections = [group & relation_evidence_ids for group in mapped_evidence_groups]
    if not intersections or any(not intersection for intersection in intersections):
        return ()
    return tuple(sorted(set().union(*intersections)))


def _source_version_for_endpoint(
    endpoint: GoldEndpointExpectation, context: _SemanticProjectionContext
) -> tuple[Source, SourceVersion] | None:
    sources = [
        item for item in context.sources_by_id.values() if item.logical_uri == endpoint.source_uri
    ]
    version = context.source_versions_by_id.get(endpoint.source_version_id)
    if (
        len(sources) != 1
        or version is None
        or version.id not in context.generation.source_version_ids
        or version.source_id != sources[0].id
        or sources[0].corpus_id != context.generation.corpus_id
        or version.dirty
    ):
        return None
    return sources[0], version


def _document_is_generation_member(
    document: Document, context: _SemanticProjectionContext
) -> bool:
    return any(
        version.id in context.generation.source_version_ids
        and version.source_id == document.source_id
        and any(file.document_id == document.id for file in version.files)
        for version in context.source_versions_by_id.values()
    )


def _document_matches_endpoint(
    document: Document,
    endpoint: GoldEndpointExpectation,
    context: _SemanticProjectionContext,
) -> bool:
    binding = _source_version_for_endpoint(endpoint, context)
    if binding is None:
        return False
    source, version = binding
    matching_files = [
        item
        for item in version.files
        if item.relative_path == endpoint.relative_path
        and item.raw_hash == endpoint.raw_file_sha256
        and item.document_id == document.id
    ]
    return (
        len(matching_files) == 1
        and document.source_id == source.id
        and document.relative_path == endpoint.relative_path
        and document.raw_hash == endpoint.raw_file_sha256
    )


def _provenance_matches_basis(
    provenance: Provenance, evidence_basis: GoldEvidenceBasis
) -> bool:
    expected_method = {
        GoldEvidenceBasis.source_code: ProvenanceMethod.STATIC_ANALYSIS,
        GoldEvidenceBasis.normative_specification: ProvenanceMethod.NORMATIVE_SPECIFICATION,
    }[evidence_basis]
    return provenance.method is expected_method


def _evidence_matches_endpoint(
    evidence: Evidence,
    endpoint: GoldEndpointExpectation,
    evidence_basis: GoldEvidenceBasis,
    context: _SemanticProjectionContext,
) -> bool:
    chunk = context.chunks_by_id.get(evidence.chunk_id)
    provenance = context.provenance_by_id.get(evidence.provenance_id)
    if chunk is None or provenance is None:
        return False
    document = context.documents_by_id.get(chunk.document_id)
    if document is None or not _document_matches_endpoint(document, endpoint, context):
        return False
    if (
        provenance.document_id != document.id
        or provenance.source_id != document.source_id
        or not _provenance_matches_basis(provenance, evidence_basis)
    ):
        return False
    locator_path = getattr(evidence.locator, "path", None)
    return locator_path in {None, endpoint.relative_path}


def _endpoint_matches(
    entity: Entity | None,
    endpoint: GoldEndpointExpectation,
    expectation: GoldRelationExpectation,
    context: _SemanticProjectionContext,
) -> bool:
    if entity is None or entity.kind is not endpoint.entity_kind:
        return False
    if entity.canonical_name != endpoint.qualified_symbol:
        return False
    matching_identity_ids = {
        candidate.id
        for candidate in context.entities_by_id.values()
        if (candidate.corpus_id, candidate.kind, candidate.identity_key)
        == (entity.corpus_id, entity.kind, entity.identity_key)
    }
    if matching_identity_ids != {entity.id}:
        return False
    allowed_surfaces = {
        endpoint.qualified_symbol,
        endpoint.qualified_symbol.rsplit(".", 1)[-1],
    }
    if (
        expectation.evidence_basis is GoldEvidenceBasis.source_code
        and endpoint.qualified_symbol == "None"
    ):
        allowed_surfaces.add("return")
    return any(
        _evidence_matches_endpoint(
            evidence,
            endpoint,
            expectation.evidence_basis,
            context,
        )
        for mention in context.mentions_by_entity.get(entity.id, ())
        for evidence in [context.evidence_by_id.get(mention.evidence_id)]
        if evidence is not None
        and mention.surface_form in allowed_surfaces
        and mention.surface_form in evidence.quote
    )


_STATE_CHANGE_PREDICATES = {
    RelationPredicate.UPDATES,
    RelationPredicate.INVALIDATES,
    RelationPredicate.REPLACES,
    RelationPredicate.DISABLES,
}
_RESULT_ROLES = {
    RelationPredicate.RETURNS: "return_value",
    RelationPredicate.RAISES: "exception",
    RelationPredicate.MAPS_TO: "mapped_value",
    RelationPredicate.CHECKS: "checked_condition",
    RelationPredicate.VALIDATES: "validated_target",
    RelationPredicate.UPDATES: "updated_state",
    RelationPredicate.SCHEDULES: "scheduled_target",
    RelationPredicate.REQUIRES: "required_action",
    RelationPredicate.INVALIDATES: "invalidated_target",
    RelationPredicate.REPLACES: "replacement_target",
    RelationPredicate.DISABLES: "disabled_target",
    RelationPredicate.OFFERS: "offered_alternative",
    RelationPredicate.COMPARES_AGAINST: "comparison_target",
    RelationPredicate.PRODUCES: "produced_result",
    RelationPredicate.REPORTS: "reported_value",
}


def _outcome_kind(
    predicate: RelationPredicate,
    evidence_basis: GoldEvidenceBasis,
    qualifiers: Mapping[str, Any],
) -> GoldOutcomeKind:
    if predicate is RelationPredicate.RETURNS:
        return GoldOutcomeKind.return_value
    if predicate is RelationPredicate.RAISES:
        return GoldOutcomeKind.exception
    if (
        evidence_basis is GoldEvidenceBasis.normative_specification
        and "normative_modality" in qualifiers
    ):
        return GoldOutcomeKind.normative_obligation
    if predicate in _STATE_CHANGE_PREDICATES:
        return GoldOutcomeKind.state_change
    return GoldOutcomeKind.fact


def _relation_provenance_matches(
    relation: Relation,
    expectation: GoldRelationExpectation,
    context: _SemanticProjectionContext,
) -> bool:
    provenance = context.provenance_by_id.get(relation.provenance_id)
    if provenance is None:
        return False
    if provenance.method is ProvenanceMethod.RULE_INFERRED:
        document = context.documents_by_id.get(provenance.document_id)
        if (
            document is None
            or document.source_id != provenance.source_id
            or not _document_is_generation_member(document, context)
            or not any(
                _document_matches_endpoint(document, endpoint, context)
                for endpoint in (expectation.subject, expectation.object)
            )
            or not provenance.premise_relation_ids
        ):
            return False
        relation_evidence_ids = set(relation.evidence_ids)
        for premise_id in provenance.premise_relation_ids:
            premise = context.relations_by_id.get(premise_id)
            if (
                premise is None
                or premise_id not in context.generation.relation_ids
                or premise.provenance_id not in context.provenance_by_id
                or not premise.evidence_ids
                or not set(premise.evidence_ids).issubset(relation_evidence_ids)
            ):
                return False
            if any(
                evidence_id not in context.evidence_by_id
                or not any(
                    _evidence_matches_endpoint(
                        context.evidence_by_id[evidence_id],
                        endpoint,
                        expectation.evidence_basis,
                        context,
                    )
                    for endpoint in (expectation.subject, expectation.object)
                )
                for evidence_id in premise.evidence_ids
            ):
                return False
        return True
    if not _provenance_matches_basis(provenance, expectation.evidence_basis):
        return False
    document = context.documents_by_id.get(provenance.document_id)
    if document is None or provenance.source_id != document.source_id:
        return False
    return any(
        _document_matches_endpoint(document, endpoint, context)
        for endpoint in (expectation.subject, expectation.object)
    )


def _semantic_projection_failure(
    relation: Relation,
    expectation: GoldRelationExpectation,
    *,
    mapped_evidence_groups: Sequence[set[str]],
    context: _SemanticProjectionContext,
) -> tuple[tuple[str, ...], str | None]:
    if relation.predicate is not expectation.predicate:
        return (), "does not match the expected predicate"
    if relation.polarity is not expectation.polarity:
        return (), "does not match the expected polarity"
    if expectation.qualifier_review_state is GoldQualifierReviewState.unreviewed:
        return (), "cannot be resolved because its qualifier review is UNREVIEWED"
    if _canonical_hash(relation.qualifiers) != _canonical_hash(expectation.qualifier_values):
        return (), "does not match the exact reviewed qualifier set"
    if (
        _outcome_kind(
            relation.predicate,
            expectation.evidence_basis,
            relation.qualifiers,
        )
        is not expectation.outcome_kind
    ):
        return (), "does not match the expected result outcome kind"
    if _RESULT_ROLES.get(relation.predicate, "relation_object") != expectation.result_role:
        return (), "does not match the expected result role"

    subject = context.entities_by_id.get(relation.subject_id)
    object_ = context.entities_by_id.get(relation.object_id)
    subject_matches = _endpoint_matches(subject, expectation.subject, expectation, context)
    object_matches = _endpoint_matches(object_, expectation.object, expectation, context)
    if not subject_matches or not object_matches:
        swapped = _endpoint_matches(subject, expectation.object, expectation, context) and (
            _endpoint_matches(object_, expectation.subject, expectation, context)
        )
        return (), (
            "has an endpoint direction mismatch"
            if swapped
            else "has no candidate matching exact source-bound endpoints"
        )

    closed_ids = _relation_evidence_closure(relation, mapped_evidence_groups)
    if not closed_ids:
        return (), "does not close over Evidence for every reviewed Gold quote"
    if not _relation_provenance_matches(relation, expectation, context):
        return (), "does not close through the expected Generation Provenance chain"
    endpoints = (expectation.subject, expectation.object)
    if any(
        not any(
            _evidence_matches_endpoint(
                context.evidence_by_id[evidence_id],
                endpoint,
                expectation.evidence_basis,
                context,
            )
            for endpoint in endpoints
        )
        for evidence_id in relation.evidence_ids
        if evidence_id in context.evidence_by_id
    ) or any(evidence_id not in context.evidence_by_id for evidence_id in relation.evidence_ids):
        return (), "contains Evidence outside its exact source, version, path, or basis"
    return closed_ids, None


def _resolve_runtime_relation(
    edge_relation_id: str,
    mapped_evidence_groups: Sequence[set[str]],
    *,
    relation_by_id: Mapping[str, Relation],
    relations: Sequence[Relation],
    relation_expectations: Mapping[str, GoldRelationExpectation] | None,
    semantic_context: _SemanticProjectionContext,
) -> tuple[Relation | None, tuple[str, ...], str | None]:
    direct = relation_by_id.get(edge_relation_id)
    expectation = (relation_expectations or {}).get(edge_relation_id)
    if direct is not None:
        if expectation is not None:
            closed_ids, failure = _semantic_projection_failure(
                direct,
                expectation,
                mapped_evidence_groups=mapped_evidence_groups,
                context=semantic_context,
            )
            if failure:
                return None, (), failure
            return direct, closed_ids, None
        closed = _relation_evidence_closure(direct, mapped_evidence_groups)
        if not closed:
            return None, (), "does not close over Evidence for every reviewed Gold quote"
        return direct, closed, None
    if expectation is None:
        return None, (), None

    matches: list[tuple[Relation, tuple[str, ...]]] = []
    has_direction_mismatch = False
    failures: set[str] = set()
    for candidate in relations:
        closed, failure = _semantic_projection_failure(
            candidate,
            expectation,
            mapped_evidence_groups=mapped_evidence_groups,
            context=semantic_context,
        )
        if failure == "has an endpoint direction mismatch":
            has_direction_mismatch = True
        if failure is not None:
            failures.add(failure)
            continue
        if failure is None:
            matches.append((candidate, closed))
    if len(matches) > 1:
        return None, (), "has multiple semantic candidates"
    if not matches:
        if has_direction_mismatch:
            return None, (), "has an endpoint direction mismatch"
        for preferred_failure in (
            "cannot be resolved because its qualifier review is UNREVIEWED",
            "does not match the exact reviewed qualifier set",
            "has no candidate matching exact source-bound endpoints",
            "does not match the expected result outcome kind",
            "does not match the expected result role",
            "does not close over Evidence for every reviewed Gold quote",
            "does not close through the expected Generation Provenance chain",
            "contains Evidence outside its exact source, version, path, or basis",
        ):
            if preferred_failure in failures:
                return None, (), preferred_failure
        return None, (), "has no candidate matching its exact endpoints and Evidence for every Gold quote"
    relation, closed_ids = matches[0]
    return relation, closed_ids, None


def map_gold_to_generation(
    cases: Sequence[PreparedBenchmarkCase],
    source_assets: Sequence[SourceAsset],
    documents: Sequence[Document],
    chunks: Sequence[Chunk],
    evidence: Sequence[Evidence],
    relations: Sequence[Relation],
    *,
    entities: Sequence[Entity] = (),
    mentions: Sequence[Mention] = (),
    sources: Sequence[Source] = (),
    source_versions: Sequence[SourceVersion] = (),
    provenance: Sequence[Provenance] = (),
    relation_expectations: Mapping[str, GoldRelationExpectation] | None = None,
    generation: Generation,
    dataset_sha256: str,
) -> GoldMappingReport:
    """Map every reviewed span/path; unmapped, ambiguous, and incomplete rows block readiness."""
    expectation_ids = relation_expectations or {}
    needs_semantic_entities = any(
        edge.relation_id in expectation_ids
        for case in cases
        for path in case.gold_paths
        for edge in path.edges
    )
    observed_members: dict[str, Sequence[str]] = {
        "document": [item.id for item in documents],
        "chunk": [item.id for item in chunks],
        "evidence": [item.id for item in evidence],
        "relation": [item.id for item in relations],
    }
    if needs_semantic_entities:
        observed_members["entity"] = [item.id for item in entities]
        observed_members["mention"] = [item.id for item in mentions]
        observed_members["provenance"] = [item.id for item in provenance]
    verified_generation = verify_generation_manifest(
        generation,
        observed_member_ids=observed_members,
    )
    if needs_semantic_entities and sorted(entity.id for entity in entities) != sorted(
        verified_generation.generation.entity_ids
    ):
        raise GenerationBindingError(
            "observed Entity IDs differ from persisted Generation entity membership"
        )
    if needs_semantic_entities and any(
        entity.corpus_id != verified_generation.generation.corpus_id for entity in entities
    ):
        raise GenerationBindingError("observed Entity corpus differs from persisted Generation")
    if needs_semantic_entities and sorted(item.id for item in source_versions) != sorted(
        verified_generation.generation.source_version_ids
    ):
        raise GenerationBindingError(
            "observed SourceVersion IDs differ from persisted Generation membership"
        )
    source_ids = {item.id for item in sources}
    if needs_semantic_entities and any(
        item.source_id not in source_ids for item in source_versions
    ):
        raise GenerationBindingError("observed SourceVersion references a missing Source")
    if needs_semantic_entities:
        for label, records in (
            ("Source", sources),
            ("SourceVersion", source_versions),
            ("Mention", mentions),
            ("Provenance", provenance),
        ):
            if len({item.id for item in records}) != len(records):
                raise GenerationBindingError(f"observed {label} IDs are not unique")
    assets = {item.id: item for item in source_assets}
    docs = {item.id: item for item in documents}
    chunks_by_document: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        chunks_by_document.setdefault(chunk.document_id, []).append(chunk)
    evidence_by_chunk: dict[str, list[Evidence]] = {}
    for item in evidence:
        evidence_by_chunk.setdefault(item.chunk_id, []).append(item)
    relation_by_id = {item.id: item for item in relations}
    entities_by_id = {item.id: item for item in entities}
    chunks_by_id = {item.id: item for item in chunks}
    evidence_by_id = {item.id: item for item in evidence}
    sources_by_id = {item.id: item for item in sources}
    source_versions_by_id = {item.id: item for item in source_versions}
    provenance_by_id = {item.id: item for item in provenance}
    mentions_by_entity: dict[str, list[Mention]] = {}
    for mention in mentions:
        mentions_by_entity.setdefault(mention.entity_id, []).append(mention)
    semantic_context = _SemanticProjectionContext(
        generation=verified_generation.generation,
        entities_by_id=entities_by_id,
        mentions_by_entity=mentions_by_entity,
        evidence_by_id=evidence_by_id,
        chunks_by_id=chunks_by_id,
        documents_by_id=docs,
        sources_by_id=sources_by_id,
        source_versions_by_id=source_versions_by_id,
        provenance_by_id=provenance_by_id,
        relations_by_id=relation_by_id,
    )

    span_mappings: list[GoldSpanMapping] = []
    path_mappings: list[GoldPathMapping] = []
    blockers: list[str] = []
    mappings_by_case_quote: dict[tuple[str, str], list[GoldSpanMapping]] = {}
    for case in cases:
        case_mappings: list[GoldSpanMapping] = []
        for index, _gold in enumerate(case.case.gold):
            mapped = _make_span_mapping(
                case,
                index,
                source_assets=assets,
                documents=docs,
                chunks_by_document=chunks_by_document,
                evidence_by_chunk=evidence_by_chunk,
            )
            case_mappings.append(mapped)
            span_mappings.append(mapped)
            mappings_by_case_quote.setdefault((case.case.id, mapped.quote_sha256), []).append(mapped)
            if mapped.status is not GoldMappingStatus.mapped:
                blockers.append(
                    f"case={case.case.id};gold_index={index};status={mapped.status.value};"
                    f"reason={mapped.reason}"
                )

        for path_index, reviewed_path in enumerate(case.gold_paths):
            mapped_relations: list[str] = []
            mapped_directions: list[bool] = []
            edge_evidence: list[tuple[str, ...]] = []
            previous_to_id: str | None = None
            failure: str | None = None
            for edge_index, edge in enumerate(reviewed_path.edges):
                mapped_evidence_groups: list[set[str]] = []
                for quote_hash in edge.evidence_quote_sha256:
                    span_mappings_for_quote = mappings_by_case_quote.get(
                        (case.case.id, quote_hash), []
                    )
                    unique_groups = {item.evidence_group_id: item for item in span_mappings_for_quote}
                    if len(unique_groups) != 1 and not _same_physical_quote_groups(
                        tuple(unique_groups.values()),
                        source_assets=assets,
                        documents=docs,
                        generation=verified_generation.generation,
                    ):
                        failure = (
                            f"Gold path edge {edge_index} quote hash {quote_hash} "
                            "does not identify exactly one reviewed source span"
                        )
                        break
                    span_mapping = next(iter(unique_groups.values()))
                    if span_mapping.status is not GoldMappingStatus.mapped:
                        failure = (
                            f"Gold path edge {edge_index} quote hash {quote_hash} "
                            "does not resolve to a mapped span"
                        )
                        break
                    mapped_evidence_groups.append(set(span_mapping.evidence_ids))
                if failure:
                    break
                relation, closed_ids, resolution_failure = _resolve_runtime_relation(
                    edge.relation_id,
                    mapped_evidence_groups,
                    relation_by_id=relation_by_id,
                    relations=relations,
                    relation_expectations=relation_expectations,
                    semantic_context=semantic_context,
                )
                if relation is None:
                    failure = (
                        f"Gold relation {edge.relation_id} {resolution_failure}"
                        if resolution_failure
                        else f"Gold relation {edge.relation_id} is absent from generation"
                    )
                    break
                if not closed_ids:
                    failure = (
                        f"Gold path edge {edge_index} relation "
                        f"{resolution_failure or 'does not close over mapped Evidence'}"
                    )
                    break
                generation_entity_ids = set(verified_generation.generation.entity_ids)
                if (
                    relation.subject_id not in generation_entity_ids
                    or relation.object_id not in generation_entity_ids
                ):
                    failure = (
                        f"Gold path edge {edge_index} relation endpoint is outside Generation "
                        "entity membership"
                    )
                    break
                step_from_id, step_to_id = (
                    (relation.object_id, relation.subject_id)
                    if edge.traversed_reverse
                    else (relation.subject_id, relation.object_id)
                )
                if previous_to_id is not None and previous_to_id != step_from_id:
                    failure = (
                        f"Gold path is discontinuous between edges {edge_index - 1} and "
                        f"{edge_index}: {previous_to_id} != {step_from_id}"
                    )
                    break
                mapped_relations.append(relation.id)
                mapped_directions.append(edge.traversed_reverse)
                edge_evidence.append(closed_ids)
                previous_to_id = step_to_id
            status = GoldMappingStatus.mapped if failure is None else GoldMappingStatus.unmapped
            path = GoldPathMapping(
                case_id=case.case.id,
                path_index=path_index,
                status=status,
                relation_ids=tuple(mapped_relations),
                traversed_reverse=tuple(mapped_directions),
                evidence_ids_by_edge=tuple(edge_evidence),
                reason=failure,
            )
            path_mappings.append(path)
            if failure:
                blockers.append(
                    f"case={case.case.id};path_index={path_index};status={status.value};reason={failure}"
                )

    return GoldMappingReport(
        generation_id=verified_generation.id,
        generation_sha256=verified_generation.generation_sha256,
        dataset_sha256=dataset_sha256,
        span_mappings=tuple(span_mappings),
        path_mappings=tuple(path_mappings),
        blockers=tuple(blockers),
    )


def mapped_evidence_group_recall(
    returned_chunk_ids: Sequence[str], mappings: Sequence[GoldSpanMapping]
) -> float:
    """Recall of reviewed source spans, counting overlapping leaf chunks as one group."""
    if not mappings:
        raise ValueError("evidence-group recall requires mapped Gold spans")
    if any(item.status is not GoldMappingStatus.mapped for item in mappings):
        raise ValueError("unmapped or ambiguous Gold spans block recall calculation")
    returned = set(returned_chunk_ids)
    groups: dict[str, set[str]] = {}
    for item in mappings:
        groups.setdefault(item.evidence_group_id, set()).update(item.chunk_ids)
    if not groups:
        raise ValueError("evidence-group recall requires at least one unique Gold group")
    return sum(bool(returned.intersection(chunk_ids)) for chunk_ids in groups.values()) / len(groups)


def strict_chunk_recall(
    returned_chunk_ids: Sequence[str], mappings: Sequence[GoldSpanMapping]
) -> float:
    """Strict leaf-chunk recall alongside equivalent-overlap evidence-group recall."""
    if not mappings:
        raise ValueError("strict chunk recall requires mapped Gold spans")
    if any(item.status is not GoldMappingStatus.mapped for item in mappings):
        raise ValueError("unmapped or ambiguous Gold spans block recall calculation")
    gold_chunks = {chunk_id for item in mappings for chunk_id in item.chunk_ids}
    if not gold_chunks:
        raise ValueError("mapped Gold spans did not resolve any leaf chunks")
    return len(set(returned_chunk_ids) & gold_chunks) / len(gold_chunks)
