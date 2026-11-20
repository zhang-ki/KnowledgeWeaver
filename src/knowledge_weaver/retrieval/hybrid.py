from __future__ import annotations

import asyncio
import hashlib
import math
import time
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, TypeVar

from knowledge_weaver.core.budget import BudgetLedger, BudgetReservation
from knowledge_weaver.core.errors import ErrorCode, KnowledgeWeaverError
from knowledge_weaver.models import (
    CountKind,
    Path,
    RetrievalHit,
    RetrievalQuery,
    RetrievalResult,
    Usage,
)
from knowledge_weaver.ports import Reranker, Retriever

from .rrf import reciprocal_rank_fusion

MAX_RERANK_CANDIDATES = 40
_FATAL_CHANNEL_CODES = {
    ErrorCode.INVALID_INPUT,
    ErrorCode.MISSING_REFERENCE,
    ErrorCode.NO_READY_GENERATION,
    ErrorCode.GENERATION_CONFLICT,
    ErrorCode.SCHEMA_INVALID,
}


@dataclass(frozen=True, slots=True)
class RerankRecord:
    """Safe, structured audit summary; it never stores query text or credentials."""

    query_sha256: str
    status: Literal["disabled", "not_needed", "applied", "degraded"]
    input_chunk_ids: tuple[str, ...]
    ranked_chunk_ids: tuple[str, ...]
    scores: tuple[tuple[str, float], ...] = ()
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievalRun:
    """Detailed B04 response; callers may persist the rerank record alongside it."""

    result: RetrievalResult
    rerank_record: RerankRecord


@dataclass(frozen=True, slots=True)
class _ChannelOutcome:
    name: str
    result: RetrievalResult | None = None
    warning: str | None = None


