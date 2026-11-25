from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from knowledge_weaver.core.budget import BudgetLedger
from knowledge_weaver.evaluation import fixed_agent_dev
from knowledge_weaver.evaluation.fixed_agent_dev import (
    DevOnlyTextReranker,
    DevOnlyTextRerankerFactory,
    FixedAgentDevCheck,
    FixedAgentDevPreflightError,
    LocalDevChannelFactory,
    LocalDevRerankerFactory,
    LoopbackB07ChannelConstructor,
    build_fixed_agent_dev_assembly,
    preflight_fixed_agent_dev,
    run_fixed_agent_dev_query,
)
from knowledge_weaver.evaluation.local_generation import DevLocalOpenAIChatProfile
from knowledge_weaver.graph.retriever import GraphRetriever
from knowledge_weaver.indexing import dense as dense_module
from knowledge_weaver.indexing.bm25 import Bm25Index
from knowledge_weaver.indexing.dense import QdrantDenseIndex
from knowledge_weaver.indexing.graph_store import PostgresGraphStore
from knowledge_weaver.indexing.symbol import EntityIndex, SymbolIndex
from knowledge_weaver.models import Budget, QueryContext, RetrievalHit
from knowledge_weaver.providers.embedding import LocalQwenEmbeddingProvider
from knowledge_weaver.providers.fake import FakeProvider
from knowledge_weaver.providers.profiles import LocalQwenProfile
from knowledge_weaver.providers.reranking import CloudReranker
from knowledge_weaver.retrieval.hybrid import HybridRetriever
from knowledge_weaver.retrieval.indexed import (
    Bm25Retriever,
    DenseRetriever,
    EntityRetriever,
    SymbolRetriever,
)
from knowledge_weaver.storage.knowledge_store import PostgresKnowledgeStore


class _LocalReranker:
    def __init__(self, config: dict[str, str]) -> None:
        self.b07_dev_local_config = config

    async def rerank(self, query: str, hits: list[object], context: object, limit: int) -> list[object]:
        raise AssertionError("construction preflight must not invoke the reranker")


def _local_factory(tmp_path: Path, build):
    artifact = tmp_path / "reranker-model.bin"
    artifact.write_bytes(b"local reranker model fixture for assembly-only test")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    config = {"model_id": "local-test-reranker", "model_sha256": digest}
    return (
        LocalDevRerankerFactory(
            build=build,
            model_id="local-test-reranker",
            artifact_path=artifact,
            artifact_sha256=digest,
            config={"precision": "fp32"},
        ),
        config,
    )


def _local_services() -> dict[str, tuple[str, int]]:
    return {
        "postgres": ("127.0.0.1", 5432),
        "qdrant": ("127.0.0.1", 56333),
        "embedding": ("127.0.0.1", 56336),
    }


class _NoNetworkQdrantClient:
    def __init__(self, url: str, *, api_key: str | None = None) -> None:
        parsed = urlsplit(url)
        self._client = SimpleNamespace(_host=parsed.hostname, _port=parsed.port)


def _components(tmp_path: Path, collection_prefix: str, monkeypatch):
    monkeypatch.setattr(dense_module, "AsyncQdrantClient", _NoNetworkQdrantClient)
    database = SimpleNamespace(
        engine=SimpleNamespace(
            url=SimpleNamespace(host="127.0.0.1", port=5432),
        )
    )
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    store.database = database
    store.lease_seconds = 300

    graph_store = PostgresGraphStore.__new__(PostgresGraphStore)
    graph_store.database = database
    graph_store.knowledge_store = store
    entity_root = tmp_path / f"{collection_prefix}-graph-entity"
    graph = GraphRetriever(graph_store, EntityIndex(entity_root), store)

    embedding_profile = LocalQwenProfile(
        endpoint_url="http://127.0.0.1:56336/v1/embeddings",
        model="qwen3-embedding-0.6b",
        dimensions=1024,
    )

    def build_channels(ledger: BudgetLedger):
        root = tmp_path / f"{collection_prefix}-indices"
        provider = LocalQwenEmbeddingProvider(embedding_profile, ledger)
        return {
            "bm25": Bm25Retriever(Bm25Index(root / "bm25"), store),
            "dense": DenseRetriever(
                QdrantDenseIndex(
                    "http://127.0.0.1:56333",
                    provider,
                    collection_prefix=collection_prefix,
                ),
                store,
            ),
            "symbol": SymbolRetriever(SymbolIndex(root / "symbol"), store),
            "entity": EntityRetriever(EntityIndex(root / "entity"), store),
        }

    channel_factory = LocalDevChannelFactory(
        build=build_channels,
        config={"collection_prefix": collection_prefix, "index_root": str(tmp_path)},
    )
    return store, channel_factory, graph


def _profile() -> DevLocalOpenAIChatProfile:
    return DevLocalOpenAIChatProfile(
        "http://127.0.0.1:56337/v1/chat/completions", "local-test-chat"
    )


