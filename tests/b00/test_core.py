from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from io import StringIO

import httpx
import pytest

from knowledge_weaver.core.budget import BudgetLedger
from knowledge_weaver.core.errors import (
    BudgetExceeded,
    ErrorCode,
    ErrorEnvelope,
    ProviderUnavailable,
)
from knowledge_weaver.core.redaction import RedactingFilter
from knowledge_weaver.models import Budget
from knowledge_weaver.providers.http import HttpxJsonTransport


def make_budget(**overrides) -> Budget:
    values = {"deadline_utc": datetime.now(UTC) + timedelta(minutes=1)}
    values.update(overrides)
    return Budget(**values)


@pytest.mark.asyncio
async def test_budget_enforces_calls_deadline_and_settles_cancellation() -> None:
    ledger = BudgetLedger(make_budget(max_tool_calls=1))
    reservation = await ledger.reserve_tool_call()
    await reservation.finish()
    with pytest.raises(BudgetExceeded):
        await ledger.reserve_tool_call()

    expired = BudgetLedger(make_budget(deadline_utc=datetime.now(UTC) - timedelta(seconds=1)))
    with pytest.raises(Exception) as raised:
        await expired.reserve_tool_call()
    assert getattr(raised.value, "code", None) == ErrorCode.DEADLINE_EXCEEDED


@pytest.mark.asyncio
async def test_http_timeout_is_sanitized_and_retryable() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic timeout with secret=never-log", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        transport = HttpxJsonTransport(client)
        with pytest.raises(ProviderUnavailable) as raised:
            await transport.post_json(
                "https://provider.example/api", {}, {}, timeout_seconds=1,
            )
    finally:
        await client.aclose()

    assert raised.value.category == "timeout"
    assert raised.value.retryable is True
    assert "never-log" not in str(raised.value)


@pytest.mark.asyncio
async def test_invalid_protocol_is_sanitized_without_network_access() -> None:
    transport = HttpxJsonTransport()
    with pytest.raises(ProviderUnavailable) as raised:
        await transport.post_json(
            "ftp://user:secret@provider.example/path", {}, {}, timeout_seconds=1,
        )
    assert raised.value.category == "request_invalid"
    assert "secret" not in str(raised.value)


def test_error_envelopes_and_logs_redact_secrets() -> None:
    envelope = ErrorEnvelope(
        code="PROVIDER_UNAVAILABLE",
        message="request failed Authorization: Bearer abc123",
        details={
            "api_key": "abc123", "url": "https://user:password@host/path?token=abc123",
            "nested": ["Bearer abc123"],
        },
    )
    rendered = str(envelope.model_dump(mode="json"))
    assert "abc123" not in rendered
    assert "password" not in rendered

    stream = StringIO()
    logger = logging.getLogger("knowledgeweaver-redaction-test")
    logger.handlers.clear()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter(["private-value"]))
    logger.addHandler(handler)
    logger.propagate = False
    logger.error("provider key=private-value failed")
    assert "private-value" not in stream.getvalue()


async def _never_called(*args, **kwargs):
    raise AssertionError("transport should not be reached")
