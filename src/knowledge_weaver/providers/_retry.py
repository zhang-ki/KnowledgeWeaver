from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from knowledge_weaver.core.budget import BudgetReservation
from knowledge_weaver.core.errors import ProviderUnavailable

from .http import JsonTransport


async def post_with_retries(
    transport: JsonTransport,
    endpoint_url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    timeout_seconds: float,
    max_attempts: int,
    backoff_seconds: float,
    reserve: Callable[[], Awaitable[BudgetReservation]],
    local_only: bool = False,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[dict[str, Any], BudgetReservation]:
    for attempt in range(1, max_attempts + 1):
        reservation = await reserve()
        try:
            body = await transport.post_json(
                endpoint_url,
                payload,
                headers,
                timeout_seconds=timeout_seconds,
                local_only=local_only,
            )
        except asyncio.CancelledError:
            await asyncio.shield(reservation.finish(None))
            raise
        except ProviderUnavailable as error:
            await reservation.finish(None)
            if not error.retryable or attempt == max_attempts:
                raise
            if backoff_seconds:
                await sleep(backoff_seconds * attempt)
        else:
            return body, reservation
    raise ProviderUnavailable(
        "provider request attempts were exhausted",
        category="provider_unavailable",
        retryable=False,
    )