def test_preflight_reports_local_reranker_and_components_as_blocking() -> None:
    report = preflight_fixed_agent_dev(
        knowledge_store=None,
        channel_factory=None,
        graph_retriever=None,
        reranker_factory=None,
        generation_profile=None,
        local_services={"qdrant": ("127.0.0.1", 1)},
    )

    assert report.ready is False
    assert report.component_config_sha256 is None
    assert any("only CloudReranker" in item for item in report.blockers)
    assert any("PostgresKnowledgeStore" in item for item in report.blockers)
    assert any("not listening" in item for item in report.blockers)


def test_endpoint_preflight_refuses_non_loopback_before_connecting() -> None:
    check = fixed_agent_dev._probe_endpoint("remote", "203.0.113.5", 443)

    assert check.available is False
    assert check.detail == "endpoint is not a valid loopback TCP address"


@pytest.mark.parametrize("kind", ["fake", "cloud"])
def test_preflight_rejects_fake_and_cloud_rerankers(tmp_path, kind: str) -> None:
    if kind == "fake":
        build = lambda _ledger: FakeProvider()
    else:
        build = lambda _ledger: CloudReranker.__new__(CloudReranker)
    factory, _config = _local_factory(tmp_path, build)

    report = preflight_fixed_agent_dev(
        knowledge_store=None,
        channel_factory=None,
        graph_retriever=None,
        reranker_factory=factory,
        generation_profile=None,
    )

    reranker_check = next(item for item in report.checks if item.component == "separate local reranker")
    assert reranker_check.available is False
    assert "construction failed" in reranker_check.detail
    assert report.component_config_sha256 is None


def test_preflight_binds_sha_to_actual_channel_configuration(tmp_path, monkeypatch) -> None:
    factory, local_config = _local_factory(
        tmp_path,
        lambda _ledger: _LocalReranker(local_config),
    )
    monkeypatch.setattr(
        fixed_agent_dev,
        "_probe_endpoint",
        lambda component, host, port: FixedAgentDevCheck(component, True, "test loopback"),
    )
    first = _components(tmp_path, "first", monkeypatch)
    second = _components(tmp_path, "second", monkeypatch)

    first_report = preflight_fixed_agent_dev(
        knowledge_store=first[0],
        channel_factory=first[1],
        graph_retriever=first[2],
        reranker_factory=factory,
        generation_profile=_profile(),
        local_services=_local_services(),
    )
    second_report = preflight_fixed_agent_dev(
        knowledge_store=second[0],
        channel_factory=second[1],
        graph_retriever=second[2],
        reranker_factory=factory,
        generation_profile=_profile(),
        local_services=_local_services(),
    )

    assert first_report.ready is True
    assert second_report.ready is True
    assert first_report.component_config_sha256 != second_report.component_config_sha256


def test_builder_constructs_budget_bound_ports_without_invoking_providers(tmp_path, monkeypatch) -> None:
    factory, local_config = _local_factory(
        tmp_path,
        lambda _ledger: _LocalReranker(local_config),
    )
    monkeypatch.setattr(
        fixed_agent_dev,
        "_probe_endpoint",
        lambda component, host, port: FixedAgentDevCheck(component, True, "test loopback"),
    )
    store, channel_factory, graph = _components(tmp_path, "assembly", monkeypatch)
    assembly = build_fixed_agent_dev_assembly(
        knowledge_store=store,
        channel_factory=channel_factory,
        graph_retriever=graph,
        reranker_factory=factory,
        generation_profile=_profile(),
        local_services=_local_services(),
    )
    ledger = BudgetLedger(Budget(deadline_utc=datetime.now(UTC) + timedelta(seconds=30)))
    retrievers = assembly.retriever_factory(ledger)

    assert assembly.route_adapter.service is assembly.agent_service
    assert assembly.agent_service.cache_state_manifest is not None
    assert set(assembly.agent_service.cache_state_manifest.components) == {
        "bm25",
        "dense",
        "symbol",
        "entity",
        "graph",
    }
    assert assembly.agent_service.cache_state_manifest.components["graph"] is graph
    assert (
        assembly.agent_service.cache_state_manifest.manifest_sha256
        == assembly.component_config_sha256
    )
    assert retrievers["graph"] is graph
    assert isinstance(retrievers["hybrid"], HybridRetriever)
    assert retrievers["hybrid"].reranker is None
    assert retrievers["hybrid"].channels == {key: value for key, value in retrievers.items() if key in {"bm25", "dense", "symbol", "entity"}}
    assert retrievers["dense"].index.provider.budget is ledger
    assert len(assembly.generation_config_sha256) == 64
    assert len(assembly.component_config_sha256) == 64


