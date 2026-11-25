from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_generation_binding import _make_generation

from knowledge_weaver.agent import AgentCacheStateManifest
from knowledge_weaver.core.ids import deterministic_id
from knowledge_weaver.evaluation.agent_adapter import AgentSuiteRouteAdapter
from knowledge_weaver.evaluation.executors import _agent_execution_output
from knowledge_weaver.evaluation.generation_binding import VerifiedGeneration
from knowledge_weaver.evaluation.protocol import load_protocol
from knowledge_weaver.evaluation.runs import BlindBenchmarkInput
from knowledge_weaver.evaluation.scheduling import ExperimentSuite, SuiteStatus
from knowledge_weaver.evaluation.suite_registry import SuiteExecutionPlan
from knowledge_weaver.graph.retriever import GraphRetriever
from knowledge_weaver.models import (
    ActionTool,
    AgentExecutionResult,
    AgentExecutionTraceStep,
    AgentProviderCallUsage,
    Answer,
    Budget,
    CountKind,
    Language,
    ModelTokenUsage,
    QueryContext,
    Usage,
)
from knowledge_weaver.retrieval.cache_state import (
    CacheStateController,
    CacheStateOperation,
    CacheStateReceipt,
)
from knowledge_weaver.retrieval.indexed import DenseRetriever


class _AgentServiceSpy:
    def __init__(self, trace_sources: tuple[ActionTool, ...] = (ActionTool.bm25,)) -> None:
        self.requests = []
        self.profiles = []
        self.trace_sources = trace_sources

    async def investigate(self, request):
        raise AssertionError("B07 trace adapter must use the additive trace port")

    async def investigate_with_trace(self, request, execution_profile):
        self.requests.append(request)
        self.profiles.append(execution_profile)
        usage = Usage(
            tool_calls=1,
            llm_calls=1,
            input_tokens=10,
            output_tokens=4,
            count_kind=CountKind.exact,
        )
        answer = Answer(
            id=deterministic_id("b07_trace_answer", request.context.request_id),
            investigation_id=deterministic_id("b07_trace_investigation", request.context.request_id),
            context=request.context,
            status="insufficient",
            claims=[],
            unanswered=["No supported answer in the isolated fixture."],
            usage=usage,
        )
        steps = tuple(
            AgentExecutionTraceStep(
                sequence=sequence,
                step=sequence,
                action_id=deterministic_id(
                    "b07_trace_action",
                    {"request_id": request.context.request_id, "sequence": sequence},
                ),
                source=source,
                query_sha256="a" * 64,
                context=request.context,
                status="failed",
                usage=Usage(tool_calls=1, count_kind=CountKind.unavailable),
                error_code="ProviderUnavailable",
            )
            for sequence, source in enumerate(self.trace_sources, start=1)
        )
        return AgentExecutionResult(
            answer=answer,
            profile=execution_profile,
            generation_id=execution_profile.generation_id,
            generation_sha256=execution_profile.generation_sha256,
            provider_calls=(
                AgentProviderCallUsage(
                    sequence=1,
                    purpose="answer",
                    status="complete",
                    model="fixture-agent",
                    revision="r1",
                    usage=ModelTokenUsage(
                        input_tokens=10,
                        output_tokens=4,
                        count_kind=CountKind.exact,
                    ),
                ),
            ),
            retrieval_steps=steps,
            aggregate_usage=usage,
            validated_citation_ids=(),
        )


