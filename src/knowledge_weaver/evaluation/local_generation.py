"""Explicit loopback-only chat provider for non-formal B07 development runs."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlsplit

from knowledge_weaver.core.budget import BudgetLedger, BudgetReservation
from knowledge_weaver.core.errors import ProviderUnavailable
from knowledge_weaver.models import LLMRequest, LLMResult
from knowledge_weaver.providers._common import (
    StructuredResponseInvalid,
    estimate_tokens,
    parse_token_usage,
    validate_json_response,
)
from knowledge_weaver.providers._retry import post_with_retries
from knowledge_weaver.providers.http import HttpxJsonTransport, JsonTransport


@dataclass(frozen=True, slots=True)
class DevLocalOpenAIChatProfile:
    """No-key profile restricted to an explicit IPv4 loopback chat endpoint."""

    endpoint_url: str
    model: str
    timeout_seconds: float = 90.0

    def __post_init__(self) -> None:
        parsed = urlsplit(self.endpoint_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port is None
            or parsed.path != "/v1/chat/completions"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("local chat profile must target http://127.0.0.1:<port>/v1/chat/completions")
        if not self.model.strip():
            raise ValueError("local chat model must be configured")
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 120:
            raise ValueError("local chat timeout must be greater than zero and at most 120 seconds")


class DevLocalOpenAICompatibleGenerationProvider:
    """OpenAI-compatible JSON chat over loopback, with no credential or remote fallback."""

    def __init__(
        self,
        profile: DevLocalOpenAIChatProfile,
        budget: BudgetLedger,
        *,
        transport: JsonTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.profile = profile
        self.budget = budget
        self.transport = transport or HttpxJsonTransport()
        self.sleep = sleep

    async def generate(self, request: LLMRequest) -> LLMResult:
        system_instruction = (
            request.system_instruction
            + "\n\nDo not return status=answered with an empty claims list. If the retrieved "
            + "passages support the answer, set status to answered, include at least one concise "
            + "supported claim, copy its supporting evidence ID or IDs from the passages, and set "
            + "unanswered to an empty list. Use status=insufficient only if the passages do not "
            + "support the answer; then leave claims empty and state the gap. Never put the word "
            + "None in unanswered. Example shape: "
            + '{"status":"answered","claims":[{"text":"<supported answer>",'
            + '"evidence_ids":["<copy an evidence ID>"]}],"unanswered":[]}. Replace placeholders.'
        )
        payload = {
            "model": self.profile.model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": request.untrusted_context},
            ],
            "max_tokens": request.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "b07_structured_answer",
                    "strict": True,
                    "schema": request.response_schema,
                },
            },
            "temperature": 0,
        }
        input_estimate = estimate_tokens(
            request.system_instruction + request.untrusted_context
        )

        async def reserve() -> BudgetReservation:
            return await self.budget.reserve_llm_call(
                input_tokens_estimate=input_estimate,
                max_output_tokens=request.max_output_tokens,
                estimated_currency=Decimal(0),
            )

        body, reservation = await post_with_retries(
            self.transport,
            self.profile.endpoint_url,
            payload,
            {"Content-Type": "application/json"},
            timeout_seconds=min(request.timeout_seconds, self.profile.timeout_seconds),
            max_attempts=1,
            backoff_seconds=0,
            reserve=reserve,
            local_only=True,
            sleep=self.sleep,
        )
        try:
            choices = body.get("choices")
            if not isinstance(choices, list) or not choices:
                raise TypeError
            message = choices[0].get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str):
                raise TypeError
            parsed = validate_json_response(json.loads(content), request.response_schema)
            usage = parse_token_usage(body.get("usage"))
            revision = body.get("model")
            if not isinstance(revision, str) or not revision:
                revision = self.profile.model
            result = LLMResult(
                parsed_json=parsed,
                usage=usage,
                provider_request_id=body.get("id") if isinstance(body.get("id"), str) else None,
                model=self.profile.model,
                revision=revision,
            )
        except ProviderUnavailable:
            await reservation.finish(None)
            raise
        except json.JSONDecodeError:
            await reservation.finish(None)
            raise StructuredResponseInvalid(
                "local chat response content was not valid JSON",
                diagnostic={"failure_stage": "content_json_parse"},
            ) from None
        except (AttributeError, KeyError, IndexError, TypeError):
            await reservation.finish(None)
            raise ProviderUnavailable(
                "local chat response did not match the JSON object contract",
                category="response_invalid",
                retryable=False,
            ) from None
        except (ValueError, OverflowError):
            await reservation.finish(None)
            raise ProviderUnavailable(
                "local chat response could not be validated",
                category="response_invalid",
                retryable=False,
            ) from None
        await reservation.finish(usage)
        return result
