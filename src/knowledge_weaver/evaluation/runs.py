"""Sequential, retry-bounded query execution records for B07 experiment suites."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from knowledge_weaver.evaluation.contracts import PreparedBenchmarkCase
from knowledge_weaver.evaluation.generation_binding import (
    GenerationBindingError,
    PersistedGenerationLookup,
    VerifiedGeneration,
    resolve_generation,
    valid_generation_binding,
    verify_generation_manifest,
)
from knowledge_weaver.evaluation.redaction import redact_text
from knowledge_weaver.evaluation.scheduling import ExperimentSuite
from knowledge_weaver.models.contracts import (
    Generation,
    Language,
    ModelTokenUsage,
    Path,
    RetrievalHit,
    Usage,
)

NonEmpty = StringConstraints(strip_whitespace=True, min_length=1)
CountKind = Literal["exact", "estimated", "unavailable"]


def _sum_known_decimals(values: list[Decimal | None]) -> Decimal | None:
    if any(value is None for value in values):
        return None
    total = Decimal(0)
    for value in values:
        assert value is not None
        total += value
    return total


class RunModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BlindBenchmarkInput(RunModel):
    """The only case data exposed to a benchmark executor."""

    query_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    language: Language


class QueryMeasurements(RunModel):
    retrieval_precision_at_k: dict[int, float] = Field(default_factory=dict)
    retrieval_recall_at_k: dict[int, float] = Field(default_factory=dict)
    retrieval_ndcg_at_k: dict[int, float] = Field(default_factory=dict)
    gold_evidence_group_recall_at_k: dict[int, float] = Field(default_factory=dict)
    strict_chunk_recall_at_k: dict[int, float] = Field(default_factory=dict)
    reciprocal_rank: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    strict_chunk_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    citation_precision: float | None = Field(default=None, ge=0.0, le=1.0)
    citation_validity: float | None = Field(default=None, ge=0.0, le=1.0)
    unanswerable_refusal_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    unanswerable_false_citation_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    complete_path_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    gold_path_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    answer_correctness: Literal[0, 1, 2] | None = None
    human_reviewer_id: str | None = None
    human_reviewed_at: datetime | None = None

    @field_validator("human_reviewed_at")
    @classmethod
    def human_review_time_is_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("human_reviewed_at must include a timezone")
        return value

    @model_validator(mode="after")
    def human_metrics_have_review_evidence(self) -> QueryMeasurements:
        if (
            (self.citation_precision is not None or self.answer_correctness is not None)
            and (not self.human_reviewer_id or self.human_reviewed_at is None)
        ):
            raise ValueError("human citation and answer metrics require reviewer and timestamp")
        for values in (
            self.retrieval_precision_at_k,
            self.retrieval_recall_at_k,
            self.retrieval_ndcg_at_k,
            self.gold_evidence_group_recall_at_k,
            self.strict_chunk_recall_at_k,
        ):
            if any(k not in {5, 10, 20} or not 0.0 <= value <= 1.0 for k, value in values.items()):
                raise ValueError("retrieval metrics must use frozen K values and [0,1] scores")
        return self


class UnscoredClaim(RunModel):
    text: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()


class UnscoredAnswer(RunModel):
    status: Literal["answered", "insufficient", "conflicted"]
    claims: tuple[UnscoredClaim, ...] = ()
    unanswered: tuple[str, ...] = ()


class CitationResolution(RunModel):
    evidence_id: str = Field(min_length=1)
    resolves_to_original: bool


class ExecutorProviderUsage(RunModel):
    usage: ModelTokenUsage
    model: str = Field(min_length=1)
    revision: str = Field(min_length=1)


class ExecutionOutput(RunModel):
    """Raw, unscored evidence and answer returned by an execution adapter."""

    retrieval_hits: tuple[RetrievalHit, ...]
    retrieved_paths: tuple[Path, ...] = ()
    answer: UnscoredAnswer
    citations: tuple[CitationResolution, ...] = ()
    retrieval_usage: Usage
    provider_usage: ExecutorProviderUsage
    response_summary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AttemptOutcome(RunModel):
    attempt: int = Field(ge=1, le=3)
    status: Literal["complete", "failed"]
    retryable: bool = False
    provider_kind: Literal["real", "mock", "synthetic"]
    provider_profile: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    failure_code: str | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    steps: int = Field(default=0, ge=0)
    count_kind: CountKind = "unavailable"
    currency_cost_usd: Decimal | None = Field(default=None, ge=Decimal(0))
    currency_cost_cny: Decimal | None = Field(default=None, ge=Decimal(0))
    price_snapshot: dict[str, Any] | None = None
    elapsed_ms: int = Field(default=0, ge=0)
    strategy_latency_ms: int = Field(default=0, ge=0)
    cache_state: Literal["cold", "warm"] = "cold"
    executed_generation_id: str | None = None
    executed_generation_sha256: str | None = None
    response_summary_sha256: str | None = None
    execution_output: ExecutionOutput | None = None
    measurements: QueryMeasurements | None = None

    @model_validator(mode="after")
    def outcome_shape_matches(self) -> AttemptOutcome:
        if (self.executed_generation_id is None) != (self.executed_generation_sha256 is None):
            raise ValueError("executor-confirmed Generation ID and digest must be recorded together")
        if self.executed_generation_id is not None and not valid_generation_binding(
            self.executed_generation_id, self.executed_generation_sha256
        ):
            raise ValueError("executor-confirmed Generation binding is invalid")
        if self.status == "complete" and self.failure_code is not None:
            raise ValueError("a complete attempt cannot carry a failure code")
        if self.status == "failed" and not self.failure_code:
            raise ValueError("a failed attempt requires a failure code")
        if self.status == "complete" and self.retryable:
            raise ValueError("a complete attempt cannot be marked retryable")
        return self


class QueryRunRecord(RunModel):
    suite_id: str
    system_id: str
    budget_id: str
    seed: int
    ablation_id: str | None = None
    case_id: str
    generation_id: str | None = None
    generation_sha256: str | None = None
    split: Literal["train", "dev", "test"]
    answerable: bool
    cache_state: Literal["cold", "warm"]
    source_family_ids: list[str]
    provider_kind: Literal["real", "mock", "synthetic"]
    synthetic_fixture: bool
    formal_quality_eligible: bool
    status: Literal["complete", "failed"]
    attempts: list[AttemptOutcome] = Field(min_length=1, max_length=3)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    llm_calls: int = Field(ge=0)
    steps: int = Field(ge=0)
    count_kind: CountKind
    currency_cost_usd: Decimal | None = Field(default=None, ge=Decimal(0))
    currency_cost_cny: Decimal | None = Field(default=None, ge=Decimal(0))
    end_to_end_latency_ms: int = Field(ge=0)
    strategy_latency_ms: int = Field(ge=0)
    budget_violations: list[str] = Field(default_factory=list)
    model_revisions: list[str] = Field(default_factory=list)
    measurements: QueryMeasurements | None = None

    @model_validator(mode="after")
    def run_totals_match_attempts(self) -> QueryRunRecord:
        if (self.generation_id is None) != (self.generation_sha256 is None):
            raise ValueError("query result generation ID and SHA-256 must be recorded together")
        if self.generation_id is not None and not valid_generation_binding(
            self.generation_id, self.generation_sha256
        ):
            raise ValueError("query result generation ID or SHA-256 is invalid")
        if self.generation_id is not None and any(
            attempt.status == "complete"
            and (
                attempt.executed_generation_id != self.generation_id
                or attempt.executed_generation_sha256 != self.generation_sha256
            )
            for attempt in self.attempts
        ):
            raise ValueError("completed query attempt does not confirm the recorded Generation")
        if [item.attempt for item in self.attempts] != list(range(1, len(self.attempts) + 1)):
            raise ValueError("query attempts must be numbered sequentially from one")
        if any(item.cache_state != self.cache_state for item in self.attempts):
            raise ValueError("all retries of one query must use the same cache state")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens plus output_tokens")
        if self.input_tokens != sum(item.input_tokens for item in self.attempts):
            raise ValueError("input_tokens must retain usage from every attempt")
        if self.output_tokens != sum(item.output_tokens for item in self.attempts):
            raise ValueError("output_tokens must retain usage from every attempt")
        if self.tool_calls != sum(item.tool_calls for item in self.attempts):
            raise ValueError("tool_calls must retain usage from every attempt")
        if self.llm_calls != sum(item.llm_calls for item in self.attempts):
            raise ValueError("llm_calls must retain usage from every attempt")
        if self.steps != sum(item.steps for item in self.attempts):
            raise ValueError("steps must retain usage from every attempt")
        if self.status != self.attempts[-1].status and not self.budget_violations:
            raise ValueError("query status must match the final attempt unless a budget was exceeded")
        if self.synthetic_fixture != (self.provider_kind != "real" or self.split != "test"):
            raise ValueError("non-real providers and non-test fixtures must be marked synthetic")
        if self.formal_quality_eligible and (
            self.synthetic_fixture
            or self.provider_kind != "real"
            or self.split != "test"
            or self.status != "complete"
            or self.measurements is None
            or self.budget_violations
            or self.generation_id is None
            or self.generation_sha256 is None
            or (
                self.answerable
                and (
                    self.measurements.answer_correctness is None
                    or self.measurements.citation_precision is None
                    or self.measurements.human_reviewer_id is None
                    or self.measurements.human_reviewed_at is None
                )
            )
            or (
                not self.answerable
                and (
                    self.measurements.unanswerable_refusal_accuracy is None
                    or self.measurements.unanswerable_false_citation_rate is None
                )
            )
        ):
            raise ValueError(
                "formal quality eligibility requires a complete real-provider result and required human review"
            )
        return self


class ProviderExecutionError(RuntimeError):
    """Structured provider failure; only explicitly transient errors are retried."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        provider_profile: str,
        model_revision: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        tool_calls: int = 0,
        llm_calls: int = 0,
        steps: int = 0,
        strategy_latency_ms: int = 0,
        count_kind: CountKind = "unavailable",
        currency_cost_usd: Decimal | None = None,
        currency_cost_cny: Decimal | None = None,
        elapsed_ms: int = 0,
    ) -> None:
        self.code = redact_text(code)
        self.retryable = retryable
        self.provider_profile = provider_profile
        self.model_revision = model_revision
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.tool_calls = tool_calls
        self.llm_calls = llm_calls
        self.steps = steps
        self.strategy_latency_ms = strategy_latency_ms
        self.count_kind = count_kind
        self.currency_cost_usd = currency_cost_usd
        self.currency_cost_cny = currency_cost_cny
        self.elapsed_ms = elapsed_ms
        super().__init__(self.code)


