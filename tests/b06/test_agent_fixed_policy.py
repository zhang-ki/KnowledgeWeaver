from __future__ import annotations

from typing import Any

import pytest
from conftest import (
    EmptyEntityIndex,
    EmptyGraphStore,
    FakeReranker,
    FakeStore,
    FakeVerifier,
    RecordingProvider,
    make_budget,
    make_chunk_evidence,
    make_context,
)

from knowledge_weaver.agent import AgentServiceImpl, agent_behavior_profile
from knowledge_weaver.core.errors import ErrorCode, KnowledgeWeaverError
from knowledge_weaver.core.ids import deterministic_id
from knowledge_weaver.graph import GraphRetriever
from knowledge_weaver.models import (
    ActionTool,
    AgentExecutionProfile,
    InvestigationRequest,
    Language,
    Query,
    RetrievalHit,
    RetrievalResult,
    SubQuestion,
    Usage,
)
from knowledge_weaver.retrieval import HybridRetriever


class _SyntheticRetriever:
    def __init__(
        self,
        source: str,
        chunks: dict[str, Any],
        evidence: dict[str, Any],
        order: list[str],
        *,
        fail: bool = False,
    ) -> None:
        self.source = source
        self.chunks = chunks
        self.evidence = evidence
        self.order = order
        self.fail = fail
        self.queries = []

    async def retrieve(self, query):
        self.queries.append(query)
        self.order.append(self.source)
        if self.fail:
            raise KnowledgeWeaverError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "synthetic fixed-agent route failure",
                retryable=True,
            )
        chunk = self.chunks[self.source]
        citation = self.evidence[self.source]
        return RetrievalResult(
            context=query.context,
            hits=[
                RetrievalHit(
                    chunk_id=chunk.id,
                    evidence_ids=[citation.id],
                    score=1.0,
                    channel=self.source,
                    rank=1,
                )
            ],
            usage=Usage(tool_calls=1, count_kind="exact"),
        )


def _fixed_case(*, max_steps: int = 6, fail_hybrid: bool = False, include_hybrid: bool = True):
    context = make_context()
    texts = {
        "bm25": "Synthetic bm25 passage explains route_probe and stage order.",
        "dense": "Synthetic dense passage confirms the fixed hybrid retrieval stage.",
        "symbol": "Synthetic symbol passage names route_probe.",
        "entity": "Synthetic entity passage describes the route component.",
        "graph": "Synthetic graph passage describes the isolated graph stage.",
    }
    chunks = {}
    evidence = {}
    for source, text in texts.items():
        chunk, citation = make_chunk_evidence(text=text)
        chunks[source] = chunk
        evidence[source] = citation
    store = FakeStore(
        {item.id: item for item in chunks.values()},
        {item.id: item for item in evidence.values()},
    )
    order: list[str] = []
    channel_fail = fail_hybrid
    channels = {
        source: _SyntheticRetriever(
            source,
            chunks,
            evidence,
            order,
            fail=channel_fail,
        )
        for source in ("bm25", "dense", "symbol", "entity")
    }
    graph = _SyntheticRetriever("graph", chunks, evidence, order)
    retrievers: dict[str, Any] = {
        **channels,
        "graph": graph,
    }
    if include_hybrid:
        retrievers["hybrid"] = HybridRetriever(channels)
    provider = RecordingProvider()
    reranker = FakeReranker()
    service = AgentServiceImpl(
        store,
        retrievers,
        provider,
        reranker=reranker,
        verifier_factory=lambda _generate: FakeVerifier(support=1.0),
        generation_config_sha256="c" * 64,
    )
    query = Query(
        id=deterministic_id("fixed_agent_query", context.request_id),
        text="Explain route_probe and provide the synthetic route order.",
        context=context,
        subquestions=[
            SubQuestion(
                id=deterministic_id("fixed_agent_facet", context.request_id),
                text="What does route_probe do?",
            )
        ],
    )
    behavior = agent_behavior_profile("agent.fixed")
    request = InvestigationRequest(
        query=query,
        context=context,
        budget=make_budget(
            context,
            max_steps=max_steps,
            max_tool_calls=8,
            max_total_tokens=12_000,
        ),
        policy=behavior.policy,
        language=Language.en,
        features=behavior.request_features(),
    )
    profile = AgentExecutionProfile(
        suite_id="system/fixed_agent/standard/seed-17",
        system_id="fixed_agent",
        route_id="agent.fixed",
        config_sha256=behavior.config_sha256,
        execution_config_sha256="f" * 64,
        generation_config_sha256="c" * 64,
        generation_id=context.generation_id,
        generation_sha256="e" * 64,
    )
    return service, request, profile, channels, graph, reranker, provider, order


