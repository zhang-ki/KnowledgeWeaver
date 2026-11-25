"""B07 suite adapter over the public B06 AgentService trace port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from knowledge_weaver.agent.execution_profiles import (
    SUPPORTED_AGENT_ROUTE_IDS,
    agent_behavior_profile,
)
from knowledge_weaver.core.ids import deterministic_id
from knowledge_weaver.evaluation.generation_binding import (
    VerifiedGeneration,
    verify_generation_manifest,
)
from knowledge_weaver.evaluation.protocol import EvaluationProtocol
from knowledge_weaver.evaluation.runs import BlindBenchmarkInput
from knowledge_weaver.evaluation.scheduling import ExperimentSuite
from knowledge_weaver.models import (
    ActionTool,
    AgentExecutionProfile,
    AgentExecutionResult,
    Budget,
    InvestigationRequest,
    Query,
    QueryContext,
)
from knowledge_weaver.ports import AgentService
from knowledge_weaver.retrieval.cache_state import CacheStateReceipt

if TYPE_CHECKING:
    from knowledge_weaver.evaluation.suite_registry import SuiteExecutionPlan


def _validate_fixed_agent_trace(result: AgentExecutionResult) -> None:
    sources = tuple(step.source for step in result.retrieval_steps)
    allowed = {
        (),
        (ActionTool.hybrid,),
        (ActionTool.hybrid, ActionTool.graph),
        (ActionTool.hybrid, ActionTool.graph, ActionTool.hybrid),
        (ActionTool.hybrid, ActionTool.graph, ActionTool.rerank),
        (ActionTool.hybrid, ActionTool.graph, ActionTool.hybrid, ActionTool.rerank),
    }
    if sources not in allowed:
        raise ValueError(
            "fixed_agent trace order must be hybrid, graph, optional expanded hybrid, then rerank"
        )


@dataclass(frozen=True, slots=True)
class AgentSuiteRouteAdapter:
    """One route-bound view over an AgentService; its profile is never caller-selected."""

    service: AgentService
    route_id: str

    supports_b07_agent_trace = True

    def __post_init__(self) -> None:
        if not isinstance(self.service, AgentService):
            raise TypeError("B07 Agent route requires the public AgentService trace port")
        agent_behavior_profile(self.route_id)

    @property
    def config_sha256(self) -> str:
        return agent_behavior_profile(self.route_id).config_sha256

    def matches_route(self, route_id: str) -> bool:
        behavior = agent_behavior_profile(self.route_id, require_supported=False)
        return (
            route_id == self.route_id
            and behavior.supported
            and self.config_sha256 == behavior.config_sha256
        )

    async def execute(
        self,
        query: BlindBenchmarkInput,
        suite: ExperimentSuite,
        plan: SuiteExecutionPlan,
        protocol: EvaluationProtocol,
        *,
        generation: VerifiedGeneration,
        context: QueryContext,
        budget: Budget,
        generation_config_sha256: str,
        cache_state: Literal["cold", "warm"],
        cache_state_receipt: CacheStateReceipt | None,
    ) -> AgentExecutionResult:
        if type(query) is not BlindBenchmarkInput:
            raise TypeError("Agent B07 adapter accepts only BlindBenchmarkInput")
        behavior = agent_behavior_profile(self.route_id)
        observed = (suite.system_id, suite.ablation_id, suite.budget_id, suite.seed)
        expected = (plan.system_id, plan.ablation_id, plan.budget_id, plan.seed)
        if observed != expected or suite.suite_id != plan.suite_id:
            raise ValueError("Agent suite identity differs from its immutable execution plan")
        if plan.route_id != self.route_id or plan.route_config_sha256 != self.config_sha256:
            raise ValueError("Agent route/config differs from its frozen B06 adapter")
        if plan.generation_config_sha256 != generation_config_sha256:
            raise ValueError("Agent generation config SHA differs from the frozen suite plan")
        if not plan.executable:
            raise ValueError("Agent suite is blocked: " + "; ".join(plan.blockers))
        if (
            cache_state_receipt is None
            or cache_state_receipt.route_id != self.route_id
            or cache_state_receipt.requested_state != cache_state
            or cache_state_receipt.applied_state != cache_state
            or not cache_state_receipt.complete
        ):
            raise ValueError("CACHE_SCOPE_INCOMPLETE: Agent retrieval cache state is not verified")
        verified = verify_generation_manifest(generation.generation)
        if verified.generation_sha256 != generation.generation_sha256:
            raise ValueError("Agent adapter received a mismatched verified Generation digest")
        if (
            context.corpus_id != verified.generation.corpus_id
            or context.generation_id != verified.id
            or context.request_id != query.query_id
            or context.source_ids is not None
        ):
            raise ValueError("Agent request context differs from the blinded verified Generation")
        requires_weights = behavior.system_id == "segr" or behavior.ablation_id is not None
        if requires_weights != (plan.weights_sha256 is not None):
            raise ValueError("Agent suite weights identity is inconsistent with its frozen profile")

        budget_spec = next((item for item in protocol.budgets if item.id == suite.budget_id), None)
        if budget_spec is None:
            raise ValueError("Agent suite references an unknown frozen budget")
        expected_budget = (
            budget_spec.max_steps,
            budget_spec.max_tool_calls,
            budget_spec.max_llm_calls,
            budget_spec.max_total_tokens,
        )
        actual_budget = (
            budget.max_steps,
            budget.max_tool_calls,
            budget.max_llm_calls,
            budget.max_total_tokens,
        )
        if expected_budget != actual_budget or budget.deadline_utc != context.deadline_utc:
            raise ValueError("Agent request budget differs from its frozen suite budget")
        request = InvestigationRequest(
            query=Query(
                id=deterministic_id("b07_agent_query", {"query_id": query.query_id}),
                text=query.question,
                context=context,
            ),
            context=context,
            budget=budget,
            policy=behavior.policy,
            language=query.language,
            features=behavior.request_features(),
        )
        profile = AgentExecutionProfile(
            suite_id=plan.suite_id,
            system_id=plan.system_id,
            ablation_id=plan.ablation_id,
            route_id=plan.route_id,
            config_sha256=plan.route_config_sha256,
            execution_config_sha256=plan.execution_config_sha256,
            generation_config_sha256=generation_config_sha256,
            weights_sha256=plan.weights_sha256,
            generation_id=verified.id,
            generation_sha256=verified.generation_sha256,
        )
        result = await self.service.investigate_with_trace(request, profile)
        if type(result) is not AgentExecutionResult:
            raise TypeError("AgentService returned an unsupported trace result DTO")
        if (
            result.profile != profile
            or result.generation_id != verified.id
            or result.generation_sha256 != verified.generation_sha256
            or result.answer.context != context
        ):
            raise ValueError("AgentService trace result differs from the frozen request binding")
        if self.route_id == "agent.fixed":
            _validate_fixed_agent_trace(result)
        return result


def build_agent_suite_adapters(service: AgentService) -> dict[str, AgentSuiteRouteAdapter]:
    """Return one immutable route wrapper per supported frozen B07 Agent identity."""
    return {
        route_id: AgentSuiteRouteAdapter(service=service, route_id=route_id)
        for route_id in SUPPORTED_AGENT_ROUTE_IDS
    }