def _case_inputs():
    root = Path(__file__).resolve().parents[2]
    protocol = load_protocol(root / "benchmarks/configs/b07_protocol_v1.json")
    generation, generation_sha256 = _make_generation()
    verified = VerifiedGeneration(generation, generation_sha256)
    query = BlindBenchmarkInput(
        query_id="33333333-3333-4333-8333-333333333333",
        question="Summarize this synthetic behavior.",
        language=Language.en,
    )
    suite = ExperimentSuite(
        suite_id="system/rule/standard/seed-17",
        kind="system",
        system_id="rule",
        budget_id="standard",
        seed=17,
        ablation_id=None,
        status=SuiteStatus.planned,
        blockers=(),
        cache_states=("cold",),
        expected_query_runs=1,
    )
    adapter = AgentSuiteRouteAdapter(_AgentServiceSpy(), "agent.rule")
    plan = SuiteExecutionPlan(
        suite_id=suite.suite_id,
        scope="formal",
        system_id="rule",
        ablation_id=None,
        budget_id="standard",
        seed=17,
        dataset_content_sha256="b" * 64,
        route_id="agent.rule",
        route_config_sha256=adapter.config_sha256,
        generation_config_sha256="c" * 64,
        weights_sha256=None,
        execution_config_sha256="d" * 64,
        executable=True,
        blockers=(),
    )
    context = QueryContext(
        corpus_id=generation.corpus_id,
        generation_id=generation.id,
        request_id=query.query_id,
        deadline_utc=datetime.now(UTC) + timedelta(seconds=60),
        source_ids=None,
    )
    budget_spec = next(item for item in protocol.budgets if item.id == "standard")
    budget = Budget(
        max_steps=budget_spec.max_steps,
        max_tool_calls=budget_spec.max_tool_calls,
        max_llm_calls=budget_spec.max_llm_calls,
        max_total_tokens=budget_spec.max_total_tokens,
        deadline_utc=context.deadline_utc,
    )
    receipt = CacheStateReceipt(
        route_id=plan.route_id,
        requested_state="cold",
        applied_state="cold",
        implementation_sha256="e" * 64,
        config_sha256="f" * 64,
        controlled_components=("test.cache-scope",),
        uncontrolled_components=(),
        warmup_sequence=("test.cache-scope",),
        operations=(
            CacheStateOperation(
                sequence=1,
                component_id="test.cache-scope",
                operation="reset",
                verified=True,
            ),
        ),
        complete=True,
    )
    return protocol, generation, verified, query, suite, adapter, plan, context, budget, receipt


@pytest.mark.asyncio
async def test_agent_adapter_passes_only_blind_input_and_persists_trace_result() -> None:
    (
        protocol,
        _generation,
        verified,
        query,
        suite,
        adapter,
        plan,
        context,
        budget,
        receipt,
    ) = _case_inputs()

    result = await adapter.execute(
        query,
        suite,
        plan,
        protocol,
        generation=verified,
        context=context,
        budget=budget,
        generation_config_sha256="c" * 64,
        cache_state="cold",
        cache_state_receipt=receipt,
    )

    service = adapter.service
    request = service.requests[0]
    request_payload = request.model_dump(mode="json")
    assert request.query.text == query.question
    assert request.language is Language.en
    assert request.context.source_ids is None
    assert request.query.requested_sources == []
    assert "gold" not in str(request_payload).casefold()
    assert "review" not in str(request_payload).casefold()
    assert service.profiles[0].generation_sha256 == verified.generation_sha256
    assert service.profiles[0].execution_config_sha256 == plan.execution_config_sha256
    assert result.retrieval_steps[0].status == "failed"
    output, provider_usage, model_revision = _agent_execution_output(result)
    assert output.agent_execution == result
    assert output.agent_execution.retrieval_steps[0].error_code == "ProviderUnavailable"
    assert provider_usage.input_tokens == 10
    assert model_revision == "fixture-agent@r1"