class HybridRetriever:
    """Run injected Retriever channels, fuse with fixed RRF, and optionally rerank."""

    def __init__(
        self,
        channels: Mapping[str, Retriever],
        reranker: Reranker | None = None,
        *,
        channel_top_k: Mapping[str, int] | None = None,
        budget: BudgetLedger | None = None,
    ) -> None:
        if not channels:
            raise KnowledgeWeaverError(
                ErrorCode.INVALID_INPUT, "At least one retrieval channel is required."
            )
        if any(not name.strip() for name in channels):
            raise KnowledgeWeaverError(
                ErrorCode.INVALID_INPUT, "Retrieval channel names must be non-empty."
            )
        self.channels = dict(channels)
        self.reranker = reranker
        self.budget = budget
        self.channel_top_k = dict(channel_top_k or {})
        if set(self.channel_top_k) - set(self.channels):
            raise KnowledgeWeaverError(
                ErrorCode.INVALID_INPUT,
                "Per-channel top_k was configured for an unknown channel.",
            )
        if any(not 1 <= value <= 100 for value in self.channel_top_k.values()):
            raise KnowledgeWeaverError(
                ErrorCode.INVALID_INPUT,
                "Per-channel top_k must be in the range 1..100.",
            )

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        return (await self.retrieve_detailed(query)).result

    async def retrieve_detailed(self, query: RetrievalQuery) -> RetrievalRun:
        started = time.monotonic()
        names = sorted(self.channels)
        raw_outcomes = await asyncio.gather(
            *(self._run_channel(name, query) for name in names),
            return_exceptions=True,
        )
        for outcome in raw_outcomes:
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, KnowledgeWeaverError) and outcome.code in _FATAL_CHANNEL_CODES:
                raise outcome

        outcomes: list[_ChannelOutcome] = []
        for name, outcome in zip(names, raw_outcomes, strict=True):
            if isinstance(outcome, _ChannelOutcome):
                outcomes.append(outcome)
            elif isinstance(outcome, BaseException):
                outcomes.append(_ChannelOutcome(name, warning=f"CHANNEL_FAILED:{type(outcome).__name__}"))
            else:
                raise TypeError("retrieval channel returned an invalid internal outcome")

        successes = [outcome for outcome in outcomes if outcome.result is not None]
        if not successes:
            details = {outcome.name: outcome.warning for outcome in outcomes}
            failure_codes = {outcome.warning for outcome in outcomes}
            error_code = (
                ErrorCode.DEADLINE_EXCEEDED
                if failure_codes == {ErrorCode.DEADLINE_EXCEEDED.value}
                else ErrorCode.BUDGET_EXHAUSTED
                if failure_codes == {ErrorCode.BUDGET_EXHAUSTED.value}
                else ErrorCode.INDEX_NOT_READY
            )
            raise KnowledgeWeaverError(
                error_code,
                "Every retrieval channel failed before producing a result.",
                request_id=query.context.request_id,
                retryable=True,
                details={"channels": details},
            )

        channel_hits: dict[str, list[RetrievalHit]] = {}
        paths_by_id: dict[str, Path] = {}
        warnings: list[str] = []
        partial = False
        usages: list[Usage] = []
        for outcome in outcomes:
            if outcome.warning:
                warnings.append(f"{outcome.name}:{outcome.warning}")
                partial = True
            if outcome.result is None:
                continue
            result = outcome.result
            if result.context != query.context:
                raise KnowledgeWeaverError(
                    ErrorCode.GENERATION_CONFLICT,
                    "A retrieval channel returned a result for a different query context.",
                    request_id=query.context.request_id,
                    details={"channel": outcome.name},
                )
            usages.append(result.usage)
            partial = partial or result.partial
            warnings.extend(f"{outcome.name}:{warning}" for warning in result.warnings)
            for path in result.paths:
                previous = paths_by_id.get(path.id)
                if previous is not None and previous != path:
                    raise KnowledgeWeaverError(
                        ErrorCode.SCHEMA_INVALID,
                        "Retrieval channels returned conflicting records for one path ID.",
                        request_id=query.context.request_id,
                    )
                paths_by_id[path.id] = path

            normalized_hits: list[RetrievalHit] = []
            for hit in result.hits:
                missing_paths = set(hit.path_ids) - set(paths_by_id)
                if missing_paths:
                    raise KnowledgeWeaverError(
                        ErrorCode.MISSING_REFERENCE,
                        "A retrieval hit references a path absent from its result.",
                        request_id=query.context.request_id,
                        details={"channel": outcome.name},
                    )
                path_evidence = {
                    evidence_id
                    for path_id in hit.path_ids
                    for step in paths_by_id[path_id].steps
                    for evidence_id in step.evidence_ids
                }
                normalized_hits.append(
                    hit.model_copy(
                        update={
                            "evidence_ids": sorted(set(hit.evidence_ids) | path_evidence)
                        }
                    )
                )
            channel_hits[outcome.name] = normalized_hits

        try:
            fused = reciprocal_rank_fusion(channel_hits)
        except (TypeError, ValueError) as exc:
            raise KnowledgeWeaverError(
                ErrorCode.SCHEMA_INVALID,
                "A retrieval channel returned invalid rank or score metadata.",
                request_id=query.context.request_id,
            ) from exc
        reranked, rerank_record, rerank_warning = await self._rerank(query, fused)
        if rerank_warning:
            warnings.append(rerank_warning)
            partial = True
        final_hits = [
            hit.model_copy(update={"rank": rank})
            for rank, hit in enumerate(reranked[: query.top_k], start=1)
        ]
        used_path_ids = {path_id for hit in final_hits for path_id in hit.path_ids}
        result_paths = [
            path for path_id, path in sorted(paths_by_id.items()) if path_id in used_path_ids
        ]
        result = RetrievalResult(
            context=query.context,
            hits=final_hits,
            paths=result_paths,
            warnings=warnings,
            partial=partial,
            usage=_aggregate_usage(usages, max(0, int((time.monotonic() - started) * 1000))),
        )
        return RetrievalRun(result=result, rerank_record=rerank_record)

    async def _run_channel(self, name: str, query: RetrievalQuery) -> _ChannelOutcome:
        reservation: BudgetReservation | None = None
        try:
            if self.budget is not None:
                reservation = await self.budget.reserve_tool_call()
            channel_query = query.model_copy(
                update={"top_k": self.channel_top_k.get(name, query.top_k)}
            )
            result = await _until_deadline(
                self.channels[name].retrieve(channel_query), query.context.deadline_utc
            )
            if result.context != query.context:
                raise KnowledgeWeaverError(
                    ErrorCode.GENERATION_CONFLICT,
                    "A retrieval channel returned a result for a different query context.",
                    request_id=query.context.request_id,
                    details={"channel": name},
                )
            return _ChannelOutcome(name=name, result=result)
        except KnowledgeWeaverError as exc:
            if exc.code in _FATAL_CHANNEL_CODES:
                raise
            return _ChannelOutcome(name=name, warning=exc.code.value)
        except TimeoutError:
            return _ChannelOutcome(name=name, warning=ErrorCode.DEADLINE_EXCEEDED.value)
        except Exception as exc:  # noqa: BLE001 - a broken channel must be isolated and reported.
            return _ChannelOutcome(name=name, warning=f"CHANNEL_FAILED:{type(exc).__name__}")
        finally:
            if reservation is not None and not reservation.settled:
                await reservation.finish()

    async def _rerank(
        self, query: RetrievalQuery, hits: list[RetrievalHit]
    ) -> tuple[list[RetrievalHit], RerankRecord, str | None]:
        query_fingerprint = hashlib.sha256(query.text.encode("utf-8")).hexdigest()
        input_hits = hits[:MAX_RERANK_CANDIDATES]
        input_ids = tuple(hit.chunk_id for hit in hits)
        if self.reranker is None:
            record = RerankRecord(
                query_sha256=query_fingerprint,
                status="disabled",
                input_chunk_ids=input_ids,
                ranked_chunk_ids=input_ids,
            )
            return hits, record, None
        if not input_hits:
            record = RerankRecord(
                query_sha256=query_fingerprint,
                status="not_needed",
                input_chunk_ids=(),
                ranked_chunk_ids=(),
            )
            return hits, record, None

        limit = min(query.top_k, len(input_hits))
        try:
            response = await _until_deadline(
                self.reranker.rerank(
                    query.text, input_hits, query.context, limit
                ),
                query.context.deadline_utc,
            )
            original = {hit.chunk_id: hit for hit in input_hits}
            response_ids = [hit.chunk_id for hit in response]
            if not response or len(response_ids) != len(set(response_ids)):
                raise ValueError("reranker returned no hits or duplicate chunk IDs")
            if set(response_ids) - set(original):
                raise ValueError("reranker returned an unknown chunk ID")

            ranked: list[RetrievalHit] = []
            scores: list[tuple[str, float]] = []
            for returned in sorted(response, key=lambda hit: (hit.rank, hit.chunk_id)):
                source = original[returned.chunk_id]
                if set(returned.evidence_ids) - set(source.evidence_ids):
                    raise ValueError("reranker attempted to introduce evidence IDs")
                if set(returned.path_ids) - set(source.path_ids):
                    raise ValueError("reranker attempted to introduce path IDs")
                score = returned.rerank_score
                if score is not None:
                    if not math.isfinite(score):
                        raise ValueError("reranker returned a non-finite score")
                    scores.append((source.chunk_id, score))
                ranked.append(source.model_copy(update={"rerank_score": score}))

            ranked_ids = {hit.chunk_id for hit in ranked}
            ranked.extend(hit for hit in input_hits if hit.chunk_id not in ranked_ids)
            ranked.extend(hits[MAX_RERANK_CANDIDATES:])
            returned_short = len(response) < limit
            status: Literal["applied", "degraded"] = "degraded" if returned_short else "applied"
            record = RerankRecord(
                query_sha256=query_fingerprint,
                status=status,
                input_chunk_ids=tuple(hit.chunk_id for hit in input_hits),
                ranked_chunk_ids=tuple(hit.chunk_id for hit in ranked),
                scores=tuple(scores),
            )
            warning = (
                f"RERANK_PARTIAL:{len(response)}_OF_{limit}" if returned_short else None
            )
            return ranked, record, warning
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - rerank failure degrades to the RRF ordering.
            record = RerankRecord(
                query_sha256=query_fingerprint,
                status="degraded",
                input_chunk_ids=tuple(hit.chunk_id for hit in input_hits),
                ranked_chunk_ids=tuple(hit.chunk_id for hit in hits),
                error_type=type(exc).__name__,
            )
            return hits, record, f"RERANK_FAILED:{type(exc).__name__}"


