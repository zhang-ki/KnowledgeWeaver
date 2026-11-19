from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|secret|password|authorization)\b\s*[:=]\s*)((?:Bearer\s+)?[^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_URL_CREDENTIALS = re.compile(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@")
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|secret|password)=)[^&#\s]*"
)
_SENSITIVE_KEY = re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret|password|authorization|credential|dsn)")


def redact_text(value: str, secret_values: Sequence[str] = ()) -> str:
    result = value
    for secret in sorted((item for item in secret_values if item), key=len, reverse=True):
        result = result.replace(secret, "[REDACTED]")
    result = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", result)
    result = _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", result)
    result = _BEARER.sub("Bearer [REDACTED]", result)
    result = _QUERY_SECRET.sub(r"\1[REDACTED]", result)
    return result


def redact_value(value: Any, secret_values: Sequence[str] = (), key: str | None = None) -> Any:
    if key and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_text(value, secret_values)
    if isinstance(value, Mapping):
        return {
            str(name): redact_value(item, secret_values, str(name))
            for name, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact_value(item, secret_values) for item in value)
    if isinstance(value, list):
        return [redact_value(item, secret_values) for item in value]
    return value


class RedactingFilter(logging.Filter):
    def __init__(self, secret_values: Sequence[str] = ()) -> None:
        super().__init__()
        self._secret_values = tuple(value for value in secret_values if value)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except (TypeError, ValueError):
            rendered = "[unrenderable log message]"
        record.msg = redact_text(rendered, self._secret_values)
        record.args = ()
        record.exc_info = None
        for name, value in tuple(record.__dict__.items()):
            if name not in {"msg", "args", "message", "exc_info", "exc_text"}:
                record.__dict__[name] = redact_value(value, self._secret_values, name)
        return True


def configure_redacted_logger(
    logger: logging.Logger, secret_values: Sequence[str] = ()
) -> logging.Logger:
    if not any(isinstance(item, RedactingFilter) for item in logger.filters):
        logger.addFilter(RedactingFilter(secret_values))
    return logger
