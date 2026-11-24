from __future__ import annotations

from typing import Any

import pytest
from conftest import (
    FakeStore,
    FakeVerifier,
    RecordingProvider,
    make_budget,
    make_chunk_evidence,
    make_context,
    make_query,
)
from test_agent import _weights

from knowledge_weaver.agent import AgentServiceImpl, agent_behavior_profile
from knowledge_weaver.core.errors import ErrorCode, KnowledgeWeaverError
from knowledge_weaver.models import (
    ActionTool,
    AgentExecutionProfile,
    CountKind,
    InvestigationRequest,
    Language,
    PolicyKind,
    RetrievalHit,
    RetrievalResult,
    Usage,
)


class _SpyRetriever:
    def __init__(self, source: str, make_result: Any, order: list[str]) -> None:
        self.source = source
        self.make_result = make_result
        self.order = order
        self.calls = 0

    async def retrieve(self, query):
        self.calls += 1
        self.order.append(self.source)
        return self.make_result(query)


def _profile(context, route_id: str, *, weights_sha256: str | None = None):
    behavior = agent_behavior_profile(route_id)
    return AgentExecutionProfile(
        suite_id=(
            f"ablation/{behavior.ablation_id}/standard/seed-17"
            if behavior.ablation_id
            else f"system/{behavior.system_id}/standard/seed-17"
        ),
        system_id=behavior.system_id,
        ablation_id=behavior.ablation_id,
        route_id=route_id,
        config_sha256=behavior.config_sha256,
        execution_config_sha256="f" * 64,
        generation_config_sha256="c" * 64,
        weights_sha256=weights_sha256,
        generation_id=context.generation_id,
        generation_sha256="e" * 64,
    )


def _service_and_request(
    context,
    *,
    route_id: str,
    items: list[tuple[str, str]],
    failing_sources: set[str] | None = None,
    max_steps: int = 2,
    query_text: str = "What does build_index do?",
):
    chunks = {}
    evidence = {}
    results = {}
    for source, text in items:
        chunk, citation = make_chunk_evidence(text=text)
        chunks[chunk.id] = chunk
        evidence[citation.id] = citation
        results[source] = RetrievalResult(
            context=context,
            hits=[
                RetrievalHit(
                    chunk_id=chunk.id,
                    evidence_ids=[citation.id],
                    score=1.0,
                    channel=source,
                    rank=1,
                )
            ],
            usage=Usage(tool_calls=1, count_kind=CountKind.exact),
        )
    order: list[str] = []
    failing = failing_sources or set()

    class Spy(_SpyRetriever):
        async def retrieve(self, query):
            self.calls += 1
            self.order.append(self.source)
            if self.source in failing:
                raise KnowledgeWeaverError(
                    ErrorCode.PROVIDER_UNAVAILABLE, "isolated retriever failure"
                )
            return results[self.source].model_copy(update={"context": query.context})

    retrievers = {
        source: Spy(source, None, order)
        for source in ("bm25", "dense", "symbol", "entity", "graph")
    }
    store = FakeStore(chunks, evidence)
    provider = RecordingProvider()
    behavior = agent_behavior_profile(route_id)
    weights = _weights() if behavior.system_id == "segr" else None
    weights_sha256 = "d" * 64 if weights is not None else None
    service = AgentServiceImpl(
        store,
        retrievers,
        provider,
        verifier_factory=lambda _generate: FakeVerifier(support=1.0),
        weights=weights,
        weights_sha256=weights_sha256,
        generation_config_sha256="c" * 64,
    )
    profile = _profile(context, route_id, weights_sha256=weights_sha256)
    request = InvestigationRequest(
        query=make_query(context, query_text),
        context=context,
        budget=make_budget(context, max_steps=max_steps, max_tool_calls=8),
        policy=behavior.policy,
        language=Language.en,
        features=behavior.request_features(),
    )
    return service, request, profile, retrievers, order, provider


@pytest.mark.asyncio
async def test_trace_preserves_live_retrieval_order_hits_generation_and_usage() -> None:
    context = make_context()
    service, request, profile, retrievers, order, provider = _service_and_request(
        context,
        route_id="agent.segr.ablation.A01",
        items=[
            ("bm25", "The first synthetic passage supports build_index."),
            ("dense", "The second synthetic passage also supports build_index."),
        ],
        max_steps=2,
    )

    result = await service.investigate_with_trace(request, profile)

    assert order == [step.source.value for step in result.retrieval_steps] == ["bm25", "dense"]
    assert [step.sequence for step in result.retrieval_steps] == [1, 2]
    assert [step.step for step in result.retrieval_steps] == [1, 2]
    assert [step.hits[0].channel for step in result.retrieval_steps] == ["bm25", "dense"]
    assert all(step.context.generation_id == context.generation_id for step in result.retrieval_steps)
    assert all(step.status == "complete" and step.error_code is None for step in result.retrieval_steps)
    assert result.generation_id == profile.generation_id
    assert result.generation_sha256 == profile.generation_sha256
    assert result.aggregate_usage == result.answer.usage
    assert result.provider_calls[-1].purpose == "answer"
    assert result.provider_calls[-1].model == "fixture-model-v1"
    assert result.provider_calls[-1].usage.input_tokens == 10
    assert len(provider.requests) == 1
    assert retrievers["bm25"].calls == retrievers["dense"].calls == 1


