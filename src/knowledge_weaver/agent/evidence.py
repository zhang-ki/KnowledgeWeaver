from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from knowledge_weaver.core.errors import ErrorCode, KnowledgeWeaverError
from knowledge_weaver.models import (
    Chunk,
    Evidence,
    Path,
    QueryContext,
    RetrievalHit,
    RetrievalResult,
    SourceKind,
)
from knowledge_weaver.ports import KnowledgeStore


@dataclass(frozen=True, slots=True)
class EvidenceEntry:
    evidence: Evidence
    chunk: Chunk
    duplicate_key: str

    @property
    def source_kind(self) -> str:
        locator_kind = self.evidence.locator.kind
        if locator_kind == "python":
            return SourceKind.python_repo.value
        return locator_kind


class EvidenceLedger:
    """Investigation-local, source-closed ledger with duplicate-group accounting."""

    def __init__(self) -> None:
        self.entries: OrderedDict[str, EvidenceEntry] = OrderedDict()
        self.hits: OrderedDict[str, RetrievalHit] = OrderedDict()
        self.paths: dict[str, Path] = {}

    async def absorb(
        self,
        result: RetrievalResult,
        store: KnowledgeStore,
        context: QueryContext,
    ) -> list[str]:
        if result.context != context:
            raise KnowledgeWeaverError(
                ErrorCode.GENERATION_CONFLICT,
                "retrieval result does not match the investigation context",
                request_id=context.request_id,
            )
        evidence_ids = list(
            dict.fromkeys(
                evidence_id
                for hit in sorted(result.hits, key=lambda item: (item.rank, item.chunk_id))
                for evidence_id in hit.evidence_ids
            )
        )
        hydrated: dict[str, Evidence] = {}
        chunks_by_id: dict[str, Chunk] = {}
        if evidence_ids:
            evidence_rows = await store.get_evidence(evidence_ids, context)
            hydrated = {item.id: item for item in evidence_rows}
            missing = sorted(set(evidence_ids) - set(hydrated))
            if missing:
                raise KnowledgeWeaverError(
                    ErrorCode.MISSING_REFERENCE,
                    "retrieval result referenced missing evidence",
                    request_id=context.request_id,
                    details={"missing_evidence_ids": missing},
                )
            chunk_ids = list(dict.fromkeys(item.chunk_id for item in hydrated.values()))
            chunks = await store.get_chunks(chunk_ids, context)
            chunks_by_id = {item.id: item for item in chunks}
            missing_chunks = sorted(set(chunk_ids) - set(chunks_by_id))
            if missing_chunks:
                raise KnowledgeWeaverError(
                    ErrorCode.MISSING_REFERENCE,
                    "retrieval evidence referenced missing chunks",
                    request_id=context.request_id,
                    details={"missing_chunk_ids": missing_chunks},
                )

        new_ids: list[str] = []
        for evidence_id in evidence_ids:
            evidence = hydrated[evidence_id]
            chunk = chunks_by_id[evidence.chunk_id]
            try:
                evidence.assert_matches_chunk(chunk)
            except ValueError as exc:
                raise KnowledgeWeaverError(
                    ErrorCode.SCHEMA_INVALID,
                    "retrieved quote is not a source-backed span of its chunk",
                    request_id=context.request_id,
                    details={"evidence_id": evidence_id},
                ) from exc
            if evidence_id not in self.entries:
                duplicate_key = chunk.duplicate_group or chunk.id
                self.entries[evidence_id] = EvidenceEntry(
                    evidence=evidence,
                    chunk=chunk,
                    duplicate_key=duplicate_key,
                )
                new_ids.append(evidence_id)

        for path in result.paths:
            previous = self.paths.get(path.id)
            if previous is not None and previous != path:
                raise KnowledgeWeaverError(
                    ErrorCode.SCHEMA_INVALID,
                    "retrieval result contains conflicting path records",
                    request_id=context.request_id,
                )
            self.paths[path.id] = path

        for hit in sorted(result.hits, key=lambda item: (item.rank, item.chunk_id)):
            old = self.hits.get(hit.chunk_id)
            if old is None:
                self.hits[hit.chunk_id] = hit
            else:
                self.hits[hit.chunk_id] = old.model_copy(
                    update={
                        "evidence_ids": sorted(set(old.evidence_ids) | set(hit.evidence_ids)),
                        "path_ids": sorted(set(old.path_ids) | set(hit.path_ids)),
                        "contributions": [*old.contributions, *hit.contributions],
                        "score": max(old.score, hit.score),
                    }
                )
        return new_ids

    def set_ranked_hits(self, hits: list[RetrievalHit]) -> None:
        ranked: OrderedDict[str, RetrievalHit] = OrderedDict()
        for hit in sorted(hits, key=lambda item: (item.rank, item.chunk_id)):
            if hit.chunk_id not in self.hits:
                continue
            original = self.hits[hit.chunk_id]
            ranked[hit.chunk_id] = original.model_copy(
                update={
                    "rank": len(ranked) + 1,
                    "rerank_score": hit.rerank_score,
                }
            )
        for chunk_id, hit in self.hits.items():
            if chunk_id not in ranked:
                ranked[chunk_id] = hit.model_copy(update={"rank": len(ranked) + 1})
        self.hits = ranked

    def evidence_for_display(self, *, limit: int = 10) -> list[EvidenceEntry]:
        displayed: list[EvidenceEntry] = []
        for hit in self.hits.values():
            for evidence_id in hit.evidence_ids:
                entry = self.entries.get(evidence_id)
                if entry is not None and evidence_id not in {item.evidence.id for item in displayed}:
                    displayed.append(entry)
                    if len(displayed) >= limit:
                        return displayed
        return displayed

    def evidence_ids(self) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for hit in self.hits.values():
            for evidence_id in hit.evidence_ids:
                if evidence_id in self.entries and evidence_id not in seen:
                    ordered.append(evidence_id)
                    seen.add(evidence_id)
        for evidence_id in self.entries:
            if evidence_id not in seen:
                ordered.append(evidence_id)
        return ordered

    def support_evidence(self, evidence_ids: list[str]) -> list[Evidence]:
        return [self.entries[item].evidence for item in evidence_ids if item in self.entries]

    def relation_ids(self) -> set[str]:
        relation_ids: set[str] = set()
        for path in self.paths.values():
            relation_ids.update(step.relation_id for step in path.steps)
        return relation_ids

    def cited_paths(self, hit_ids: set[str] | None = None) -> list[Path]:
        if hit_ids is None:
            return list(self.paths.values())
        return [
            path
            for path in self.paths.values()
            if any(evidence_id in hit_ids for step in path.steps for evidence_id in step.evidence_ids)
        ]
