"""Configurable model adapters with hard external-use gates and deterministic fakes."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import asyncio


_EMBED_STOPWORDS = {"a", "an", "and", "are", "does", "how", "is", "of", "or", "the", "to", "what", "which"}
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class ProviderUnavailable(RuntimeError):
    """A provider is not configured or the approved capability gate is closed."""


@dataclass(frozen=True)
class RemoteUseApproval:
    external_data_approved: bool = False
    budget_approved: bool = False
    calls_explicitly_enabled: bool = False
    currency_cap: float | None = None

    def require_open(self) -> None:
        if not (
            self.external_data_approved
            and self.budget_approved
            and self.calls_explicitly_enabled
            and self.currency_cap is not None
            and self.currency_cap > 0
            and os.environ.get("KW_ALLOW_REMOTE", "false").casefold() == "true"
            and os.environ.get("KW_MODEL_OFFLINE", "true").casefold() == "false"
        ):
            raise ProviderUnavailable("external model gate is closed")


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    dimension: int
    normalized: bool
    model: str
    revision: str
    fingerprint: str
    usage: dict[str, int | str]


class FakeEmbeddingProvider:
    """Stable hash embeddings for offline engineering tests; not model evidence."""

    def __init__(self, dimension: int = 32) -> None:
        self.dimension = dimension
        self.model = "fake-hash-embedding-v1"

    async def embed(self, texts: list[str], mode: str) -> EmbeddingBatch:
        if mode not in {"query", "document"}:
            raise ValueError("mode must be query or document")
        vectors = [self._vector(text) for text in texts]
        return EmbeddingBatch(
            vectors,
            self.dimension,
            True,
            self.model,
            "fixed-v1",
            f"{self.model}:{self.dimension}:camelcase-stopword-sha256-sign-v2",
            {"input_tokens": sum(max(1, len(text.split())) for text in texts), "count_kind": "estimated"},
        )

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        expanded = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
        tokens = [token for token in re.findall(r"[a-z0-9_]+", expanded.casefold()) if token not in _EMBED_STOPWORDS]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector


class QwenEmbeddingProvider:
    """OpenAI-compatible Qwen adapter. The configured endpoint is never guessed."""

    def __init__(self, approval: RemoteUseApproval, timeout_seconds: float = 20.0) -> None:
        self.approval = approval
        self.timeout_seconds = timeout_seconds

    async def embed(self, texts: list[str], mode: str) -> EmbeddingBatch:
        self.approval.require_open()
        return await asyncio.to_thread(self._embed_sync, texts, mode)

    def _embed_sync(self, texts: list[str], mode: str) -> EmbeddingBatch:
        self.approval.require_open()
        if mode not in {"query", "document"}:
            raise ValueError("mode must be query or document")
        base_url = os.environ.get("KW_EMBEDDING_BASE_URL", "").strip().rstrip("/")
        model = os.environ.get("KW_EMBEDDING_MODEL", "").strip()
        api_key = os.environ.get("KW_EMBEDDING_API_KEY", "")
        if not base_url or not model or not api_key:
            raise ProviderUnavailable("Qwen embedding endpoint, model, or credential is missing")
        payload = {"model": model, "input": texts}
        response = _post_json(f"{base_url}/embeddings", payload, api_key, self.timeout_seconds)
        items = response.get("data")
        if not isinstance(items, list) or len(items) != len(texts):
            raise ProviderUnavailable("Qwen embedding response shape is invalid")
        vectors = [item.get("embedding") for item in sorted(items, key=lambda item: item.get("index", 0))]
        if any(not isinstance(vector, list) or not vector for vector in vectors):
            raise ProviderUnavailable("Qwen embedding response omitted vectors")
        dimension = len(vectors[0])
        if any(len(vector) != dimension for vector in vectors):
            raise ProviderUnavailable("Qwen embedding dimensions are inconsistent")
        usage = response.get("usage", {})
        revision = str(response.get("model", model))
        return EmbeddingBatch(
            vectors,
            dimension,
            False,
            model,
            revision,
            f"qwen:{model}:{revision}:{dimension}:configured-endpoint",
            {"input_tokens": int(usage.get("prompt_tokens", 0)), "count_kind": "exact" if usage else "unavailable"},
        )


@dataclass(frozen=True)
class LLMResult:
    parsed_json: dict[str, Any]
    model: str
    revision: str
    usage: dict[str, int | str]
    provider_request_id: str | None = None


@dataclass(frozen=True)
class LLMRequest:
    system_instruction: str
    untrusted_context: str
    response_schema: dict[str, Any]
    max_output_tokens: int
    timeout_seconds: float


class FakeGenerationProvider:
    """Deterministic local provider. Output is fixture behavior, not model quality."""

    def __init__(self, timeout: bool = False) -> None:
        self.timeout = timeout

    async def generate(self, request: LLMRequest) -> LLMResult:
        if self.timeout:
            raise TimeoutError("deterministic fake provider timeout")
        payload = json.loads(request.untrusted_context)
        conflict = payload.get("conflicts", [])
        if conflict:
            left, right = conflict[0]["evidence_sides"]
            result = {
                "status": "conflicted",
                "answer": "The sources conflict: the PDF says the demo cache uses PersistentStore, while the Python comment says it uses ProcessMemory. The available evidence does not resolve which is correct.",
                "claims": [
                    {"text": left["claim"], "evidence_ids": left["evidence_ids"], "relation_ids": left["relation_ids"]},
                    {"text": right["claim"], "evidence_ids": right["evidence_ids"], "relation_ids": right["relation_ids"]},
                ],
                "unanswered": ["Which cache implementation is authoritative?"],
            }
        elif payload.get("paths"):
            path = payload["paths"][0]
            result = {
                "status": "answered",
                "answer": "The PDF links the ingestion pipeline to Evidence, and the Python type annotation links EvidenceRecord.span to SourceSpan.",
                "claims": [{"text": "The two sources form a pipeline → Evidence → SourceSpan chain.", "evidence_ids": path["evidence_ids"], "relation_ids": path["relation_ids"]}],
                "unanswered": [],
            }
        elif payload.get("evidence"):
            evidence = next(
                (item for item in payload["evidence"] if "span: SourceSpan" in item["quote"]),
                payload["evidence"][0],
            )
            result = {
                "status": "answered",
                "answer": (
                    "EvidenceRecord.span is annotated as SourceSpan."
                    if "span: SourceSpan" in evidence["quote"]
                    else f"The source shows: {evidence['quote']}"
                ),
                "claims": [
                    {
                        "text": "EvidenceRecord.span is annotated as SourceSpan."
                        if "span: SourceSpan" in evidence["quote"]
                        else evidence["quote"],
                        "evidence_ids": [evidence["id"]],
                    }
                ],
                "unanswered": [],
            }
        else:
            result = {"status": "insufficient", "answer": "The corpus has no supporting evidence.", "claims": [], "unanswered": ["No supporting source was found."]}
        return LLMResult(result, "fake-generation-v1", "fixed-v1", {"input_tokens": 0, "output_tokens": 0, "count_kind": "unavailable"})


class DeepSeekGenerationProvider:
    """OpenAI-compatible DeepSeek chat adapter, gated against unapproved egress."""

    def __init__(self, approval: RemoteUseApproval) -> None:
        self.approval = approval

    async def generate(self, request: LLMRequest) -> LLMResult:
        self.approval.require_open()
        return await asyncio.to_thread(self._generate_sync, request)

    def _generate_sync(self, request: LLMRequest) -> LLMResult:
        self.approval.require_open()
        base_url = os.environ.get("KW_LLM_BASE_URL", "").strip().rstrip("/")
        model = os.environ.get("KW_LLM_MODEL", "").strip()
        api_key = os.environ.get("KW_LLM_API_KEY", "")
        if not base_url or not model or not api_key:
            raise ProviderUnavailable("DeepSeek endpoint, model, or credential is missing")
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system_instruction},
                {"role": "user", "content": request.untrusted_context},
            ],
            "max_tokens": request.max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        response = _post_json(f"{base_url}/chat/completions", payload, api_key, request.timeout_seconds)
        try:
            content = response["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderUnavailable("DeepSeek response did not match the JSON contract") from exc
        usage = response.get("usage", {})
        return LLMResult(
            parsed,
            model,
            str(response.get("model", model)),
            {
                "input_tokens": int(usage.get("prompt_tokens", 0)),
                "output_tokens": int(usage.get("completion_tokens", 0)),
                "count_kind": "exact" if usage else "unavailable",
            },
            str(response.get("id")) if response.get("id") else None,
        )


def _post_json(url: str, payload: dict[str, Any], api_key: str, timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        # Never attach headers, endpoint query parameters, response body, or credentials.
        raise ProviderUnavailable(f"configured provider request failed: {type(exc).__name__}") from None
    if not isinstance(result, dict):
        raise ProviderUnavailable("configured provider returned an invalid JSON object")
    return result