@pytest.mark.asyncio
async def test_trace_retains_failed_retrieval_step_before_later_success() -> None:
    context = make_context()
    service, request, profile, _retrievers, order, _provider = _service_and_request(
        context,
        route_id="agent.rule",
        items=[
            ("bm25", "The first synthetic passage supports build_index."),
            ("dense", "The second synthetic passage supports build_index."),
        ],
        failing_sources={"bm25"},
        query_text="Describe the repository behavior.",
    )

    result = await service.investigate_with_trace(request, profile)

    assert order[:2] == ["bm25", "dense"]
    assert result.retrieval_steps[0].source is ActionTool.bm25
    assert result.retrieval_steps[0].status == "failed"
    assert result.retrieval_steps[0].error_code == ErrorCode.PROVIDER_UNAVAILABLE.value
    assert result.retrieval_steps[0].hits == ()
    assert result.retrieval_steps[1].source is ActionTool.dense
    assert result.retrieval_steps[1].status == "complete"
    assert result.retrieval_steps[1].hits[0].channel == "dense"


@pytest.mark.asyncio
async def test_mismatched_generation_result_fails_before_answer_generation() -> None:
    context = make_context()
    service, request, profile, retrievers, _order, provider = _service_and_request(
        context,
        route_id="agent.rule",
        items=[("bm25", "The first synthetic passage supports build_index.")],
        max_steps=1,
        query_text="Describe the repository behavior.",
    )
    retriever = retrievers["bm25"]
    original_retrieve = retriever.retrieve

    async def wrong_generation(query):
        result = await original_retrieve(query)
        other_context = query.context.model_copy(
            update={"generation_id": "99999999-9999-4999-8999-999999999999"}
        )
        return result.model_copy(update={"context": other_context})

    retriever.retrieve = wrong_generation

    with pytest.raises(KnowledgeWeaverError, match="different query context"):
        await service.investigate_with_trace(request, profile)

    assert retriever.calls == 1
    assert len(provider.requests) == 0


@pytest.mark.asyncio
async def test_config_and_generation_bindings_fail_before_retriever_or_provider_calls() -> None:
    context = make_context()
    service, request, profile, retrievers, _order, provider = _service_and_request(
        context,
        route_id="agent.rule",
        items=[("bm25", "The first synthetic passage supports build_index.")],
        max_steps=1,
        query_text="Describe the repository behavior.",
    )

    wrong_config = profile.model_copy(update={"config_sha256": "0" * 64})
    with pytest.raises(KnowledgeWeaverError, match="ID/config binding"):
        await service.investigate_with_trace(request, wrong_config)

    wrong_generation = profile.model_copy(
        update={"generation_id": "99999999-9999-4999-8999-999999999999"}
    )
    with pytest.raises(KnowledgeWeaverError, match="Generation differs"):
        await service.investigate_with_trace(request, wrong_generation)

    assert all(adapter.calls == 0 for adapter in retrievers.values())
    assert len(provider.requests) == 0


@pytest.mark.asyncio
async def test_weights_and_generation_provider_hashes_fail_before_provider_calls() -> None:
    context = make_context()
    service, request, profile, retrievers, _order, provider = _service_and_request(
        context,
        route_id="agent.segr.ablation.A01",
        items=[("bm25", "The first synthetic passage supports build_index.")],
        max_steps=1,
    )

    wrong_weights = profile.model_copy(update={"weights_sha256": "0" * 64})
    with pytest.raises(KnowledgeWeaverError, match="weights SHA"):
        await service.investigate_with_trace(request, wrong_weights)

    wrong_generation_config = profile.model_copy(
        update={"generation_config_sha256": "0" * 64}
    )
    with pytest.raises(KnowledgeWeaverError, match="generation config SHA"):
        await service.investigate_with_trace(request, wrong_generation_config)

    assert all(adapter.calls == 0 for adapter in retrievers.values())
    assert len(provider.requests) == 0


def test_supported_agent_profiles_have_distinct_routes_and_config_hashes() -> None:
    from knowledge_weaver.agent.execution_profiles import SUPPORTED_AGENT_ROUTE_IDS

    profiles = [agent_behavior_profile(route_id) for route_id in SUPPORTED_AGENT_ROUTE_IDS]
    assert len(profiles) == 13
    assert len({item.route_id for item in profiles}) == len(profiles)
    assert len({item.config_sha256 for item in profiles}) == len(profiles)
    assert agent_behavior_profile("agent.segr.ablation.A01").policy is PolicyKind.fixed
    assert agent_behavior_profile("agent.segr.ablation.A08").request_features() == {
        "enable_source_features": False
    }
    with pytest.raises(ValueError, match="fixed hybrid"):
        agent_behavior_profile("agent.fixed")
