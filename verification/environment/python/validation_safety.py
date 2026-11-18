"""Small, isolated checks for safe configuration diagnostics in P1."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

_KEY_VALUE_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|password|passwd|secret)\b"
    r"(\s*[:=]\s*)([^\s,;&]+)"
)
_URL_CREDENTIALS = re.compile(r"(?i)(://)[^/@\s]+:[^/@\s]+@")
_BEARER_TOKEN = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+")


def require_setting(name: str, values: Mapping[str, str] | None = None) -> str:
    """Return a required setting or raise a clear error naming only the setting."""
    source = os.environ if values is None else values
    value = source.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Missing required setting: {name}")
    return value


def redact_secrets(message: str) -> str:
    """Redact common key/value, bearer-token, and URL-credential forms."""
    message = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", message)
    message = _KEY_VALUE_SECRET.sub(r"\1\2[REDACTED]", message)
    return _BEARER_TOKEN.sub(r"\1[REDACTED]", message)
