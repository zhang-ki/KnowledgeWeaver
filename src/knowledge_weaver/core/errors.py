from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from .redaction import redact_text, redact_value

UUIDText = Annotated[
    str,
    StringConstraints(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    ),
]


class ErrorCode(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    PATH_DENIED = "PATH_DENIED"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    PARSE_FAILED = "PARSE_FAILED"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    MISSING_REFERENCE = "MISSING_REFERENCE"
    NO_READY_GENERATION = "NO_READY_GENERATION"
    GENERATION_CONFLICT = "GENERATION_CONFLICT"
    INDEX_NOT_READY = "INDEX_NOT_READY"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorEnvelope(BaseModel):
    schema_version: Literal[1] = 1
    code: ErrorCode
    message: str
    request_id: UUIDText | None = None
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("message")
    @classmethod
    def scrub_message(cls, value: str) -> str:
        return redact_text(value)

    @field_validator("details")
    @classmethod
    def scrub_details(cls, value: dict[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], redact_value(value))


class KnowledgeWeaverError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        request_id: str | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.envelope = ErrorEnvelope(
            code=code,
            message=message,
            request_id=request_id,
            retryable=retryable,
            details=details or {},
        )
        super().__init__(self.envelope.message)

    @property
    def code(self) -> ErrorCode:
        return self.envelope.code

    @property
    def retryable(self) -> bool:
        return self.envelope.retryable

    def to_dict(self) -> dict[str, Any]:
        return self.envelope.model_dump(mode="json", exclude_none=True)


class ProviderUnavailable(KnowledgeWeaverError):
    def __init__(
        self,
        message: str = "provider is unavailable",
        *,
        category: str = "provider_unavailable",
        retryable: bool = False,
        http_status: int | None = None,
        request_id: str | None = None,
    ) -> None:
        code = ErrorCode.RATE_LIMITED if http_status == 429 else ErrorCode.PROVIDER_UNAVAILABLE
        details: dict[str, Any] = {"category": category}
        if http_status is not None:
            details["http_status"] = http_status
        self.category = category
        self.http_status = http_status
        super().__init__(
            code,
            message,
            request_id=request_id,
            retryable=retryable,
            details=details,
        )


class BudgetExceeded(KnowledgeWeaverError):
    def __init__(self, message: str = "call budget is exhausted") -> None:
        super().__init__(ErrorCode.BUDGET_EXHAUSTED, message)


class DeadlineExceeded(KnowledgeWeaverError):
    def __init__(self, message: str = "request deadline has passed") -> None:
        super().__init__(ErrorCode.DEADLINE_EXCEEDED, message)
