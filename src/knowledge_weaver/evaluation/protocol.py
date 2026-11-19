"""Versioned B07 matrix contract and loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)


class ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BudgetSpec(ProtocolModel):
    id: Literal["low", "standard", "high"]
    max_steps: int = Field(gt=0)
    max_tool_calls: int = Field(gt=0)
    max_llm_calls: int = Field(gt=0)
    max_total_tokens: int = Field(gt=0)
    deadline_seconds: int = Field(gt=0)


class SystemSpec(ProtocolModel):
    id: Annotated[str, StringConstraints(min_length=1)]
    behavior: Annotated[str, StringConstraints(min_length=1)]


class AblationSpec(ProtocolModel):
    id: Annotated[str, StringConstraints(pattern=r"^A(0[1-9]|10)$")]
    behavior: Annotated[str, StringConstraints(min_length=1)]


class FairnessControls(ProtocolModel):
    same_corpus_generation: Literal[True]
    same_answer_generator_and_prompt: Literal[True]
    same_verifier_and_hard_budget: Literal[True]
    reranker_availability_fixed: Literal[True]
    unavailable_provider_blocks_run: Literal[True]


class MetricPlan(ProtocolModel):
    retrieval_k: list[Literal[5, 10, 20]]
    retrieval_grouping: Literal["claim_group_first_occurrence"]
    precision_denominator: Literal["fixed_k_including_empty_slots"]
    ndcg_gain: Literal["2^grade_minus_1"]
    exclude_unanswerable_from_retrieval: Literal[True]
    answerability_metrics: list[str]
    citation_metrics: list[str]
    multihop_metrics: list[str]
    answer_correctness_scale: list[str]
    efficiency_metrics: list[str]
    unknown_currency_cost: Literal["null"]
    latency_concurrency: Literal[1]
    latency_cache_states: list[Literal["cold", "warm"]]
    bootstrap_unit: Literal["source_family"]
    bootstrap_confidence_level: float = Field(ge=0.95, le=0.95)


class EvaluationProtocol(ProtocolModel):
    schema_version: Literal[1] = 1
    plan_version: Literal["KW-P0-2026-11-18"]
    protocol_id: Annotated[str, StringConstraints(min_length=1)]
    dataset_target: Literal[100]
    categories: list[str]
    category_cases_each: Literal[20]
    language_minimum_each: Literal[40]
    unanswerable_minimum: Literal[10]
    source_family_minimum: Literal[15]
    split_family_minimum_each: Literal[5]
    test_case_minimum: Literal[40]
    test_category_minimum_each: Literal[6]
    split_case_targets: dict[str, int]
    systems: list[SystemSpec]
    budgets: list[BudgetSpec]
    ablations: list[AblationSpec]
    seeds: list[int]
    decision_log: list[Annotated[str, StringConstraints(min_length=1)]]
    fairness_controls: FairnessControls
    metric_plan: MetricPlan
    citation_candidate_limit: Literal[10]
    active_set_rerank_limit: Literal[40]
    formal_suite_count: Literal[111]
    suite_formula: Literal["(9*3+10)*3"]
    final_status: Literal["BLOCKED_ON_PREP_DATA_UPSTREAMS_PROVIDER_BUDGET"]

    @model_validator(mode="after")
    def frozen_protocol_shape(self) -> Self:
        expected_categories = {"single", "multihop", "cross_source", "conflict", "code"}
        expected_systems = {
            "bm25",
            "dense",
            "hybrid_rrf",
            "hybrid_rerank",
            "graph",
            "fixed_agent",
            "rule",
            "llm_router",
            "segr",
        }
        if set(self.categories) != expected_categories or len(self.categories) != 5:
            raise ValueError("protocol categories must match the five frozen benchmark categories")
        if {system.id for system in self.systems} != expected_systems or len(self.systems) != 9:
            raise ValueError("protocol must define the nine frozen baseline systems")
        if {budget.id for budget in self.budgets} != {"low", "standard", "high"} or len(
            self.budgets
        ) != 3:
            raise ValueError("protocol must define low, standard, and high budgets")
        if len(self.ablations) != 10 or {item.id for item in self.ablations} != {
            f"A{index:02d}" for index in range(1, 11)
        }:
            raise ValueError("protocol must define the ten separately named ablations")
        if len(self.seeds) != 3 or len(set(self.seeds)) != 3:
            raise ValueError("protocol must freeze three distinct seeds")
        if not self.decision_log:
            raise ValueError("protocol decisions must be recorded with the frozen config")
        if self.metric_plan.retrieval_k != [5, 10, 20]:
            raise ValueError("retrieval K must remain 5, 10, and 20")
        if set(self.metric_plan.answerability_metrics) != {
            "unanswerable_refusal_accuracy",
            "unanswerable_false_citation_rate",
        } or len(self.metric_plan.answerability_metrics) != 2:
            raise ValueError("protocol must report both no-answer metrics")
        if set(self.metric_plan.citation_metrics) != {
            "human_support_precision",
            "resolvable_citation_validity",
        } or len(self.metric_plan.citation_metrics) != 2:
            raise ValueError("citation support and validity must remain separate")
        if set(self.metric_plan.multihop_metrics) != {
            "complete_directional_path_accuracy",
            "gold_path_coverage",
        } or len(self.metric_plan.multihop_metrics) != 2:
            raise ValueError("protocol must report complete path accuracy and path coverage")
        if set(self.metric_plan.answer_correctness_scale) != {
            "incorrect",
            "partial",
            "sufficient_and_conflicts_handled",
        } or len(self.metric_plan.answer_correctness_scale) != 3:
            raise ValueError("answer correctness must retain the frozen three-level human scale")
        expected_efficiency_metrics = {
            "total_tokens",
            "currency_cost",
            "end_to_end_latency_p50_ms",
            "end_to_end_latency_p95_ms",
            "strategy_latency_p50_ms",
            "strategy_latency_p95_ms",
        }
        if set(self.metric_plan.efficiency_metrics) != expected_efficiency_metrics or len(
            self.metric_plan.efficiency_metrics
        ) != len(expected_efficiency_metrics):
            raise ValueError("protocol must preserve all frozen cost and latency metrics")
        if set(self.metric_plan.latency_cache_states) != {"cold", "warm"} or len(
            self.metric_plan.latency_cache_states
        ) != 2:
            raise ValueError("latency must be reported for cold and warm cache states")
        if self.metric_plan.bootstrap_unit != "source_family":
            raise ValueError("confidence intervals must resample by source family")
        if self.split_case_targets != {"train": 30, "dev": 20, "test": 50}:
            raise ValueError("target split counts must be train=30, dev=20, test=50")
        expected_budgets = {
            "low": (3, 6, 4, 6000, 30),
            "standard": (6, 12, 8, 12000, 60),
            "high": (8, 16, 10, 16000, 90),
        }
        for budget in self.budgets:
            actual = (
                budget.max_steps,
                budget.max_tool_calls,
                budget.max_llm_calls,
                budget.max_total_tokens,
                budget.deadline_seconds,
            )
            if actual != expected_budgets[budget.id]:
                raise ValueError(f"budget {budget.id} differs from the frozen evaluation plan")
        if self.computed_suite_count != self.formal_suite_count:
            raise ValueError("formal suite count must match the matrix formula")
        return self

    @property
    def computed_suite_count(self) -> int:
        return (len(self.systems) * len(self.budgets) + len(self.ablations)) * len(self.seeds)


def load_protocol(path: Path) -> EvaluationProtocol:
    try:
        return EvaluationProtocol.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise ValueError(f"evaluation protocol is invalid: {exc}") from exc
