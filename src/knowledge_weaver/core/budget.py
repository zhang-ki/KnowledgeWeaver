from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from knowledge_weaver.core.errors import BudgetExceeded, DeadlineExceeded
from knowledge_weaver.models import Budget, CountKind, ModelTokenUsage, Usage


@dataclass(slots=True)
class BudgetReservation:
    ledger: BudgetLedger
    kind: Literal["tool", "llm"]
    reserved_tokens: int
    reserved_input_tokens: int
    reserved_output_tokens: int
    estimated_currency: Decimal | None
    settled: bool = False

    async def finish(self, usage: ModelTokenUsage | Usage | None = None) -> None:
        if self.settled:
            raise RuntimeError("budget reservation already settled")
        self.settled = True
        await asyncio.shield(self.ledger._settle(self, usage))


class BudgetLedger:
    """Atomic, per-investigation accounting; each retry consumes another reservation."""

    def __init__(
        self,
        budget: Budget,
        *,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.budget = budget
        self._now = now or (lambda: datetime.now(UTC))
        self._monotonic = monotonic
        self._started = monotonic()
        self._lock = asyncio.Lock()
        self._tool_calls = 0
        self._llm_calls = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._reserved_tokens = 0
        self._currency_spent = Decimal(0)
        self._currency_reserved = Decimal(0)
        self._estimated_usage = False

    def _check_deadline(self) -> None:
        if self._now().astimezone(UTC) >= self.budget.deadline_utc:
            raise DeadlineExceeded()

    async def reserve_tool_call(self) -> BudgetReservation:
        return await self._reserve("tool", 0, 0, 0, None)

    async def reserve_model_call(
        self,
        *,
        input_tokens_estimate: int,
        output_tokens_estimate: int = 0,
        estimated_currency: Decimal | None = None,
    ) -> BudgetReservation:
        if input_tokens_estimate < 0 or output_tokens_estimate < 0:
            raise ValueError("token estimates must be non-negative")
        return await self._reserve(
            "llm",
            input_tokens_estimate + output_tokens_estimate,
            input_tokens_estimate,
            output_tokens_estimate,
            estimated_currency,
        )

    async def reserve_llm_call(
        self,
        *,
        input_tokens_estimate: int,
        max_output_tokens: int,
        estimated_currency: Decimal | None = None,
    ) -> BudgetReservation:
        if max_output_tokens < 1:
            raise ValueError("output cap must be positive")
        return await self.reserve_model_call(
            input_tokens_estimate=input_tokens_estimate,
            output_tokens_estimate=max_output_tokens,
            estimated_currency=estimated_currency,
        )

    async def _reserve(
        self,
        kind: Literal["tool", "llm"],
        tokens: int,
        input_tokens: int,
        output_tokens: int,
        currency: Decimal | None,
    ) -> BudgetReservation:
        async with self._lock:
            self._check_deadline()
            if kind == "tool" and self._tool_calls >= self.budget.max_tool_calls:
                raise BudgetExceeded("tool-call budget is exhausted")
            if kind == "llm" and self._llm_calls >= self.budget.max_llm_calls:
                raise BudgetExceeded("model-call budget is exhausted")
            if self._input_tokens + self._output_tokens + self._reserved_tokens + tokens > self.budget.max_total_tokens:
                raise BudgetExceeded("token budget is exhausted")
            cap = self.budget.max_currency_amount
            if kind == "llm" and cap is not None:
                if currency is None:
                    raise BudgetExceeded("currency cap requires a per-call cost estimate")
                if self._currency_spent + self._currency_reserved + currency > cap:
                    raise BudgetExceeded("currency budget is exhausted")
                self._currency_reserved += currency
            if kind == "tool":
                self._tool_calls += 1
            else:
                self._llm_calls += 1
            self._reserved_tokens += tokens
            return BudgetReservation(
                ledger=self,
                kind=kind,
                reserved_tokens=tokens,
                reserved_input_tokens=input_tokens,
                reserved_output_tokens=output_tokens,
                estimated_currency=currency,
            )

    async def _settle(
        self, reservation: BudgetReservation, usage: ModelTokenUsage | Usage | None
    ) -> None:
        async with self._lock:
            self._reserved_tokens -= reservation.reserved_tokens
            if usage is not None and usage.count_kind == CountKind.exact:
                input_tokens = usage.input_tokens
                output_tokens = usage.output_tokens
            else:
                input_tokens = reservation.reserved_tokens
                output_tokens = 0
                self._estimated_usage = True
            self._input_tokens += input_tokens
            self._output_tokens += output_tokens
            over_tokens = (
                self._input_tokens + self._output_tokens + self._reserved_tokens
                > self.budget.max_total_tokens
            )

            actual_currency = usage.currency_cost if isinstance(usage, Usage) else None
            if reservation.estimated_currency is not None:
                self._currency_reserved -= reservation.estimated_currency
                self._currency_spent += actual_currency if actual_currency is not None else reservation.estimated_currency
            over_currency = (
                self.budget.max_currency_amount is not None
                and self._currency_spent + self._currency_reserved
                > self.budget.max_currency_amount
            )
            if over_tokens:
                raise BudgetExceeded("provider usage exceeded the reserved token budget")
            if over_currency:
                raise BudgetExceeded("provider usage exceeded the currency budget")

    async def snapshot(self) -> Usage:
        async with self._lock:
            pending = self._reserved_tokens
            count_kind = (
                CountKind.estimated
                if self._estimated_usage or pending
                else CountKind.exact
                if self._tool_calls or self._llm_calls
                else CountKind.unavailable
            )
            return Usage(
                elapsed_ms=max(0, int((self._monotonic() - self._started) * 1000)),
                tool_calls=self._tool_calls,
                llm_calls=self._llm_calls,
                input_tokens=self._input_tokens + pending,
                output_tokens=self._output_tokens,
                count_kind=count_kind,
                currency_cost=self._currency_spent if self._currency_spent else None,
            )
