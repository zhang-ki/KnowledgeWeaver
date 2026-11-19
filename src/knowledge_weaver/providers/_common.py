from __future__ import annotations

import hashlib
import json
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import SecretStr

from knowledge_weaver.core.errors import ProviderUnavailable
from knowledge_weaver.models import CountKind, ModelTokenUsage


def auth_headers(
    api_key: SecretStr | None,
    *,
    header_name: str = "Authorization",
    scheme: str = "Bearer",
) -> dict[str, str]:
    if api_key is None:
        raise ProviderUnavailable(
            "provider credential is missing",
            category="configuration_missing",
            retryable=False,
        )
    value = api_key.get_secret_value().strip()
    if not value:
        raise ProviderUnavailable(
            "provider credential is missing",
            category="configuration_missing",
            retryable=False,
        )
    return {"Content-Type": "application/json", header_name: f"{scheme} {value}"}


def estimate_tokens(text: str) -> int:
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def parse_token_usage(raw: Any) -> ModelTokenUsage:
    if raw is None:
        return ModelTokenUsage()
    if not isinstance(raw, dict):
        raise ProviderUnavailable(
            "provider response usage is invalid",
            category="usage_invalid",
            retryable=False,
        )
    fields = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "cache_hit_tokens",
        "cache_miss_tokens",
    )
    preserved: dict[str, int] = {}
    for name in fields:
        value = raw.get(name)
        if value is None:
            continue
        if type(value) is not int or value < 0:
            raise ProviderUnavailable(
                "provider response usage is invalid",
                category="usage_invalid",
                retryable=False,
            )
        preserved[name] = value
    input_tokens = preserved.get("prompt_tokens", 0)
    output_tokens = preserved.get("completion_tokens", 0)
    count_kind = (
        CountKind.exact
        if "prompt_tokens" in preserved and "completion_tokens" in preserved
        else CountKind.unavailable
    )
    return ModelTokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        count_kind=count_kind,
        raw_fields=preserved,
    )


def validate_json_response(value: Any, schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderUnavailable(
            "provider response did not match the JSON object contract",
            category="response_invalid",
            retryable=False,
        )
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
    except SchemaError:
        raise ProviderUnavailable(
            "configured response schema is invalid",
            category="request_invalid",
            retryable=False,
        ) from None
    except ValidationError:
        raise ProviderUnavailable(
            "provider response did not match the requested schema",
            category="response_invalid",
            retryable=False,
        ) from None
    return value


def config_fingerprint(*parts: str) -> str:
    raw = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