def test_loopback_constructor_builds_the_actual_b04_channel_types(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dense_module, "AsyncQdrantClient", _NoNetworkQdrantClient)
    store, _channel_factory, _graph = _components(tmp_path, "constructor", monkeypatch)
    constructor = LoopbackB07ChannelConstructor(
        knowledge_store=store,
        index_root=tmp_path,
        qdrant_url="http://127.0.0.1:56333",
        collection_prefix="b07dev_constructor",
        embedding_profile=LocalQwenProfile(
            endpoint_url="http://127.0.0.1:56336/v1/embeddings",
            model="qwen3-embedding-0.6b",
            dimensions=1024,
        ),
        _qdrant_api_key="unit-test-only",
    )
    ledger = BudgetLedger(Budget(deadline_utc=datetime.now(UTC) + timedelta(seconds=30)))

    channels = constructor(ledger)

    assert set(channels) == {"bm25", "dense", "symbol", "entity"}
    assert isinstance(channels["bm25"], Bm25Retriever)
    assert isinstance(channels["dense"], DenseRetriever)
    assert isinstance(channels["symbol"], SymbolRetriever)
    assert isinstance(channels["entity"], EntityRetriever)
    assert channels["dense"].index.provider.budget is ledger
    assert "unit-test-only" not in str(fixed_agent_dev._safe_config(constructor))


def test_builder_fails_closed_before_any_query_when_local_reranker_is_absent(tmp_path, monkeypatch) -> None:
    store, channel_factory, graph = _components(tmp_path, "blocked", monkeypatch)
    with pytest.raises(FixedAgentDevPreflightError) as caught:
        build_fixed_agent_dev_assembly(
            knowledge_store=store,
            channel_factory=channel_factory,
            graph_retriever=graph,
            reranker_factory=None,  # type: ignore[arg-type]
            generation_profile=_profile(),
            local_services=_local_services(),
        )

    assert any("only CloudReranker" in item for item in caught.value.report.blockers)


@pytest.mark.asyncio
async def test_dev_only_text_reranker_scores_hydrated_text_and_uses_stable_order() -> None:
    target_id = "b80f776d-38f2-5379-99a7-fac8dead54ee"
    other_id = "6dfc640b-ac07-5637-9985-139dbfc159b5"

    class _HydratingStore:
        async def get_chunks(self, ids, _context):
            assert set(ids) == {target_id, other_id}
            return [
                SimpleNamespace(
                    id=other_id,
                    text='"""Self authored synthetic B07 persistence fixture."""',
                ),
                SimpleNamespace(
                    id=target_id,
                    text='def acceptance_codename() -> str:\n    return "LARKSPUR"',
                ),
            ]

    budget = BudgetLedger(Budget(deadline_utc=datetime.now(UTC) + timedelta(seconds=30)))
    reranker = DevOnlyTextReranker(
        _HydratingStore(), budget, DevOnlyTextRerankerFactory().config
    )
    context = QueryContext(
        corpus_id="9fe42200-d034-4989-9c08-cd755ad1c262",
        generation_id="30152134-f2d0-4395-bc0a-420a1b9439c2",
        request_id="d06e22ce-679c-4a4e-a38d-f0e9d7d6a8e4",
        deadline_utc=datetime.now(UTC) + timedelta(seconds=30),
    )
    hits = [
        RetrievalHit(chunk_id=other_id, evidence_ids=[], score=0.5, channel="hybrid", rank=1),
        RetrievalHit(chunk_id=target_id, evidence_ids=[], score=0.4, channel="hybrid", rank=2),
    ]

    ranked = await reranker.rerank(
        "What does acceptance_codename return?", hits, context, limit=10
    )

    assert [hit.chunk_id for hit in ranked] == [target_id, other_id]
    assert ranked[0].rerank_score == pytest.approx(0.6)
    assert [hit.rank for hit in ranked] == [1, 2]
    assert reranker.budget is budget


def test_dev_only_text_assembly_cannot_create_a_formal_route_adapter(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        fixed_agent_dev,
        "_probe_endpoint",
        lambda component, host, port: FixedAgentDevCheck(component, True, "test loopback"),
    )
    store, channel_factory, graph = _components(tmp_path, "dev-text", monkeypatch)
    assembly = build_fixed_agent_dev_assembly(
        knowledge_store=store,
        channel_factory=channel_factory,
        graph_retriever=graph,
        reranker_factory=DevOnlyTextRerankerFactory(),
        generation_profile=_profile(),
        local_services=_local_services(),
    )

    reranker = assembly.agent_service._make_reranker(
        BudgetLedger(Budget(deadline_utc=datetime.now(UTC) + timedelta(seconds=30)))
    )
    assert assembly.dev_only is True
    assert assembly.route_adapter is None
    assert assembly.component_manifest["separate_reranker"]["dev_only"] is True
    assert assembly.component_manifest["separate_reranker"]["formal_eligible"] is False
    assert assembly.component_manifest["evidence_verifier"]["timeout_seconds"] == 90.0
    assert assembly.component_manifest["evidence_verifier"]["max_output_tokens"] == 512
    assert isinstance(reranker, DevOnlyTextReranker)


@pytest.mark.asyncio
async def test_dev_query_runner_rejects_gold_bearing_case_object_before_provider_call() -> None:
    assembly = SimpleNamespace(dev_only=True, route_adapter=None)
    with pytest.raises(TypeError, match="only BlindBenchmarkInput"):
        await run_fixed_agent_dev_query(
            assembly,  # type: ignore[arg-type]
            SimpleNamespace(query_id="q", question="synthetic", language="en"),  # type: ignore[arg-type]
            generation=None,  # type: ignore[arg-type]
            context=None,  # type: ignore[arg-type]
            budget=None,  # type: ignore[arg-type]
        )
