from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

from knowledge_weaver.core.budget import BudgetLedger, BudgetReservation
from knowledge_weaver.core.config import RemoteUseApproval, RuntimeSettings
from knowledge_weaver.core.errors import ProviderUnavailable
from knowledge_weaver.models import ModelTokenUsage, QueryContext, RetrievalHit

from ._common import auth_headers, estimate_tokens, parse_token_usage
from ._retry import post_with_retries
from .http import HttpxJsonTransport, JsonTransport
from .profiles import RerankProfile


class ChunkTextResolver(Protocol):
    async def resolve_texts(
        self, chunk_ids: list[str], context: QueryContext
    ) -> Mapping[str, str]: ...


class CloudReranker:
    """Profile-driven rerank endpoint that preserves citation and path metadata."""

    def __init__(
        self,
        profile: RerankProfile,
        approval: RemoteUseApproval,
        budget: BudgetLedger,
        text_resolver: ChunkTextResolver,
        *,
        settings: RuntimeSettings | None = None,
        transport: JsonTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.profile = profile
        self.approval = approval
        self.settings = settings or RuntimeSettings.from_environment()
        self.budget = budget
        self.text_resolver = text_resolver
        self.transport = transport or HttpxJsonTransport()
        self.sleep = sleep

    async def rerank(
        self, query: str, hits: list[RetrievalHit], context: QueryContext, limit: int
    ) -> list[RetrievalHit]:
        self.approval.require_open(self.settings)
        if not query.strip() or limit < 1:
            raise ValueError("query must be non-empty and limit positive")
        if len(hits) > 40:
            raise ValueError("rerank candidate count must not exceed 40")
        if not hits:
            return []
        headers = auth_headers(
            self.profile.api_key,
            header_name=self.profile.auth_header,
            scheme=self.profile.auth_scheme,
        )
        chunk_ids = [hit.chunk_id for hit in hits]
        texts = await self.text_resolver.resolve_texts(chunk_ids, context)
        if any(chunk_id not in texts for chunk_id in chunk_ids):
            raise ProviderUnavailable(
                "rerank input referenced a missing chunk",
                category="missing_reference",
                retryable=False,
            )
        documents = [texts[chunk_id] for chunk_id in chunk_ids]
        top_n = min(limit, len(hits))
        payload = {
            self.profile.model_field: self.profile.model,
            self.profile.query_field: query,
            self.profile.documents_field: documents,
            self.profile.limit_field: top_n,
        }
        input_estimate = estimate_tokens(query) + sum(estimate_tokens(text) for text in documents)

        async def reserve() -> BudgetReservation:
            return await self.budget.reserve_model_call(input_tokens_estimate=input_estimate)

        body, reservation = await post_with_retries(
            self.transport,
            self.profile.endpoint_url,
            payload,
            headers,
            timeout_seconds=self.profile.timeout_seconds,
            max_attempts=self.profile.max_attempts,
            backoff_seconds=self.profile.retry_backoff_seconds,
            reserve=reserve,
            sleep=self.sleep,
        )
        try:
            raw_results = body.get(self.profile.results_field)
            if not isinstance(raw_results, list) or len(raw_results) > top_n:
                raise ProviderUnavailable(
                    "rerank response results were invalid",
                    category="response_invalid",
                    retryable=False,
                )
            parsed: list[tuple[int, float]] = []
            seen: set[int] = set()
            for item in raw_results:
                if not isinstance(item, dict):
                    raise ProviderUnavailable(
                        "rerank response item was invalid",
                        category="response_invalid",
                        retryable=False,
                    )
                index = item.get(self.profile.index_field)
                score = item.get(self.profile.score_field)
                if (
                    not isinstance(index, int)
                    or isinstance(index, bool)
                    or index < 0
                    or index >= len(hits)
                    or index in seen
                    or not isinstance(score, (int, float))
                    or isinstance(score, bool)
                    or not math.isfinite(float(score))
                ):
                    raise ProviderUnavailable(
                        "rerank response item was invalid",
                        category="response_invalid",
                        retryable=False,
                    )
                seen.add(index)
                parsed.append((index, float(score)))
            reverse = self.profile.score_direction == "higher"
            parsed.sort(key=lambda row: ((-row[1] if reverse else row[1]), row[0]))
            ranked = [
                hits[index].model_copy(update={"rank": rank, "rerank_score": score})
                for rank, (index, score) in enumerate(parsed, start=1)
            ]
            usage = (
                parse_token_usage(body.get("usage"))
                if body.get("usage") is not None
                else ModelTokenUsage()
            )
        except ProviderUnavailable:
            await reservation.finish(None)
            raise
        except (KeyError, IndexError, TypeError, ValueError, OverflowError):
            await reservation.finish(None)
            raise ProviderUnavailable(
                "rerank response could not be validated",
                category="response_invalid",
                retryable=False,
            ) from None
        await reservation.finish(usage)
        return ranked
