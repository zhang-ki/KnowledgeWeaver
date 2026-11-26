from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from knowledge_weaver.core.budget import BudgetLedger, BudgetReservation
from knowledge_weaver.core.config import RemoteUseApproval, RuntimeSettings
from knowledge_weaver.core.errors import ProviderUnavailable
from knowledge_weaver.models import LLMRequest, LLMResult

from ._common import (
    StructuredResponseInvalid,
    auth_headers,
    estimate_tokens,
    parse_token_usage,
    validate_json_response,
)
from ._retry import post_with_retries
from .http import HttpxJsonTransport, JsonTransport
from .profiles import OpenAIChatProfile


class CloudGenerationProvider:
    """Profile-bound chat completion adapter; live use requires all explicit gates."""

    def __init__(
        self,
        profile: OpenAIChatProfile,
        approval: RemoteUseApproval,
        budget: BudgetLedger,
        *,
        settings: RuntimeSettings | None = None,
        transport: JsonTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.profile = profile
        self.approval = approval
        self.settings = settings or RuntimeSettings.from_environment()
        self.budget = budget
        self.transport = transport or HttpxJsonTransport()
        self.sleep = sleep

    async def generate(self, request: LLMRequest) -> LLMResult:
        self.approval.require_open(self.settings)
        headers = auth_headers(
            self.profile.api_key,
            header_name=self.profile.auth_header,
            scheme=self.profile.auth_scheme,
        )
        system_content = request.system_instruction
        if self.profile.json_mode == "json_object" and request.response_schema:
            system_content = (
                f"{system_content}\n\nRequired JSON Schema (include all required fields and valid enum values): "
                f"{json.dumps(request.response_schema, ensure_ascii=False, separators=(',', ':'))}"
            )
        payload: dict[str, Any] = {
            self.profile.model_field: self.profile.model,
            self.profile.messages_field: [
                {"role": "system", "content": system_content},
                {"role": "user", "content": request.untrusted_context},
            ],
            self.profile.max_tokens_field: request.max_output_tokens,
        }
        if self.profile.json_mode == "json_object":
            payload[self.profile.response_format_field] = {"type": "json_object"}
        elif self.profile.json_mode == "json_schema":
            payload[self.profile.response_format_field] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "knowledgeweaver_response",
                    "strict": True,
                    "schema": request.response_schema,
                },
            }

        host = (urlsplit(self.profile.endpoint_url).hostname or "").casefold()
        disable_thinking = (
            self.profile.disable_thinking
            if self.profile.disable_thinking is not None
            else host.endswith(".aliyuncs.com")
        )
        if disable_thinking:
            payload["enable_thinking"] = False

        input_estimate = estimate_tokens(system_content + request.untrusted_context)

        async def reserve() -> BudgetReservation:
            return await self.budget.reserve_llm_call(
                input_tokens_estimate=input_estimate,
                max_output_tokens=request.max_output_tokens,
            )

        body, reservation = await post_with_retries(
            self.transport,
            self.profile.endpoint_url,
            payload,
            headers,
            timeout_seconds=min(request.timeout_seconds, self.profile.timeout_seconds),
            max_attempts=self.profile.max_attempts,
            backoff_seconds=self.profile.retry_backoff_seconds,
            reserve=reserve,
            sleep=self.sleep,
        )
        try:
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
            parsed: Any = json.loads(content)
            parsed = validate_json_response(parsed, request.response_schema)
            usage = parse_token_usage(body.get("usage"))
            revision_value = body.get("model")
            revision = (
                revision_value
                if isinstance(revision_value, str) and revision_value
                else self.profile.model
            )
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
            choices = body.get("choices")
            choice = choices[0] if isinstance(choices, list) and choices else {}
            finish_reason = (
                choice.get("finish_reason") if isinstance(choice, dict) else None
            )
            message = choice.get("message") if isinstance(choice, dict) else None
            reasoning_exhausted = bool(
                finish_reason == "length"
                and isinstance(message, dict)
                and not str(message.get("content") or "").strip()
                and str(message.get("reasoning_content") or "").strip()
            )
            diagnostic: dict[str, Any] = {
                "failure_stage": "content_json_parse",
                "finish_reason": finish_reason,
                "content_truncated": finish_reason == "length",
            }
            if reasoning_exhausted:
                diagnostic["reasoning_exhausted"] = True
            raise StructuredResponseInvalid(
                "provider response content was not valid JSON",
                diagnostic=diagnostic,
            ) from None
        except (KeyError, IndexError, TypeError):
            await reservation.finish(None)
            raise ProviderUnavailable(
                "provider response did not match the JSON object contract",
                category="response_invalid",
                retryable=False,
            ) from None
        except (ValueError, OverflowError):
            await reservation.finish(None)
            raise ProviderUnavailable(
                "provider response could not be validated",
                category="response_invalid",
                retryable=False,
            ) from None
        await reservation.finish(usage)
        return result
