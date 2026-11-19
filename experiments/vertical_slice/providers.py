"""Configurable model adapters with hard external-use gates and deterministic fakes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, ClassVar

_EMBED_STOPWORDS = {"a", "an", "and", "are", "does", "how", "is", "of", "or", "the", "to", "what", "which"}


class ProviderUnavailable(RuntimeError):
    """A provider is not configured or the approved capability gate is closed."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "provider_unavailable",
        retryable: bool = False,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.http_status = http_status


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
            and (self.currency_cap is None or self.currency_cap > 0)
            and os.environ.get("KW_ALLOW_REMOTE", "false").casefold() == "true"
            and os.environ.get("KW_MODEL_OFFLINE", "true").casefold() == "false"
        ):
            raise ProviderUnavailable(
                "external model gate is closed",
                category="approval_gate",
                retryable=False,
            )


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
    """Local-only OpenAI-compatible Qwen adapter; it never uses the cloud gate."""

    _LOOPBACK_HOSTS: ClassVar[set[str]] = {"localhost", "127.0.0.1", "::1"}

    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def embed(self, texts: list[str], mode: str) -> EmbeddingBatch:
        return await asyncio.to_thread(self._embed_sync, texts, mode)

    def _embed_sync(self, texts: list[str], mode: str) -> EmbeddingBatch:
        if mode not in {"query", "document"}:
            raise ValueError("mode must be query or document")
        if not isinstance(texts, list) or not texts or any(not isinstance(text, str) for text in texts):
            raise ValueError("texts must be a non-empty list of strings")
        base_url = os.environ.get("KW_EMBEDDING_BASE_URL", "").strip().rstrip("/")
        model = os.environ.get("KW_EMBEDDING_MODEL", "").strip()
        if not base_url or not model:
            raise ProviderUnavailable("local Qwen embedding endpoint or model is missing")
        base_url = self._validate_loopback_endpoint(base_url)
        local_api_key = os.environ.get("KW_LOCAL_EMBEDDING_API_KEY", "").strip() or None
        payload = {"model": model, "input": texts}
        response = _post_json(
            f"{base_url}/embeddings",
            payload,
            local_api_key,
            self.timeout_seconds,
            local_only=True,
        )
        items = response.get("data")
        if not isinstance(items, list) or len(items) != len(texts):
            raise ProviderUnavailable("Qwen embedding response shape is invalid")
        vectors: list[list[float]] = []
        dimension: int | None = None
        for expected_index, item in enumerate(items):
            if not isinstance(item, dict) or type(item.get("index")) is not int or item["index"] != expected_index:
                raise ProviderUnavailable("Qwen embedding response order is invalid")
            raw_vector = item.get("embedding")
            if not isinstance(raw_vector, list) or not raw_vector:
                raise ProviderUnavailable("Qwen embedding response omitted a non-empty vector")
            if dimension is None:
                dimension = len(raw_vector)
            elif len(raw_vector) != dimension:
                raise ProviderUnavailable("Qwen embedding dimensions are inconsistent")
            vector: list[float] = []
            for value in raw_vector:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ProviderUnavailable("Qwen embedding vector values are invalid")
                try:
                    numeric_value = float(value)
                except (OverflowError, ValueError):
                    raise ProviderUnavailable("Qwen embedding vector values are invalid") from None
                if not math.isfinite(numeric_value):
                    raise ProviderUnavailable("Qwen embedding vector values are invalid")
                vector.append(numeric_value)
            vectors.append(vector)
        if dimension is None or dimension == 0:
            raise ProviderUnavailable("Qwen embedding dimension must be non-zero")
        usage = response.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise ProviderUnavailable("Qwen embedding response usage is invalid")
        prompt_tokens = _validated_token_count(usage or {}, "prompt_tokens")
        response_model = response.get("model")
        revision = response_model if isinstance(response_model, str) and response_model else model
        return EmbeddingBatch(
            vectors,
            dimension,
            False,
            model,
            revision,
            f"qwen-local:{model}:{revision}:{dimension}:configured-loopback-endpoint",
            {
                "input_tokens": prompt_tokens if prompt_tokens is not None else 0,
                "count_kind": "exact" if prompt_tokens is not None else "unavailable",
            },
        )

    @classmethod
    def _validate_loopback_endpoint(cls, base_url: str) -> str:
        try:
            parsed = urllib.parse.urlsplit(base_url)
            hostname = (parsed.hostname or "").casefold().rstrip(".")
            _ = parsed.port  # Validate malformed ports before any request can be attempted.
        except ValueError:
            raise ProviderUnavailable("Qwen embedding endpoint URL is invalid") from None
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or hostname not in cls._LOOPBACK_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ProviderUnavailable("Qwen embedding endpoint must be an HTTP(S) loopback URL")
        return base_url


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
        api_key = os.environ.get("KW_LLM_API_KEY", "").strip()
        if not base_url or not model or not api_key:
            raise ProviderUnavailable(
                "DeepSeek endpoint, model, or credential is missing",
                category="configuration_missing",
                retryable=False,
            )
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
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            raise ProviderUnavailable(
                "DeepSeek response did not match the JSON contract",
                category="response_invalid",
                retryable=False,
            ) from None
        if not isinstance(parsed, dict):
            raise ProviderUnavailable(
                "DeepSeek response did not match the JSON contract",
                category="response_invalid",
                retryable=False,
            )
        usage = response.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise ProviderUnavailable(
                "DeepSeek response usage is invalid",
                category="usage_invalid",
                retryable=False,
            )
        usage = usage or {}
        prompt_tokens = _validated_token_count(usage, "prompt_tokens")
        completion_tokens = _validated_token_count(usage, "completion_tokens")
        usage_is_exact = prompt_tokens is not None and completion_tokens is not None
        preserved_usage: dict[str, int | str] = {}
        for field in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
            "cache_hit_tokens",
            "cache_miss_tokens",
        ):
            value = _validated_token_count(usage, field)
            if value is not None:
                preserved_usage[field] = value
        response_model = response.get("model")
        revision = response_model if isinstance(response_model, str) and response_model else model
        normalized_usage: dict[str, int | str] = {
            "input_tokens": prompt_tokens if prompt_tokens is not None else 0,
            "output_tokens": completion_tokens if completion_tokens is not None else 0,
            "count_kind": "exact" if usage_is_exact else "unavailable",
        }
        normalized_usage.update(preserved_usage)
        return LLMResult(
            parsed,
            model,
            revision,
            normalized_usage,
            str(response.get("id")) if response.get("id") else None,
        )


