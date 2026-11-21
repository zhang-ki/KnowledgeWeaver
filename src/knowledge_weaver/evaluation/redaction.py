"""Secret-safe serialization primitives for B07 runtime artifacts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

_SENSITIVE_KEY = re.compile(r"(?i)(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|secret)")
_ASSIGNED_SECRET = re.compile(
    r"(?i)(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|secret)"
    r"(\s*[:=]\s*|\s+)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_DEEPSEEK_OR_OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_URL_PASSWORD = re.compile(r"(://[^:/\s@]+:)[^@\s]+(@)")


def redact_text(value: str) -> str:
    redacted = _URL_PASSWORD.sub(r"\1[REDACTED]\2", value)
    redacted = _BEARER.sub("Bearer [REDACTED]", redacted)
    redacted = _DEEPSEEK_OR_OPENAI_KEY.sub("[REDACTED_KEY]", redacted)
    return _ASSIGNED_SECRET.sub(r"\1\2[REDACTED]", redacted)


def redact_payload(value: Any, *, key: str | None = None) -> Any:
    """Recursively scrub secret-shaped fields and values before writing logs."""
    if key is not None and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {str(item_key): redact_payload(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, tuple):
        return [redact_payload(item) for item in value]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [redact_payload(item) for item in value]
    return value


def canonical_json_line(value: Any) -> str:
    scrubbed = redact_payload(value)
    return json.dumps(
        scrubbed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
