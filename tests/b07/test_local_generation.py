from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from knowledge_weaver.core.budget import BudgetLedger
from knowledge_weaver.evaluation.local_generation import (
    DevLocalOpenAIChatProfile,
    DevLocalOpenAICompatibleGenerationProvider,
)
from knowledge_weaver.models import Budget, CountKind, LLMRequest


class _JsonTransport:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body
        self.calls: list[dict[str, Any]] = []

    async def post_json(
        self,
        endpoint_url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        *,
        timeout_seconds: float,
        local_only: bool = False,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "endpoint_url": endpoint_url,
                "payload": payload,
                "headers": headers,
                "timeout_seconds": timeout_seconds,
                "local_only": local_only,
            }
        )
        return self.body


def _provider(transport: _JsonTransport) -> DevLocalOpenAICompatibleGenerationProvider:
    budget = Budget(
        max_steps=2,
        max_tool_calls=2,
        max_llm_calls=2,
        max_total_tokens=200,
        deadline_utc=datetime.now(UTC) + timedelta(minutes=1),
    )
    profile = DevLocalOpenAIChatProfile(
        endpoint_url="http://127.0.0.1:56337/v1/chat/completions",
        model="qwen3.5-4b-local",
    )
    return DevLocalOpenAICompatibleGenerationProvider(
        profile,
        BudgetLedger(budget),
        transport=transport,
    )


def _request() -> LLMRequest:
    return LLMRequest(
        system_instruction="Return a JSON object.",
        untrusted_context="Synthetic local context.",
        response_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        max_output_tokens=32,
        timeout_seconds=8,
    )


@pytest.mark.asyncio
async def test_local_generation_uses_loopback_without_auth_and_keeps_usage() -> None:
    transport = _JsonTransport(
        {
            "id": "local-request-1",
            "model": "qwen3.5-4b-local",
            "choices": [{"message": {"content": '{"ok":true}'}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
        }
    )

    result = await _provider(transport).generate(_request())

    assert result.parsed_json == {"ok": True}
    assert result.usage.count_kind is CountKind.exact
    assert (result.usage.input_tokens, result.usage.output_tokens) == (11, 3)
    assert result.model == result.revision == "qwen3.5-4b-local"
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["endpoint_url"] == "http://127.0.0.1:56337/v1/chat/completions"
    assert call["local_only"] is True
    assert call["headers"] == {"Content-Type": "application/json"}
    assert "Authorization" not in call["headers"]
    response_format = call["payload"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"] == _request().response_schema
    assert "Do not return status=answered with an empty claims list" in call["payload"]["messages"][0]["content"]


@pytest.mark.parametrize(
    "endpoint_url",
    [
        "https://127.0.0.1:56337/v1/chat/completions",
        "http://localhost:56337/v1/chat/completions",
        "http://192.0.2.10:56337/v1/chat/completions",
        "http://user@127.0.0.1:56337/v1/chat/completions",
        "http://127.0.0.1:56337/v1/chat/completions?redirect=1",
    ],
)
def test_local_profile_rejects_nonexplicit_loopback_endpoints(endpoint_url: str) -> None:
    with pytest.raises(ValueError, match="127.0.0.1"):
        DevLocalOpenAIChatProfile(endpoint_url=endpoint_url, model="qwen3.5-4b-local")