@pytest.mark.asyncio
async def test_fixed_agent_executes_hybrid_graph_expansion_then_rerank_with_real_trace() -> None:
    service, request, profile, channels, graph, reranker, provider, order = _fixed_case()

    result = await service.investigate_with_trace(request, profile)

    assert [step.source for step in result.retrieval_steps] == [
        ActionTool.hybrid,
        ActionTool.graph,
        ActionTool.hybrid,
        ActionTool.rerank,
    ]
    assert [step.sequence for step in result.retrieval_steps] == [1, 2, 3, 4]
    assert result.retrieval_steps[0].hits
    assert {hit.channel for hit in result.retrieval_steps[0].hits} == {"hybrid"}
    assert {
        contribution["channel"]
        for hit in result.retrieval_steps[0].hits
        for contribution in hit.contributions
    } == {
        "bm25",
        "dense",
        "symbol",
        "entity",
    }
    assert result.retrieval_steps[2].query_sha256 != result.retrieval_steps[0].query_sha256
    assert graph.queries[0].graph_options.max_hops == 2
    assert all(len(channels[source].queries) == 2 for source in channels)
    assert reranker.calls == 1
    assert len(provider.requests) == 1
    assert result.profile.config_sha256 == profile.config_sha256
    assert all(step.context.generation_id == profile.generation_id for step in result.retrieval_steps)
    assert order[0] in {"bm25", "dense", "symbol", "entity"}


@pytest.mark.asyncio
async def test_fixed_agent_graph_stage_uses_real_entity_index_and_allows_empty_seed_result() -> None:
    service, request, profile, _channels, _graph, _reranker, _provider, _order = _fixed_case()
    graph_index = EmptyEntityIndex()
    service.retrievers = {
        **service.retrievers,
        "graph": GraphRetriever(EmptyGraphStore(), graph_index, service.knowledge_store),
    }

    result = await service.investigate_with_trace(request, profile)

    assert [step.source for step in result.retrieval_steps] == [
        ActionTool.hybrid,
        ActionTool.graph,
        ActionTool.hybrid,
        ActionTool.rerank,
    ]
    graph_step = result.retrieval_steps[1]
    assert graph_step.status == "complete"
    assert graph_step.hits == ()
    assert len(graph_index.queries) == 1


@pytest.mark.asyncio
async def test_fixed_agent_honors_step_budget_before_later_phases() -> None:
    service, request, profile, channels, graph, reranker, _provider, _order = _fixed_case(
        max_steps=1
    )

    result = await service.investigate_with_trace(request, profile)

    assert [step.source for step in result.retrieval_steps] == [ActionTool.hybrid]
    assert all(len(channels[source].queries) == 1 for source in channels)
    assert graph.queries == []
    assert reranker.calls == 0
    assert service.get_trace(result.answer.investigation_id)[-1]["stop_reason"] == "budget_exhausted"


@pytest.mark.asyncio
async def test_fixed_agent_records_hybrid_failure_then_advances_to_graph() -> None:
    service, request, profile, _channels, graph, _reranker, _provider, _order = _fixed_case(
        max_steps=2,
        fail_hybrid=True,
    )

    result = await service.investigate_with_trace(request, profile)

    assert [step.source for step in result.retrieval_steps] == [
        ActionTool.hybrid,
        ActionTool.graph,
    ]
    assert result.retrieval_steps[0].status == "failed"
    assert result.retrieval_steps[0].error_code == ErrorCode.INDEX_NOT_READY.value
    assert result.retrieval_steps[1].status == "complete"
    assert graph.queries[0].graph_options.max_hops == 2


@pytest.mark.asyncio
async def test_fixed_agent_config_sha_mismatch_fails_before_any_runtime_call() -> None:
    service, request, profile, channels, graph, reranker, provider, _order = _fixed_case()

    with pytest.raises(KnowledgeWeaverError, match="ID/config binding"):
        await service.investigate_with_trace(
            request,
            profile.model_copy(update={"config_sha256": "0" * 64}),
        )

    assert all(not item.queries for item in channels.values())
    assert graph.queries == []
    assert reranker.calls == 0
    assert provider.requests == []


@pytest.mark.asyncio
async def test_fixed_agent_requires_hybrid_and_reranker_before_provider_calls() -> None:
    service, request, profile, channels, graph, _reranker, provider, _order = _fixed_case(
        include_hybrid=False
    )

    with pytest.raises(KnowledgeWeaverError, match="requires the configured B04 hybrid"):
        await service.investigate_with_trace(request, profile)

    assert all(not item.queries for item in channels.values())
    assert graph.queries == []
    assert provider.requests == []


@pytest.mark.asyncio
async def test_fixed_agent_requires_final_reranker_before_hybrid_call() -> None:
    service, request, profile, channels, graph, _reranker, provider, _order = _fixed_case()
    service.reranker = None

    with pytest.raises(KnowledgeWeaverError, match="requires the configured final reranker"):
        await service.investigate_with_trace(request, profile)

    assert all(not item.queries for item in channels.values())
    assert graph.queries == []
    assert provider.requests == []