@pytest.mark.asyncio
async def test_fixed_agent_adapter_rejects_out_of_order_public_trace() -> None:
    (
        protocol,
        _generation,
        verified,
        query,
        suite,
        _adapter,
        plan,
        context,
        budget,
        receipt,
    ) = _case_inputs()
    adapter = AgentSuiteRouteAdapter(
        _AgentServiceSpy(
            trace_sources=(ActionTool.hybrid, ActionTool.rerank, ActionTool.graph)
        ),
        "agent.fixed",
    )
    suite = replace(
        suite,
        suite_id="system/fixed_agent/standard/seed-17",
        system_id="fixed_agent",
    )
    plan = replace(
        plan,
        suite_id=suite.suite_id,
        system_id="fixed_agent",
        route_id="agent.fixed",
        route_config_sha256=adapter.config_sha256,
    )
    receipt = receipt.model_copy(update={"route_id": "agent.fixed"})

    with pytest.raises(ValueError, match="fixed_agent trace order"):
        await adapter.execute(
            query,
            suite,
            plan,
            protocol,
            generation=verified,
            context=context,
            budget=budget,
            generation_config_sha256="c" * 64,
            cache_state="cold",
            cache_state_receipt=receipt,
        )


@pytest.mark.asyncio
async def test_agent_adapter_fails_closed_on_unknown_cache_and_generation_hash() -> None:
    (
        protocol,
        _generation,
        verified,
        query,
        suite,
        adapter,
        plan,
        context,
        budget,
        receipt,
    ) = _case_inputs()

    with pytest.raises(ValueError, match="CACHE_SCOPE_INCOMPLETE"):
        await adapter.execute(
            query,
            suite,
            plan,
            protocol,
            generation=verified,
            context=context,
            budget=budget,
            generation_config_sha256="c" * 64,
            cache_state="cold",
            cache_state_receipt=None,
        )
    assert adapter.service.requests == []

    wrong_generation = VerifiedGeneration(verified.generation, "0" * 64)
    with pytest.raises(ValueError, match="mismatched verified Generation digest"):
        await adapter.execute(
            query,
            suite,
            plan,
            protocol,
            generation=wrong_generation,
            context=context,
            budget=budget,
            generation_config_sha256="c" * 64,
            cache_state="cold",
            cache_state_receipt=receipt,
        )
    assert adapter.service.requests == []


@pytest.mark.asyncio
async def test_b04_controller_uses_agent_nested_cache_manifest_and_keeps_external_scope_blocked() -> None:
    (
        _protocol,
        _generation,
        _verified,
        _query,
        _suite,
        _adapter,
        _plan,
        context,
        _budget,
        _receipt,
    ) = _case_inputs()
    graph = GraphRetriever(object(), object(), object())
    dense = DenseRetriever(object(), object())
    service = _AgentServiceSpy()
    service.cache_state_manifest = AgentCacheStateManifest(
        components={"dense": dense, "graph": graph},
        uncontrolled_components=(
            "generation-provider/shared-cache",
            "reranker/shared-provider-cache",
        ),
        manifest_sha256="d" * 64,
    )
    route = AgentSuiteRouteAdapter(service, "agent.rule")
    controller = CacheStateController.for_retrievers(
        {"agent.rule": route},
        uncontrolled_by_route={
            "agent.rule": ("agent.rule/answer-provider/shared-cache",)
        },
    )

    receipt = await controller.prepare("agent.rule", context, "cold")

    assert receipt.controlled_components == (
        "agent.rule/component/graph/graph-snapshot-cache",
    )
    assert receipt.applied_state == "cold"
    assert receipt.complete is False
    assert "agent.rule/unknown-cache-scope" not in receipt.uncontrolled_components
    assert "agent.rule/component/dense/embedding-provider/shared-cache" in receipt.uncontrolled_components
    assert "agent.rule/generation-provider/shared-cache" in receipt.uncontrolled_components
    assert "agent.rule/reranker/shared-provider-cache" in receipt.uncontrolled_components
    assert "agent.rule/answer-provider/shared-cache" in receipt.uncontrolled_components

    service.cache_state_manifest = AgentCacheStateManifest(
        components={"dense": dense, "graph": graph},
        uncontrolled_components=(
            "generation-provider/shared-cache",
            "reranker/shared-provider-cache",
        ),
        manifest_sha256="e" * 64,
    )
    changed = CacheStateController.for_retrievers({"agent.rule": route})
    assert changed.config_sha256_for("agent.rule") != receipt.config_sha256
