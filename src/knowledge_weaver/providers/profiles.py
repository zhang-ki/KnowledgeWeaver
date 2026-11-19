from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from knowledge_weaver.core.errors import ProviderUnavailable


class _Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _validate_endpoint(value: str, *, loopback_only: bool = False) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("endpoint must be an absolute HTTP(S) URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("endpoint URL cannot contain credentials, query, or fragment")
    if loopback_only:
        host = parts.hostname.casefold()
        is_loopback = host == "localhost"
        try:
            is_loopback = is_loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if not is_loopback:
            raise ValueError("local embedding endpoint must use a loopback host")
    return value.rstrip("/")


def _required(values: Mapping[str, str], *names: str) -> list[str]:
    result = [values.get(name, "").strip() for name in names]
    if any(not item for item in result):
        raise ProviderUnavailable(
            "provider endpoint, model, or credential is missing",
            category="configuration_missing",
            retryable=False,
        )
    return result


def _endpoint_with_path(base_url: str, path: str) -> str:
    base = base_url.strip().rstrip("/")
    suffix = "/" + path.strip("/")
    return base if base.casefold().endswith(suffix.casefold()) else base + suffix


class OpenAIChatProfile(_Profile):
    """Profile for one explicitly selected OpenAI chat-completions wire contract."""

    profile_id: str = "openai-chat-v1"
    endpoint_url: str
    model: str
    api_key: SecretStr | None = None
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    model_field: str = "model"
    messages_field: str = "messages"
    max_tokens_field: str = "max_tokens"
    response_format_field: str = "response_format"
    json_mode: Literal["none", "json_object", "json_schema"] = "json_object"
    max_attempts: int = Field(default=3, ge=1, le=3)
    retry_backoff_seconds: float = Field(default=0.25, ge=0.0, le=10.0)
    timeout_seconds: float = Field(default=15.0, gt=0.0, le=120.0)

    @field_validator("endpoint_url")
    @classmethod
    def endpoint_is_safe(cls, value: str) -> str:
        return _validate_endpoint(value)

    @field_validator("model")
    @classmethod
    def model_is_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model must be configured")
        return value.strip()

    @classmethod
    def from_environment(cls, values: Mapping[str, str] | None = None) -> OpenAIChatProfile:
        env = os.environ if values is None else values
        base_url, model, key = _required(
            env, "KW_LLM_BASE_URL", "KW_LLM_MODEL", "KW_LLM_API_KEY"
        )
        return cls(
            endpoint_url=_endpoint_with_path(base_url, "chat/completions"),
            model=model,
            api_key=SecretStr(key),
        )


class OpenAIEmbeddingProfile(_Profile):
    """Profile for an explicitly selected OpenAI embeddings wire contract."""

    profile_id: str = "openai-embedding-v1"
    endpoint_url: str
    model: str
    api_key: SecretStr | None = None
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    model_field: str = "model"
    input_field: str = "input"
    dimensions_field: str = "dimensions"
    dimensions: int | None = Field(default=None, ge=1)
    query_prefix: str = ""
    document_prefix: str = ""
    max_attempts: int = Field(default=3, ge=1, le=3)
    retry_backoff_seconds: float = Field(default=0.25, ge=0.0, le=10.0)
    timeout_seconds: float = Field(default=15.0, gt=0.0, le=120.0)

    @field_validator("endpoint_url")
    @classmethod
    def endpoint_is_safe(cls, value: str) -> str:
        return _validate_endpoint(value)

    @field_validator("model")
    @classmethod
    def model_is_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model must be configured")
        return value.strip()

    @classmethod
    def from_environment(
        cls, values: Mapping[str, str] | None = None
    ) -> OpenAIEmbeddingProfile:
        env = os.environ if values is None else values
        endpoint, model, key = _required(
            env,
            "KW_CLOUD_EMBEDDING_ENDPOINT_URL",
            "KW_CLOUD_EMBEDDING_MODEL",
            "KW_CLOUD_EMBEDDING_API_KEY",
        )
        return cls(endpoint_url=endpoint, model=model, api_key=SecretStr(key))


class LocalQwenProfile(_Profile):
    """Loopback-only local Qwen embeddings; no cloud key or remote approval."""

    profile_id: str = "qwen-local-embeddings-v1"
    endpoint_url: str
    model: str
    local_api_key: SecretStr | None = None
    model_field: str = "model"
    input_field: str = "input"
    dimensions_field: str | None = None
    dimensions: int | None = Field(default=None, ge=1)
    query_prefix: str = ""
    document_prefix: str = ""
    max_attempts: int = Field(default=1, ge=1, le=3)
    retry_backoff_seconds: float = Field(default=0.0, ge=0.0, le=10.0)
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=120.0)

    @field_validator("endpoint_url")
    @classmethod
    def endpoint_is_loopback(cls, value: str) -> str:
        return _validate_endpoint(value, loopback_only=True)

    @field_validator("model")
    @classmethod
    def model_is_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model must be configured")
        return value.strip()

    @classmethod
    def from_environment(cls, values: Mapping[str, str] | None = None) -> LocalQwenProfile:
        env = os.environ if values is None else values
        base, model = _required(env, "KW_EMBEDDING_BASE_URL", "KW_EMBEDDING_MODEL")
        key = env.get("KW_LOCAL_EMBEDDING_API_KEY", "").strip()
        return cls(
            endpoint_url=_endpoint_with_path(base, "embeddings"),
            model=model,
            local_api_key=SecretStr(key) if key else None,
        )


class RerankProfile(_Profile):
    """Rerank wire fields are selected per provider rather than shared by endpoint assumption."""

    profile_id: str
    endpoint_url: str
    model: str
    api_key: SecretStr | None = None
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    model_field: str = "model"
    query_field: str = "query"
    documents_field: str = "documents"
    limit_field: str = "top_n"
    results_field: str = "results"
    index_field: str = "index"
    score_field: str = "relevance_score"
    score_direction: Literal["higher", "lower"] = "higher"
    max_attempts: int = Field(default=3, ge=1, le=3)
    retry_backoff_seconds: float = Field(default=0.25, ge=0.0, le=10.0)
    timeout_seconds: float = Field(default=15.0, gt=0.0, le=120.0)

    @field_validator("endpoint_url")
    @classmethod
    def endpoint_is_safe(cls, value: str) -> str:
        return _validate_endpoint(value)

    @field_validator("model")
    @classmethod
    def model_is_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model must be configured")
        return value.strip()

    @model_validator(mode="after")
    def nonempty_profile_id(self) -> Self:
        if not self.profile_id.strip():
            raise ValueError("profile_id must be configured")
        return self
