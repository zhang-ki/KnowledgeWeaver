from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from knowledge_weaver.core.errors import ErrorCode, KnowledgeWeaverError
from knowledge_weaver.models import (
    Chunk,
    CitationCheck,
    CitationCheckItem,
    Evidence,
    Generation,
    QueryContext,
)

from .database import Database
from .schema import (
    ChunkRow,
    CorpusRow,
    DocumentRow,
    EvidenceRow,
    GenerationChunkRow,
    GenerationDocumentRow,
    GenerationEntityRow,
    GenerationEvidenceRow,
    GenerationMentionRow,
    GenerationProvenanceRow,
    GenerationRelationRow,
    GenerationRow,
    GenerationSourceVersionRow,
    QueryLeaseRow,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_MEMBER_TABLES: dict[str, tuple[Any, str]] = {
    "source_version": (GenerationSourceVersionRow, "source_version_id"),
    "document": (GenerationDocumentRow, "document_id"),
    "chunk": (GenerationChunkRow, "chunk_id"),
    "evidence": (GenerationEvidenceRow, "evidence_id"),
    "provenance": (GenerationProvenanceRow, "provenance_id"),
    "entity": (GenerationEntityRow, "entity_id"),
    "mention": (GenerationMentionRow, "mention_id"),
    "relation": (GenerationRelationRow, "relation_id"),
}


class PostgresKnowledgeStore:
    def __init__(self, database: Database, *, lease_seconds: int = 300) -> None:
        self.database = database
        self.lease_seconds = lease_seconds

    async def _members(self, session: AsyncSession, generation_id: str, kind: str) -> set[str]:
        model, member_column = _MEMBER_TABLES[kind]
        result: Any = await session.scalars(
            select(getattr(model, member_column)).where(model.generation_id == generation_id)
        )
        return set(result.all())

    async def _assert_context(self, session: AsyncSession, context: QueryContext) -> None:
        generation = await session.get(GenerationRow, context.generation_id)
        if generation is None or generation.state not in {"READY", "RETIRED"} or generation.corpus_id != context.corpus_id:
            raise KnowledgeWeaverError(ErrorCode.NO_READY_GENERATION, "Query context does not identify a ready generation in this corpus.", request_id=context.request_id)

    async def resolve_context(self, corpus_id: str, request_id: str) -> QueryContext:
        async with self.database.session() as session:
            corpus = await session.get(CorpusRow, corpus_id)
            if corpus is None or corpus.active_generation_id is None:
                raise KnowledgeWeaverError(ErrorCode.NO_READY_GENERATION, "Corpus has no published generation.", request_id=request_id)
            generation = await session.get(GenerationRow, corpus.active_generation_id)
            if generation is None or generation.state not in {"READY", "RETIRED"}:
                raise KnowledgeWeaverError(ErrorCode.NO_READY_GENERATION, "Corpus has no published generation.", request_id=request_id)
            expiry = datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
            await session.merge(QueryLeaseRow(request_id=request_id, generation_id=generation.id, expires_at=expiry))
            await session.commit()
            return QueryContext(corpus_id=corpus_id, generation_id=generation.id, request_id=request_id, deadline_utc=expiry)

    async def get_chunks(self, ids: list[str], context: QueryContext) -> list[Chunk]:
        if not ids:
            return []
        async with self.database.session() as session:
            await self._assert_context(session, context)
            members = await self._members(session, context.generation_id, "chunk")
            requested = set(ids)
            missing = requested - members
            if missing:
                raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "One or more chunks are outside the query generation.", request_id=context.request_id)
            query = select(ChunkRow).join(DocumentRow, ChunkRow.document_id == DocumentRow.id).where(ChunkRow.id.in_(requested))
            if context.source_ids is not None:
                if not context.source_ids:
                    raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "Chunks are excluded by the empty source filter.", request_id=context.request_id)
                query = query.where(DocumentRow.source_id.in_(context.source_ids))
            rows = (await session.scalars(query)).all()
            if {row.id for row in rows} != requested:
                raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "One or more chunks are excluded by the source filter.", request_id=context.request_id)
            return [Chunk.model_validate(row.payload) for row in rows]

    async def get_evidence(self, ids: list[str], context: QueryContext) -> list[Evidence]:
        return await self._evidence(ids, context, by_chunk=False)

    async def get_chunk_evidence(self, ids: list[str], context: QueryContext) -> list[Evidence]:
        return await self._evidence(ids, context, by_chunk=True)

    async def _evidence(self, ids: list[str], context: QueryContext, *, by_chunk: bool) -> list[Evidence]:
        if not ids:
            return []
        async with self.database.session() as session:
            await self._assert_context(session, context)
            ev_members = await self._members(session, context.generation_id, "evidence")
            chunk_members = await self._members(session, context.generation_id, "chunk")
            if by_chunk:
                requested = set(ids)
                direct_chunks = requested & chunk_members
                direct_evidence = requested & ev_members
                if requested - direct_chunks - direct_evidence:
                    raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "One or more chunk/evidence references are outside the query generation.", request_id=context.request_id)
                chunk_ids = set(direct_chunks)
                if direct_evidence:
                    chunk_ids.update((await session.scalars(select(EvidenceRow.chunk_id).where(EvidenceRow.id.in_(direct_evidence)))).all())
                if direct_evidence and not direct_chunks:
                    rows = (await session.scalars(select(EvidenceRow).where(EvidenceRow.id.in_(direct_evidence), EvidenceRow.id.in_(ev_members)))).all()
                else:
                    rows = (await session.scalars(select(EvidenceRow).where(EvidenceRow.id.in_(ev_members), EvidenceRow.chunk_id.in_(chunk_ids)))).all() if chunk_ids else []
            else:
                if set(ids) - ev_members:
                    raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "One or more evidence references are outside the query generation.", request_id=context.request_id)
                rows = (await session.scalars(select(EvidenceRow).where(EvidenceRow.id.in_(ids)))).all()
            chunk_ids = {row.chunk_id for row in rows}
            chunks = {row.id: row for row in (await session.scalars(select(ChunkRow).where(ChunkRow.id.in_(chunk_ids)))).all()}
            source_ids = set(context.source_ids) if context.source_ids is not None else None
            documents = {row.id: row for row in (await session.scalars(select(DocumentRow).where(DocumentRow.id.in_({chunks[row.chunk_id].document_id for row in rows} or {""})))).all()}
            if source_ids is not None:
                rows = [row for row in rows if documents[chunks[row.chunk_id].document_id].source_id in source_ids]
            if not by_chunk and {row.id for row in rows} != set(ids):
                raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "One or more evidence records are missing or filtered from this context.", request_id=context.request_id)
            return [Evidence.model_validate(row.payload) for row in rows]

    async def validate_citations(self, ids: list[str], context: QueryContext) -> CitationCheck:
        async with self.database.session() as session:
            await self._assert_context(session, context)
            members = await self._members(session, context.generation_id, "evidence")
            valid = sorted(set(ids) & members)
            invalid = [CitationCheckItem(id=item, reason="MISSING_REFERENCE") for item in sorted(set(ids) - members)]
            if context.source_ids is not None and valid:
                accepted = {item.id for item in await self.get_evidence(valid, context)}
                invalid.extend(CitationCheckItem(id=item, reason="SOURCE_FILTERED") for item in valid if item not in accepted)
                valid = sorted(accepted)
            return CitationCheck(valid_ids=valid, invalid=invalid)

    async def release_lease(self, request_id: str) -> None:
        async with self.database.session() as session:
            await session.execute(delete(QueryLeaseRow).where(QueryLeaseRow.request_id == request_id))
            await session.commit()

    async def generation(self, generation_id: str) -> Generation:
        async with self.database.session() as session:
            row = await session.get(GenerationRow, generation_id)
            if row is None:
                raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "Generation reference does not exist.")
            members: dict[str, list[str]] = {}
            for kind in ("chunk", "entity", "relation"):
                members[kind] = sorted(await self._members(session, generation_id, kind))
            return Generation(id=row.id, corpus_id=row.corpus_id, state=row.state,
                source_version_ids=row.source_version_ids, chunk_ids=members["chunk"],
                entity_ids=members["entity"], relation_ids=members["relation"],
                index_manifest=row.index_manifest, embedding_fingerprint=row.embedding_fingerprint,
                published_at=row.published_at)