class BenchmarkExecutor(Protocol):
    provider_kind: Literal["real", "mock", "synthetic"]

    async def execute_query(
        self,
        query: BlindBenchmarkInput,
        suite: ExperimentSuite,
        *,
        attempt: int,
        cache_state: Literal["cold", "warm"],
        generation: Generation | None = None,
        generation_sha256: str | None = None,
    ) -> AttemptOutcome: ...


async def run_query_with_retries(
    executor: BenchmarkExecutor,
    case: PreparedBenchmarkCase,
    suite: ExperimentSuite,
    *,
    formal_dataset: bool,
    cache_state: Literal["cold", "warm"] = "cold",
    generation_resolver: PersistedGenerationLookup | None = None,
    generation_binding: VerifiedGeneration | None = None,
    generation_id: str | None = None,
    generation_sha256: str | None = None,
    retry_delay_seconds: float = 0.25,
) -> QueryRunRecord:
    """Run one query sequentially; transient failures get at most three total attempts."""
    if not 0.0 <= retry_delay_seconds <= 30.0:
        raise ValueError("retry delay must be between zero and thirty seconds")
    if formal_dataset and executor.provider_kind != "real":
        raise ValueError("mock/synthetic provider results cannot enter a formal run")
    if formal_dataset and case.case.split.value != "test":
        raise ValueError("formal query runs are restricted to the frozen test split")
    blind_input = BlindBenchmarkInput(
        query_id=case.case.id,
        question=case.case.question,
        language=case.case.language,
    )
    if (generation_id is None) != (generation_sha256 is None):
        raise ValueError("query execution Generation ID and digest must be supplied together")
    if generation_binding is not None and generation_resolver is not None:
        raise ValueError("supply either a verified Generation or a Generation resolver")
    if generation_binding is None and generation_id is not None:
        if generation_resolver is None:
            raise ValueError("query execution requires a persisted Generation resolver")
        try:
            generation_binding = await resolve_generation(
                generation_resolver, generation_id, generation_sha256
            )
        except GenerationBindingError as exc:
            raise ValueError(f"Generation binding could not be verified: {exc}") from None
    elif generation_binding is not None:
        try:
            recomputed = verify_generation_manifest(generation_binding.generation)
        except GenerationBindingError as exc:
            raise ValueError(f"Generation binding could not be verified: {exc}") from None
        if recomputed.generation_sha256 != generation_binding.generation_sha256:
            raise ValueError("Generation binding digest differs from persisted manifest content")
        if generation_id is not None and (
            generation_id != recomputed.id or generation_sha256 != recomputed.generation_sha256
        ):
            raise ValueError("requested Generation binding differs from the verified Generation")
        generation_binding = recomputed
    if formal_dataset and generation_binding is None:
        raise ValueError("formal query runs require a verified persisted Generation")

    attempts: list[AttemptOutcome] = []
    started = time.perf_counter()
    for attempt_number in range(1, 4):
        try:
            execution_kwargs: dict[str, Any] = {
                "attempt": attempt_number,
                "cache_state": cache_state,
            }
            if generation_binding is not None:
                execution_kwargs.update(
                    generation=generation_binding.generation,
                    generation_sha256=generation_binding.generation_sha256,
                )
            outcome = await executor.execute_query(blind_input, suite, **execution_kwargs)
            if outcome.attempt != attempt_number:
                raise ValueError("executor returned an attempt number that does not match")
            if outcome.provider_kind != executor.provider_kind:
                raise ValueError("executor outcome provider kind does not match its capability")
            if outcome.cache_state != cache_state:
                raise ValueError("executor outcome cache state does not match the requested profile")
            if generation_binding is not None and (
                outcome.executed_generation_id != generation_binding.id
                or outcome.executed_generation_sha256 != generation_binding.generation_sha256
            ):
                raise ValueError("executor did not confirm the verified Generation it queried")
            if formal_dataset and outcome.measurements is not None:
                outcome = outcome.model_copy(
                    update={
                        "status": "failed",
                        "retryable": False,
                        "failure_code": "EXECUTOR_SCORE_FORBIDDEN",
                        "measurements": None,
                    }
                )
        except ProviderExecutionError as exc:
            outcome = AttemptOutcome(
                attempt=attempt_number,
                status="failed",
                retryable=exc.retryable,
                provider_kind=executor.provider_kind,
                provider_profile=exc.provider_profile,
                model_revision=exc.model_revision,
                failure_code=exc.code,
                input_tokens=exc.input_tokens,
                output_tokens=exc.output_tokens,
                tool_calls=exc.tool_calls,
                llm_calls=exc.llm_calls,
                steps=exc.steps,
                count_kind=exc.count_kind,
                currency_cost_usd=exc.currency_cost_usd,
                currency_cost_cny=exc.currency_cost_cny,
                elapsed_ms=exc.elapsed_ms,
                strategy_latency_ms=exc.strategy_latency_ms,
                cache_state=cache_state,
            )
        except Exception as exc:  # noqa: BLE001 - preserve unexpected failures in the run artifact.
            # Programming/configuration failures are retained and never retried implicitly.
            outcome = AttemptOutcome(
                attempt=attempt_number,
                status="failed",
                retryable=False,
                provider_kind=executor.provider_kind,
                provider_profile="unknown",
                model_revision="unknown",
                failure_code=redact_text(type(exc).__name__),
                cache_state=cache_state,
            )
        attempts.append(outcome)
        if outcome.status == "complete" or not outcome.retryable or attempt_number == 3:
            break
        if retry_delay_seconds:
            await asyncio.sleep(retry_delay_seconds * attempt_number)

    final = attempts[-1]
    measured = final.measurements if final.status == "complete" else None
    provider_kind = executor.provider_kind
    synthetic = provider_kind != "real"
    formal_quality_eligible = (
        formal_dataset
        and provider_kind == "real"
        and case.case.split.value == "test"
        and final.status == "complete"
        and measured is not None
        and (
            (
                case.case.answerable
                and measured.answer_correctness is not None
                and measured.citation_precision is not None
                and measured.human_reviewer_id is not None
                and measured.human_reviewed_at is not None
            )
            or (
                not case.case.answerable
                and measured.unanswerable_refusal_accuracy is not None
                and measured.unanswerable_false_citation_rate is not None
            )
        )
    )
    usd_cost = _sum_known_decimals([item.currency_cost_usd for item in attempts])
    cny_cost = _sum_known_decimals([item.currency_cost_cny for item in attempts])
    kinds = {item.count_kind for item in attempts}
    count_kind: CountKind = "exact" if kinds == {"exact"} else "estimated" if "estimated" in kinds else "unavailable"
    elapsed = max(
        int((time.perf_counter() - started) * 1000),
        sum(item.elapsed_ms for item in attempts),
    )
    return QueryRunRecord(
        suite_id=suite.suite_id,
        system_id=suite.system_id,
        budget_id=suite.budget_id,
        seed=suite.seed,
        ablation_id=suite.ablation_id,
        case_id=case.case.id,
        generation_id=generation_binding.id if generation_binding is not None else None,
        generation_sha256=(
            generation_binding.generation_sha256 if generation_binding is not None else None
        ),
        split=case.case.split.value,
        answerable=case.case.answerable,
        cache_state=cache_state,
        source_family_ids=case.case.source_family_ids,
        provider_kind=provider_kind,
        synthetic_fixture=synthetic,
        formal_quality_eligible=formal_quality_eligible,
        status=final.status,
        attempts=attempts,
        input_tokens=sum(item.input_tokens for item in attempts),
        output_tokens=sum(item.output_tokens for item in attempts),
        total_tokens=sum(item.input_tokens + item.output_tokens for item in attempts),
        tool_calls=sum(item.tool_calls for item in attempts),
        llm_calls=sum(item.llm_calls for item in attempts),
        steps=sum(item.steps for item in attempts),
        count_kind=count_kind,
        currency_cost_usd=usd_cost,
        currency_cost_cny=cny_cost,
        end_to_end_latency_ms=elapsed,
        strategy_latency_ms=sum(item.strategy_latency_ms for item in attempts),
        model_revisions=sorted({item.model_revision for item in attempts}),
        measurements=measured,
    )
