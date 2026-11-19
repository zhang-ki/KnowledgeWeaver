from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

from knowledge_weaver.core.budget import BudgetLedger, BudgetReservation
from knowledge_weaver.core.config import RemoteUseApproval, RuntimeSettings
from knowledge_weaver.core.errors import ProviderUnavailable
from knowledge_weaver.models import CountKind, EmbeddingBatch, EmbeddingMode, ModelTokenUsage

from ._common import auth_headers, config_fingerprint, estimate_tokens
from ._retry import post_with_retries
from .http import HttpxJsonTransport, JsonTransport
from .profiles import LocalQwenProfile, OpenAIEmbeddingProfile


def _usage(raw: Any) -> ModelTokenUsage:
    if raw is None:
        return ModelTokenUsage()
    if not isinstance(raw, dict):
        raise ProviderUnavailable(
            "provider response usage is invalid", category="usage_invalid", retryable=False
        )
    fields: dict[str, int] = {}
    for name in ("prompt_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        value = raw.get(name)
        if value is None:
            continue
        if type(value) is not int or value < 0:
            raise ProviderUnavailable(
                "provider response usage is invalid", category="usage_invalid", retryable=False
            )
        fields[name] = value
    input_tokens = fields.get("prompt_tokens", fields.get("total_tokens", 0))
    return ModelTokenUsage(
        input_tokens=input_tokens,
        output_tokens=0,
        count_kind=CountKind.exact if "prompt_tokens" in fields else CountKind.unavailable,
        raw_fields=fields,
    )


def _batch(
    body: dict[str, Any],
    *,
    profile_id: str,
    model: str,
    expected_dimension: int | None,
    texts: list[str],
    usage: ModelTokenUsage,
    mode: EmbeddingMode,
    endpoint_url: str,
) -> EmbeddingBatch:
    items = body.get("data")
    if not isinstance(items, list) or len(items) != len(texts):
        raise ProviderUnavailable(
            "embedding response count did not match input",
            category="response_invalid",
            retryable=False,
        )
    ordered: list[list[float] | None] = [None] * len(texts)
    for item in items:
        if not isinstance(item, dict):
            raise ProviderUnavailable(
                "embedding response item is invalid",
                category="response_invalid",
                retryable=False,
            )
        index = item.get("index")
        vector = item.get("embedding")
        if type(index) is not int or index < 0 or index >= len(texts) or ordered[index] is not None:
            raise ProviderUnavailable(
                "embedding response order is invalid",
                category="response_invalid",
                retryable=False,
            )
        if not isinstance(vector, list) or not vector:
            raise ProviderUnavailable(
                "embedding vector is invalid",
                category="response_invalid",
                retryable=False,
            )
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
            raise ProviderUnavailable(
                "embedding vector contains invalid values",
                category="response_invalid",
                retryable=False,
            )
        ordered[index] = [float(value) for value in vector]
    vectors = [item for item in ordered if item is not None]
    if len(vectors) != len(texts):
        raise ProviderUnavailable(
            "embedding response omitted a vector",
            category="response_invalid",
            retryable=False,
        )
    dimension = expected_dimension or (len(vectors[0]) if vectors else 0)
    if dimension < 1 or any(len(vector) != dimension for vector in vectors):
        raise ProviderUnavailable(
            "embedding dimensions were inconsistent",
            category="response_invalid",
            retryable=False,
        )
    normalized = bool(vectors) and all(
        abs(math.sqrt(sum(value * value for value in vector)) - 1.0) <= 0.02
        for vector in vectors
    )
    revision_value = body.get("model")
    revision = revision_value if isinstance(revision_value, str) and revision_value else model
    fingerprint = config_fingerprint(
        profile_id,
        model,
        revision,
        str(dimension),
        mode,
        endpoint_url,
        "l2" if normalized else "raw",
    )
    try:
        return EmbeddingBatch(
            vectors=vectors,
            dimension=dimension,
            normalized=normalized,
            model=model,
            revision=revision,
            fingerprint=fingerprint,
            usage=usage,
        )
    except ValueError:
        raise ProviderUnavailable(
            "embedding response did not match the contract",
            category="response_invalid",
            retryable=False,
        ) from None


async def _embed(
    profile: OpenAIEmbeddingProfile | LocalQwenProfile,
    texts: list[str],
    mode: EmbeddingMode,
    budget: BudgetLedger,
    transport: JsonTransport,
    headers: dict[str, str],
    *,
    local_only: bool,
    sleep: Callable[[float], Awaitable[None]],
) -> EmbeddingBatch:
    if mode not in {"query", "document"}:
        raise ValueError("mode must be query or document")
    if not texts:
        raise ValueError("texts must not be empty")
    prefix = profile.query_prefix if mode == "query" else profile.document_prefix
    inputs = [prefix + item for item in texts]
    payload: dict[str, Any] = {
        profile.model_field: profile.model,
        profile.input_field: inputs,
    }
    if profile.dimensions is not None:
        dimensions_field = getattr(profile, "dimensions_field", None)
        if dimensions_field:
            payload[dimensions_field] = profile.dimensions
    estimate = sum(estimate_tokens(text) for text in inputs)

    async def reserve() -> BudgetReservation:
        cost = Decimal(0) if local_only else None
        return await budget.reserve_model_call(
            input_tokens_estimate=estimate,
            estimated_currency=cost,
        )

    body, reservation = await post_with_retries(
        transport,
        profile.endpoint_url,
        payload,
        headers,
        timeout_seconds=profile.timeout_seconds,
        max_attempts=profile.max_attempts,
        backoff_seconds=profile.retry_backoff_seconds,
        reserve=reserve,
        local_only=local_only,
        sleep=sleep,
    )
    try:
        usage = _usage(body.get("usage"))
        result = _batch(
            body,
            profile_id=profile.profile_id,
            model=profile.model,
            expected_dimension=profile.dimensions,
            texts=texts,
            usage=usage,
            mode=mode,
            endpoint_url=profile.endpoint_url,
        )
    except ProviderUnavailable:
        await reservation.finish(None)
        raise
    except (KeyError, IndexError, TypeError, ValueError, OverflowError):
        await reservation.finish(None)
        raise ProviderUnavailable(
            "embedding response could not be validated",
            category="response_invalid",
            retryable=False,
        ) from None
    await reservation.finish(usage)
    return result


class CloudEmbeddingProvider:
    """Explicit OpenAI-embeddings wire profile behind the cloud approval gate."""

    def __init__(
        self,
        profile: OpenAIEmbeddingProfile,
        approval: RemoteUseApproval,
        budget: BudgetLedger,
        *,
        settings: RuntimeSettings | None = None,
        transport: JsonTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.profile = profile
        self.approval = approval
        self.settings = settings or RuntimeSettings.from_environment()
        self.budget = budget
        self.transport = transport or HttpxJsonTransport()
        self.sleep = sleep

    async def embed(self, texts: list[str], mode: EmbeddingMode) -> EmbeddingBatch:
        self.approval.require_open(self.settings)
        headers = auth_headers(
            self.profile.api_key,
            header_name=self.profile.auth_header,
            scheme=self.profile.auth_scheme,
        )
        return await _embed(
            self.profile,
            texts,
            mode,
            self.budget,
            self.transport,
            headers,
            local_only=False,
            sleep=self.sleep,
        )


class LocalQwenEmbeddingProvider:
    """Loopback-only Qwen embeddings; independent of cloud credentials and approval."""

    def __init__(
        self,
        profile: LocalQwenProfile,
        budget: BudgetLedger,
        *,
        transport: JsonTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.profile = profile
        self.budget = budget
        self.transport = transport or HttpxJsonTransport()
        self.sleep = sleep

    async def embed(self, texts: list[str], mode: EmbeddingMode) -> EmbeddingBatch:
        headers = {"Content-Type": "application/json"}
        if self.profile.local_api_key is not None:
            key = self.profile.local_api_key.get_secret_value().strip()
            if not key:
                raise ProviderUnavailable(
                    "local embedding credential is missing",
                    category="configuration_missing",
                    retryable=False,
                )
            headers["Authorization"] = f"Bearer {key}"
        return await _embed(
            self.profile,
            texts,
            mode,
            self.budget,
            self.transport,
            headers,
            local_only=True,
            sleep=self.sleep,
        )