def _validated_token_count(usage: dict[str, Any], field: str) -> int | None:
    value = usage.get(field)
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ProviderUnavailable(
            "provider response usage is invalid",
            category="usage_invalid",
            retryable=False,
        )
    return value


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_json_request(request: urllib.request.Request, timeout: float, *, local_only: bool):
    if local_only:
        # Do not let proxy settings or redirects turn a loopback request into egress.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirectHandler())
        return opener.open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)


def _post_json(
    url: str,
    payload: dict[str, Any],
    api_key: str | None,
    timeout: float,
    *,
    local_only: bool = False,
) -> dict[str, Any]:
    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ProviderUnavailable(
            "configured provider request could not be serialized",
            category="request_invalid",
            retryable=False,
        ) from None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        request = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method="POST",
        )
    except (TypeError, ValueError, UnicodeError):
        raise ProviderUnavailable(
            "configured provider request URL is invalid",
            category="request_invalid",
            retryable=False,
        ) from None
    try:
        with _open_json_request(request, timeout=timeout, local_only=local_only) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Only the numeric status crosses this boundary; do not read or expose the
        # response body, headers, URL, reason text, or credential-bearing request.
        status = exc.code if type(exc.code) is int else None
        if status in {408, 425, 429}:
            category, retryable = "http_transient", True
        elif status is not None and 500 <= status <= 599:
            category, retryable = "http_server_error", True
        elif status in {401, 403}:
            category, retryable = "http_auth", False
        elif status is not None and 400 <= status <= 499:
            category, retryable = "http_client_error", False
        else:
            category, retryable = "http_error", False
        label = f"configured provider returned HTTP {status}" if status is not None else "configured provider returned an HTTP error"
        raise ProviderUnavailable(
            label,
            category=category,
            retryable=retryable,
            http_status=status,
        ) from None
    except urllib.error.URLError as exc:
        # URLError.reason may contain DNS, proxy, or URL details; keep classification only.
        is_timeout = isinstance(exc.reason, TimeoutError)
        raise ProviderUnavailable(
            "configured provider request timed out" if is_timeout else "configured provider network request failed",
            category="timeout" if is_timeout else "network_error",
            retryable=True,
        ) from None
    except (TimeoutError, OSError) as exc:
        is_timeout = isinstance(exc, TimeoutError)
        # Never attach exception text: it can contain endpoints or other request data.
        raise ProviderUnavailable(
            "configured provider request timed out" if is_timeout else "configured provider network request failed",
            category="timeout" if is_timeout else "network_error",
            retryable=True,
        ) from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProviderUnavailable(
            "configured provider returned an invalid JSON response",
            category="response_invalid",
            retryable=False,
        ) from None
    except (TypeError, ValueError):
        # urllib may include the configured URL in these deterministic request errors.
        raise ProviderUnavailable(
            "configured provider request configuration is invalid",
            category="request_invalid",
            retryable=False,
        ) from None
    if not isinstance(result, dict):
        raise ProviderUnavailable(
            "configured provider returned an invalid JSON object",
            category="response_invalid",
            retryable=False,
        )
    return result
