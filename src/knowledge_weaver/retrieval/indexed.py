from __future__ import annotations

import asyncio
import math
import time
from abc import ABC
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import TypeVar

from knowledge_weaver.core.errors import DeadlineExceeded, ErrorCode, KnowledgeWeaverError
from knowledge_weaver.models import (
    CountKind,
    IndexCandidate,
    IndexChannel,
    IndexQuery,
    RetrievalHit,
    RetrievalQuery,
    RetrievalResult,
    Usage,
)
from knowledge_weaver.ports import CandidateIndex, KnowledgeStore

_T = TypeVar("_T")


async def _before_deadline(awaitable: Awaitable[_T], deadline: datetime) -> _T:
    remaining = (deadline.astimezone(UTC) - datetime.now(UTC)).total_seconds()
    if remaining <= 0:
        raise DeadlineExceeded()
    try:
        async with asyncio.timeout(remaining):
            return await awaitable
    except TimeoutError:
        raise DeadlineExceeded() from None


class _IndexedRetriever(ABC):
    channel: IndexChannel

    def __init__(self, index: CandidateIndex, store: KnowledgeStore) -> None:
        self.index = index
        self.store = store

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        if query.context.source_ids == []:
            return RetrievalResult(
                context=query.context,
                hits=[],
                usage=Usage(count_kind=CountKind.exact),
            )

        index_query = IndexQuery(
            channel=self.channel,
            context=query.context,
            text=query.text,
            top_k=query.top_k,
            entity_ids=query.entity_ids,
            symbol=query.symbol,
        )
        started = time.monotonic()
        candidates = await _before_deadline(
            self.index.search(index_query), query.context.deadline_utc
        )
        selected = self._deduplicate(candidates)
        if not selected:
            return RetrievalResult(
                context=query.context,
                hits=[],
                usage=Usage(
                    elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                    tool_calls=1,
                    count_kind=CountKind.exact,
                ),
            )

        chunk_ids = [candidate.chunk_id for candidate in selected]
        chunks = await _before_deadline(
            self.store.get_chunks(chunk_ids, query.context), query.context.deadline_utc
        )
        chunk_by_id = {chunk.id: chunk for chunk in chunks}
        if set(chunk_by_id) != set(chunk_ids):
            raise KnowledgeWeaverError(
                ErrorCode.MISSING_REFERENCE,
                "Candidate index returned a chunk missing from the query generation.",
                request_id=query.context.request_id,
            )
        evidence = await _before_deadline(
            self.store.get_chunk_evidence(chunk_ids, query.context),
            query.context.deadline_utc,
        )
        evidence_by_chunk: dict[str, list[str]] = {}
        for item in evidence:
            chunk = chunk_by_id.get(item.chunk_id)
            if chunk is None:
                raise KnowledgeWeaverError(
                    ErrorCode.MISSING_REFERENCE,
                    "Evidence references a chunk outside the retrieved candidate set.",
                    request_id=query.context.request_id,
                )
            try:
                item.assert_matches_chunk(chunk)
            except ValueError as exc:
                raise KnowledgeWeaverError(
                    ErrorCode.SCHEMA_INVALID,
                    "Stored evidence does not match its source-backed chunk.",
                    request_id=query.context.request_id,
                ) from exc
            evidence_by_chunk.setdefault(item.chunk_id, []).append(item.id)

        hits: list[RetrievalHit] = []
        warnings: list[str] = []
        for candidate in selected:
            chunk = chunk_by_id[candidate.chunk_id]
            if not chunk.is_leaf:
                continue
            evidence_ids = sorted(set(evidence_by_chunk.get(candidate.chunk_id, [])))
            if not evidence_ids:
                warnings.append(f"MISSING_EVIDENCE:{candidate.chunk_id}")
                continue
            hits.append(
                RetrievalHit(
                    chunk_id=candidate.chunk_id,
                    evidence_ids=evidence_ids,
                    score=candidate.raw_score,
                    channel=self.channel.value,
                    rank=candidate.rank,
                    contributions=[
                        {
                            "channel": self.channel.value,
                            "rank": candidate.rank,
                            "raw_score": candidate.raw_score,
                            "score_direction": candidate.score_direction.value,
                        }
                    ],
                )
            )
        return RetrievalResult(
            context=query.context,
            hits=hits,
            warnings=warnings,
            partial=bool(warnings),
            usage=Usage(
                elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                tool_calls=1,
                count_kind=CountKind.exact,
            ),
        )

    @staticmethod
    def _deduplicate(candidates: list[IndexCandidate]) -> list[IndexCandidate]:
        by_chunk: dict[str, IndexCandidate] = {}
        for candidate in candidates:
            if not math.isfinite(candidate.raw_score):
                raise KnowledgeWeaverError(
                    ErrorCode.SCHEMA_INVALID,
                    "Candidate index returned a non-finite score.",
                )
            previous = by_chunk.get(candidate.chunk_id)
            candidate_key = (
                candidate.rank,
                candidate.score_direction.value,
                candidate.raw_score,
            )
            if previous is None:
                by_chunk[candidate.chunk_id] = candidate
            else:
                previous_key = (
                    previous.rank,
                    previous.score_direction.value,
                    previous.raw_score,
                )
                if candidate_key < previous_key:
                    by_chunk[candidate.chunk_id] = candidate
        return sorted(by_chunk.values(), key=lambda candidate: (candidate.rank, candidate.chunk_id))


class Bm25Retriever(_IndexedRetriever):
    """Hydrate BM25 leaf candidates with generation-scoped persisted evidence."""

    channel = IndexChannel.bm25


class DenseRetriever(_IndexedRetriever):
    """Hydrate dense leaf candidates with generation-scoped persisted evidence."""

    channel = IndexChannel.dense


class SymbolRetriever(_IndexedRetriever):
    """Hydrate symbol leaf candidates with generation-scoped persisted evidence."""

    channel = IndexChannel.symbol


class EntityRetriever(_IndexedRetriever):
    """Hydrate entity-index leaf candidates with generation-scoped evidence."""

    channel = IndexChannel.entity
