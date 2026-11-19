from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from knowledge_weaver.models import (
    CountKind,
    EmbeddingBatch,
    EmbeddingMode,
    LLMRequest,
    LLMResult,
    ModelTokenUsage,
    RetrievalHit,
)

from ._common import config_fingerprint, estimate_tokens


class FakeGenerationProvider:
    """Deterministic fixture provider; output is not model-quality evidence."""

    def __init__(
        self,
        response: dict[str, Any] | None = None,
        *,
        timeout: bool = False,
    ) -> None:
        self.response = response or {"status": "insufficient", "claims": []}
        self.timeout = timeout
        self.calls = 0

    async def generate(self, request: LLMRequest) -> LLMResult:
        self.calls += 1
        if self.timeout:
            raise TimeoutError("deterministic fake provider timeout")
        return LLMResult(
            parsed_json=dict(self.response),
            usage=ModelTokenUsage(),
            provider_request_id=None,
            model="fake-generation-v1",
            revision="fixed-v1",
        )


class FakeEmbeddingProvider:
    """Stable hash vectors for offline contract tests, never a real embedding model."""

    def __init__(self, dimension: int = 32) -> None:
        if dimension < 1:
            raise ValueError("dimension must be positive")
        self.dimension = dimension
        self.calls = 0

    async def embed(self, texts: list[str], mode: EmbeddingMode) -> EmbeddingBatch:
        if mode not in {"query", "document"}:
            raise ValueError("mode must be query or document")
        if not texts:
            raise ValueError("texts must not be empty")
        self.calls += 1
        vectors = [self._vector(text) for text in texts]
        return EmbeddingBatch(
            vectors=vectors,
            dimension=self.dimension,
            normalized=True,
            model="fake-hash-embedding-v1",
            revision="fixed-v1",
            fingerprint=config_fingerprint("fake-hash-embedding-v1", str(self.dimension), mode),
            usage=ModelTokenUsage(
                input_tokens=sum(estimate_tokens(text) for text in texts),
                output_tokens=0,
                count_kind=CountKind.estimated,
            ),
        )

    def _vector(self, text: str) -> list[float]:
        values = [0.0] * self.dimension
        tokens = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text.casefold())
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            values[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in values))
        if norm:
            return [value / norm for value in values]
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values[int.from_bytes(digest[:4], "big") % self.dimension] = 1.0
        return values

    async def rerank(
        self,
        query: str,
        hits: list[RetrievalHit],
        context: Any,
        limit: int,
    ) -> list[RetrievalHit]:
        if limit < 1:
            raise ValueError("limit must be positive")
        self.calls += 1
        return [
            hit.model_copy(update={"rank": rank, "rerank_score": hit.score})
            for rank, hit in enumerate(hits[:limit], start=1)
        ]


class FakeProvider:
    """Combined offline implementation for tests and local contract checks only."""

    def __init__(
        self,
        *,
        dimension: int = 32,
        generation_response: dict[str, Any] | None = None,
    ) -> None:
        self._generation = FakeGenerationProvider(generation_response)
        self._embedding = FakeEmbeddingProvider(dimension)

    @property
    def generation_calls(self) -> int:
        return self._generation.calls

    @property
    def embedding_calls(self) -> int:
        return self._embedding.calls

    async def generate(self, request: LLMRequest) -> LLMResult:
        return await self._generation.generate(request)

    async def embed(self, texts: list[str], mode: EmbeddingMode) -> EmbeddingBatch:
        return await self._embedding.embed(texts, mode)

    async def rerank(
        self, query: str, hits: list[RetrievalHit], context: Any, limit: int
    ) -> list[RetrievalHit]:
        return await self._embedding.rerank(query, hits, context, limit)
