"""Resolve reviewed source spans to source-backed leaf chunks in one generation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from knowledge_weaver.evaluation.contracts import PreparedBenchmarkCase, SourceAsset
from knowledge_weaver.evaluation.generation_binding import verify_generation_manifest
from knowledge_weaver.models.contracts import Chunk, Document, Evidence, Generation, Relation


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


def map_gold_to_generation(
    cases: Sequence[PreparedBenchmarkCase],
    source_assets: Sequence[SourceAsset],
    documents: Sequence[Document],
    chunks: Sequence[Chunk],
    evidence: Sequence[Evidence],
    relations: Sequence[Relation],
    *,
    generation: Generation,
    dataset_sha256: str,
) -> GoldMappingReport:
    """Map every reviewed span/path; unmapped, ambiguous, and incomplete rows block readiness."""
    verified_generation = verify_generation_manifest(
        generation,
        observed_member_ids={
            "document": [item.id for item in documents],
            "chunk": [item.id for item in chunks],
            "evidence": [item.id for item in evidence],
            "relation": [item.id for item in relations],
        },
    )
    assets = {item.id: item for item in source_assets}
    docs = {item.id: item for item in documents}
    chunks_by_document: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        chunks_by_document.setdefault(chunk.document_id, []).append(chunk)
    evidence_by_chunk: dict[str, list[Evidence]] = {}
    for item in evidence:
        evidence_by_chunk.setdefault(item.chunk_id, []).append(item)
    relation_by_id = {item.id: item for item in relations}

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
            failure: str | None = None
            for edge_index, edge in enumerate(reviewed_path.edges):
                relation = relation_by_id.get(edge.relation_id)
                if relation is None:
                    failure = f"Gold relation {edge.relation_id} is absent from generation"
                    break
                mapped_ids: set[str] = set()
                for quote_hash in edge.evidence_quote_sha256:
                    span_mappings_for_quote = mappings_by_case_quote.get(
                        (case.case.id, quote_hash), []
                    )
                    unique_groups = {item.evidence_group_id: item for item in span_mappings_for_quote}
                    if len(unique_groups) != 1:
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
                    mapped_ids.update(span_mapping.evidence_ids)
                if failure:
                    break
                closed_ids = tuple(sorted(mapped_ids & set(relation.evidence_ids)))
                if not closed_ids:
                    failure = (
                        f"Gold path edge {edge_index} relation does not close over mapped Evidence"
                    )
                    break
                mapped_relations.append(relation.id)
                mapped_directions.append(edge.traversed_reverse)
                edge_evidence.append(closed_ids)
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