_T = TypeVar("_T")


async def _until_deadline(awaitable: Awaitable[_T], deadline: datetime) -> _T:
    remaining = (deadline.astimezone(UTC) - datetime.now(UTC)).total_seconds()
    if remaining <= 0:
        raise TimeoutError
    async with asyncio.timeout(remaining):
        return await awaitable


def _aggregate_usage(usages: list[Usage], elapsed_ms: int) -> Usage:
    if not usages:
        return Usage(elapsed_ms=elapsed_ms, count_kind=CountKind.unavailable)
    count_kind = (
        CountKind.unavailable
        if all(item.count_kind == CountKind.unavailable for item in usages)
        else CountKind.exact
        if all(item.count_kind == CountKind.exact for item in usages)
        else CountKind.estimated
    )
    currency_values = [item.currency_cost for item in usages if item.currency_cost is not None]
    price_snapshots = [item.price_snapshot for item in usages if item.price_snapshot is not None]
    return Usage(
        elapsed_ms=elapsed_ms,
        tool_calls=sum(item.tool_calls for item in usages),
        llm_calls=sum(item.llm_calls for item in usages),
        input_tokens=sum(item.input_tokens for item in usages),
        output_tokens=sum(item.output_tokens for item in usages),
        count_kind=count_kind,
        currency_cost=sum(currency_values, Decimal(0)) if currency_values else None,
        price_snapshot=(
            price_snapshots[0]
            if price_snapshots and all(item == price_snapshots[0] for item in price_snapshots)
            else None
        ),
    )
