"""Explicit, loopback-only assembly for the fixed_agent B07 Dev route.

This module constructs the existing B04/B06 ports from caller-supplied runtime
components. It never supplies fixture retrievers, fake generation, or a cloud
reranker. The returned component digest is separate from the frozen policy
route digest and binds the actual constructed components.
"""

from __future__ import annotations

import hashlib
import inspect
import ipaddress
import json
import math
import re
import socket
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from types import (
    CodeType,
    FrameType,
    FunctionType,
    MappingProxyType,
    MethodType,
    ModuleType,
    TracebackType,
)
from typing import Any
from urllib.parse import urlsplit

from knowledge_weaver.agent.execution_profiles import agent_behavior_profile
from knowledge_weaver.agent.service import AgentServiceImpl
from knowledge_weaver.agent.verifier import StructuredEvidenceVerifier
from knowledge_weaver.core.budget import BudgetLedger
from knowledge_weaver.core.ids import deterministic_id
from knowledge_weaver.evaluation.agent_adapter import AgentSuiteRouteAdapter
from knowledge_weaver.evaluation.generation_binding import (
    VerifiedGeneration,
    verify_generation_manifest,
)
from knowledge_weaver.evaluation.local_generation import (
    DevLocalOpenAIChatProfile,
    DevLocalOpenAICompatibleGenerationProvider,
)
from knowledge_weaver.evaluation.runs import BlindBenchmarkInput
from knowledge_weaver.graph.retriever import GraphRetriever
from knowledge_weaver.indexing.bm25 import TOKENIZER_VERSION, Bm25Index, tokenize
from knowledge_weaver.indexing.dense import QdrantDenseIndex
from knowledge_weaver.indexing.graph_store import PostgresGraphStore
from knowledge_weaver.indexing.symbol import EntityIndex, SymbolIndex
from knowledge_weaver.models import (
    AgentExecutionProfile,
    AgentExecutionResult,
    Budget,
    InvestigationRequest,
    LLMRequest,
    LLMResult,
    Query,
    QueryContext,
    RetrievalHit,
)
from knowledge_weaver.ports import EvidenceVerifier, KnowledgeStore, Reranker, Retriever
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

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHANNEL_TYPES: Mapping[str, type[Retriever]] = MappingProxyType(
    {
        "bm25": Bm25Retriever,
        "dense": DenseRetriever,
        "symbol": SymbolRetriever,
        "entity": EntityRetriever,
    }
)
_CHANNEL_INDEX_TYPES: Mapping[str, type[object]] = MappingProxyType(
    {
        "bm25": Bm25Index,
        "dense": QdrantDenseIndex,
        "symbol": SymbolIndex,
        "entity": EntityIndex,
    }
)
_REQUIRED_LOCAL_SERVICES = frozenset({"postgres", "qdrant", "embedding"})
_DEV_VERIFIER_TIMEOUT_SECONDS = 90.0
_DEV_VERIFIER_MAX_OUTPUT_TOKENS = 512
_SECRET_FIELD = re.compile(
    r"secret|password|credential|api.?key|authorization|token", re.IGNORECASE
)
_SKIP_CONFIG_FIELDS = {"budget", "transport", "sleep", "engine", "sessions"}


@dataclass(frozen=True, slots=True)
class FixedAgentDevCheck:
    component: str
    available: bool
    detail: str


@dataclass(frozen=True, slots=True)
class FixedAgentDevPreflight:
    checks: tuple[FixedAgentDevCheck, ...]
    component_config_sha256: str | None

    @property
    def ready(self) -> bool:
        return all(item.available for item in self.checks)

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(
            f"{item.component}: {item.detail}" for item in self.checks if not item.available
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "component_config_sha256": self.component_config_sha256,
            "checks": [
                {
                    "component": item.component,
                    "available": item.available,
                    "detail": item.detail,
                }
                for item in self.checks
            ],
            "blockers": list(self.blockers),
        }


class FixedAgentDevPreflightError(RuntimeError):
    def __init__(self, report: FixedAgentDevPreflight) -> None:
        self.report = report
        super().__init__("fixed_agent Dev construction blocked: " + "; ".join(report.blockers))


@dataclass(frozen=True, slots=True)
class LocalDevRerankerFactory:
    """A budget-aware local reranker factory bound to a verified model artifact."""

    build: Callable[[BudgetLedger], Reranker]
    model_id: str
    artifact_path: Path
    artifact_sha256: str
    config: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not callable(self.build):
            raise TypeError("local reranker builder must be callable")
        if not self.model_id.strip():
            raise ValueError("local reranker model_id is required")
        if not _SHA256.fullmatch(self.artifact_sha256):
            raise ValueError("local reranker artifact SHA must be a lowercase SHA-256 digest")
        object.__setattr__(self, "artifact_path", Path(self.artifact_path))
        object.__setattr__(self, "config", MappingProxyType(dict(self.config)))


@dataclass(frozen=True, slots=True)
class LocalDevChannelFactory:
    """Build the four B04 adapters with the per-investigation budget ledger."""

    build: Callable[[BudgetLedger], Mapping[str, Retriever]]
    config: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not callable(self.build):
            raise TypeError("local B04 channel builder must be callable")
        object.__setattr__(self, "config", MappingProxyType(dict(self.config)))


@dataclass(frozen=True, slots=True)
class LoopbackB07ChannelConstructor:
    """Construct actual B04 channel adapters from one isolated local index scope."""

    knowledge_store: KnowledgeStore
    index_root: Path
    qdrant_url: str
    collection_prefix: str
    embedding_profile: LocalQwenProfile
    _qdrant_api_key: str = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "index_root", Path(self.index_root))
        if not self.collection_prefix.startswith("b07dev_"):
            raise ValueError("Dev Qdrant collection prefix must be uniquely marked b07dev_")
        if not self._qdrant_api_key.strip():
            raise ValueError("local Qdrant API key is required for the isolated Dev collection")

    def __call__(self, ledger: BudgetLedger) -> Mapping[str, Retriever]:
        provider = LocalQwenEmbeddingProvider(self.embedding_profile, ledger)
        dense_index = QdrantDenseIndex(
            self.qdrant_url,
            provider,
            api_key=self._qdrant_api_key,
            collection_prefix=self.collection_prefix,
        )
        return {
            "bm25": Bm25Retriever(Bm25Index(self.index_root), self.knowledge_store),
            "dense": DenseRetriever(dense_index, self.knowledge_store),
            "symbol": SymbolRetriever(SymbolIndex(self.index_root), self.knowledge_store),
            "entity": EntityRetriever(EntityIndex(self.index_root), self.knowledge_store),
        }


@dataclass(frozen=True, slots=True)
class DevOnlyTextRerankerFactory:
    """Deterministic local text reranker, explicitly ineligible for formal B07."""

    dev_only: bool = True
    formal_eligible: bool = False

    @property
    def config(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {
                "profile": "dev-only-local-text-reranker-v1",
                "algorithm": "query-token-coverage",
                "tokenizer_version": TOKENIZER_VERSION,
                "score": "intersection(query_terms, chunk_terms)/query_term_count",
                "tie_break": ["incoming_rank", "chunk_id"],
                "dev_only": self.dev_only,
                "formal_eligible": self.formal_eligible,
            }
        )

    @property
    def config_sha256(self) -> str:
        return _canonical_sha256(dict(self.config))

    def build(self, knowledge_store: KnowledgeStore, budget: BudgetLedger) -> Reranker:
        return DevOnlyTextReranker(knowledge_store, budget, self.config)


class DevOnlyTextReranker:
    """Scores real persisted chunk text with deterministic B04 lexical tokenization."""

    def __init__(
        self,
        knowledge_store: KnowledgeStore,
        budget: BudgetLedger,
        config: Mapping[str, Any],
    ) -> None:
        self.knowledge_store = knowledge_store
        self.budget = budget
        self.b07_dev_only_config = MappingProxyType(dict(config))
        self.b07_dev_only_config_sha256 = _canonical_sha256(dict(config))

    async def rerank(
        self,
        query: str,
        hits: list[RetrievalHit],
        context: QueryContext,
        limit: int,
    ) -> list[RetrievalHit]:
        if type(context) is not QueryContext:
            raise TypeError("Dev-only text reranker requires the public QueryContext DTO")
        if limit < 1:
            raise ValueError("rerank limit must be positive")
        chunk_ids = [hit.chunk_id for hit in hits]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("rerank input contains duplicate chunks")
        chunks = await self.knowledge_store.get_chunks(chunk_ids, context)
        by_id = {chunk.id: chunk for chunk in chunks}
        if set(by_id) != set(chunk_ids):
            raise ValueError("B03 did not hydrate every reranker chunk from the requested Generation")
        query_terms = set(tokenize(query))
        denominator = max(1, len(query_terms))
        ranked = []
        for hit in hits:
            chunk_terms = set(tokenize(by_id[hit.chunk_id].text))
            score = len(query_terms & chunk_terms) / denominator
            ranked.append((hit, score))
        ranked.sort(key=lambda row: (-row[1], row[0].rank, row[0].chunk_id))
        return [
            hit.model_copy(update={"rank": rank, "rerank_score": score})
            for rank, (hit, score) in enumerate(ranked[:limit], 1)
        ]


@dataclass(frozen=True, slots=True)
class FixedAgentDevAssembly:
    retriever_factory: Callable[[BudgetLedger], Mapping[str, Retriever]]
    hybrid_retriever_factory: Callable[[BudgetLedger], HybridRetriever]
    graph_retriever: GraphRetriever
    agent_service: AgentServiceImpl
    route_adapter: AgentSuiteRouteAdapter | None
    generation_config_sha256: str
    component_config_sha256: str
    component_manifest: Mapping[str, Any]
    dev_only: bool


def preflight_fixed_agent_dev(
    *,
    knowledge_store: object | None,
    channel_factory: LocalDevChannelFactory | None,
    graph_retriever: object | None,
    reranker_factory: LocalDevRerankerFactory | DevOnlyTextRerankerFactory | None,
    generation_profile: DevLocalOpenAIChatProfile | None,
    local_services: Mapping[str, tuple[str, int]] | None = None,
) -> FixedAgentDevPreflight:
    """Validate a real local composition without issuing retrieval or model calls."""
    checks: list[FixedAgentDevCheck] = []
    service_map = dict(local_services or {})
    store_ok = _is_local_knowledge_store(knowledge_store, service_map)
    checks.append(
        FixedAgentDevCheck(
            "B03 KnowledgeStore",
            store_ok,
            "PostgresKnowledgeStore is bound to the configured loopback PostgreSQL endpoint"
            if store_ok
            else "requires PostgresKnowledgeStore on the configured loopback PostgreSQL endpoint",
        )
    )

    channel_ledger = _new_probe_ledger()
    channels, channel_error = _probe_channels(channel_factory, channel_ledger)
    channels_ok = (
        channels is not None
        and _validate_channels(
            channels,
            knowledge_store,
            channel_ledger,
            service_map,
        )
        is None
    )
    if not channels_ok and channel_error is None:
        channel_error = "channel instances do not match the local B04 ports or endpoint/budget configuration"
    checks.append(
        FixedAgentDevCheck(
            "B04 four-channel Hybrid",
            channels_ok,
            "per-investigation factory builds real BM25/Dense/Symbol/Entity adapters with local embedding and Qdrant"
            if channels_ok
            else channel_error or "local channel factory is missing",
        )
    )

    graph_ok = (
        isinstance(graph_retriever, GraphRetriever)
        and getattr(graph_retriever, "knowledge_store", None) is knowledge_store
        and _is_local_graph_retriever(graph_retriever, knowledge_store, service_map)
    )
    checks.append(
        FixedAgentDevCheck(
            "B05 GraphRetriever",
            graph_ok,
            "PostgresGraphStore and EntityIndex share the same loopback PostgreSQL and KnowledgeStore"
            if graph_ok
            else "requires a GraphRetriever backed by the supplied local PostgresKnowledgeStore and local EntityIndex",
        )
    )

    reranker, reranker_reason = _probe_reranker(reranker_factory, knowledge_store)
    checks.append(FixedAgentDevCheck("separate local reranker", reranker is not None, reranker_reason))

    generation_ok = isinstance(generation_profile, DevLocalOpenAIChatProfile)
    checks.append(
        FixedAgentDevCheck(
            "local Generation",
            generation_ok,
            "loopback-only OpenAI-compatible profile supplied"
            if generation_ok
            else "requires DevLocalOpenAIChatProfile; cloud/fake generation is not accepted",
        )
    )
    if generation_ok and generation_profile is not None:
        parsed = urlsplit(generation_profile.endpoint_url)
        checks.append(
            _probe_endpoint(
                "local chat endpoint",
                parsed.hostname or "",
                parsed.port or 0,
            )
        )

    for name, endpoint in sorted((local_services or {}).items()):
        host, port = endpoint
        checks.append(_probe_endpoint(name, host, port))
    missing_services = sorted(_REQUIRED_LOCAL_SERVICES - set(local_services or {}))
    checks.append(
        FixedAgentDevCheck(
            "isolated retrieval backends",
            not missing_services,
            "explicit loopback endpoints supplied for PostgreSQL, Qdrant, and query embedding"
            if not missing_services
            else "missing loopback endpoint configuration for " + ", ".join(missing_services),
        )
    )

    manifest: dict[str, Any] | None = None
    config_sha256: str | None = None
    if (
        store_ok
        and channels_ok
        and graph_ok
        and reranker is not None
        and generation_ok
        and all(item.available for item in checks)
    ):
        assert channels is not None and channel_factory is not None
        assert graph_retriever is not None
        assert generation_profile is not None
        manifest = _component_manifest(
            knowledge_store=knowledge_store,
            channels=channels,
            channel_factory=channel_factory,
            graph_retriever=graph_retriever,
            reranker=reranker,
            reranker_factory=reranker_factory,
            generation_profile=generation_profile,
            local_services=local_services or {},
        )
        config_sha256 = _canonical_sha256(manifest)
    return FixedAgentDevPreflight(tuple(checks), config_sha256)


def build_fixed_agent_dev_assembly(
    *,
    knowledge_store: KnowledgeStore,
    channel_factory: LocalDevChannelFactory,
    graph_retriever: GraphRetriever,
    reranker_factory: LocalDevRerankerFactory | DevOnlyTextRerankerFactory,
    generation_profile: DevLocalOpenAIChatProfile,
    local_services: Mapping[str, tuple[str, int]] | None = None,
) -> FixedAgentDevAssembly:
    """Construct the existing B04/B05/B06 runtime only after a ready preflight."""
    report = preflight_fixed_agent_dev(
        knowledge_store=knowledge_store,
        channel_factory=channel_factory,
        graph_retriever=graph_retriever,
        reranker_factory=reranker_factory,
        generation_profile=generation_profile,
        local_services=local_services,
    )
    if not report.ready or report.component_config_sha256 is None:
        raise FixedAgentDevPreflightError(report)

    reranker_probe, reranker_reason = _probe_reranker(reranker_factory, knowledge_store)
    if reranker_probe is None:
        refreshed = preflight_fixed_agent_dev(
            knowledge_store=knowledge_store,
            channel_factory=channel_factory,
            graph_retriever=graph_retriever,
            reranker_factory=reranker_factory,
            generation_profile=generation_profile,
            local_services=local_services,
        )
        if refreshed.ready:
            refreshed = FixedAgentDevPreflight(
                (
                    *refreshed.checks,
                    FixedAgentDevCheck("separate local reranker", False, reranker_reason),
                ),
                None,
            )
        raise FixedAgentDevPreflightError(refreshed)

    probe_ledger = _new_probe_ledger()
    probe_channels, channel_error = _probe_channels(channel_factory, probe_ledger)
    if probe_channels is None:
        raise FixedAgentDevPreflightError(
            FixedAgentDevPreflight(
                (*report.checks, FixedAgentDevCheck("B04 four-channel Hybrid", False, channel_error or "local channel factory failed")),
                None,
            )
        )
    channel_issue = _validate_channels(
        probe_channels, knowledge_store, probe_ledger, dict(local_services or {})
    )
    if channel_issue:
        raise FixedAgentDevPreflightError(
            FixedAgentDevPreflight(
                (*report.checks, FixedAgentDevCheck("B04 four-channel Hybrid", False, channel_issue)),
                None,
            )
        )
    channels_sha256 = _canonical_sha256(
        {
            name: _implementation_descriptor(channel)
            for name, channel in sorted(probe_channels.items())
        }
    )
    channel_factory_identity = _canonical_sha256(
        {
            "implementation": _implementation_descriptor(channel_factory.build),
            "config": _safe_config(channel_factory.config),
        }
    )

    def build_retrievers(ledger: BudgetLedger) -> Mapping[str, Retriever]:
        observed_factory_identity = _canonical_sha256(
            {
                "implementation": _implementation_descriptor(channel_factory.build),
                "config": _safe_config(channel_factory.config),
            }
        )
        if observed_factory_identity != channel_factory_identity:
            raise ValueError("B04 channel factory/config changed after preflight")
        channels, error = _probe_channels(channel_factory, ledger)
        if channels is None:
            raise ValueError(error or "local B04 channel factory failed")
        issue = _validate_channels(channels, knowledge_store, ledger, dict(local_services or {}))
        if issue:
            raise ValueError(issue)
        observed_sha256 = _canonical_sha256(
            {
                name: _implementation_descriptor(channel)
                for name, channel in sorted(channels.items())
            }
        )
        if observed_sha256 != channels_sha256:
            raise ValueError("B04 channel implementation/config changed after preflight")
        hybrid = HybridRetriever(channels, reranker=None)
        return {**channels, "graph": graph_retriever, "hybrid": hybrid}

    def build_hybrid(ledger: BudgetLedger) -> HybridRetriever:
        retrievers = build_retrievers(ledger)
        hybrid = retrievers.get("hybrid")
        if not isinstance(hybrid, HybridRetriever):
            raise TypeError("B04 route did not build a HybridRetriever")
        return hybrid

    generation_sha = _canonical_sha256(
        {
            "provider": _implementation_descriptor(DevLocalOpenAICompatibleGenerationProvider),
            "profile": _safe_config(generation_profile),
        }
    )

    expected_reranker_descriptor = _reranker_descriptor(reranker_probe, reranker_factory)
    reranker_factory_identity = _reranker_factory_identity(reranker_factory)

    def generation_factory(ledger: BudgetLedger) -> DevLocalOpenAICompatibleGenerationProvider:
        return DevLocalOpenAICompatibleGenerationProvider(generation_profile, ledger)

    def bound_reranker_factory(ledger: BudgetLedger) -> Reranker:
        observed_factory_identity = _reranker_factory_identity(reranker_factory)
        if observed_factory_identity != reranker_factory_identity:
            raise ValueError("local reranker factory/config changed after Dev preflight")
        if isinstance(reranker_factory, DevOnlyTextRerankerFactory):
            reranker = reranker_factory.build(knowledge_store, ledger)
            _validate_dev_only_text_reranker(reranker, reranker_factory)
        else:
            reranker = reranker_factory.build(ledger)
            _validate_reranker_instance(reranker, reranker_factory)
        if _reranker_descriptor(reranker, reranker_factory) != expected_reranker_descriptor:
            raise ValueError("local reranker implementation/config changed after Dev preflight")
        return reranker

    agent = AgentServiceImpl(
        knowledge_store,
        build_retrievers,
        generation_factory,
        reranker=bound_reranker_factory,
        verifier_factory=_make_dev_evidence_verifier,
        generation_config_sha256=generation_sha,
    )
    dev_only = isinstance(reranker_factory, DevOnlyTextRerankerFactory)
    adapter = None if dev_only else AgentSuiteRouteAdapter(agent, "agent.fixed")
    report_manifest = _component_manifest(
        knowledge_store=knowledge_store,
        channels=probe_channels,
        channel_factory=channel_factory,
        graph_retriever=graph_retriever,
        reranker=reranker_probe,
        reranker_factory=reranker_factory,
        generation_profile=generation_profile,
        local_services=local_services or {},
    )
    return FixedAgentDevAssembly(
        retriever_factory=build_retrievers,
        hybrid_retriever_factory=build_hybrid,
        graph_retriever=graph_retriever,
        agent_service=agent,
        route_adapter=adapter,
        generation_config_sha256=generation_sha,
        component_config_sha256=_canonical_sha256(report_manifest),
        component_manifest=MappingProxyType(report_manifest),
        dev_only=dev_only,
    )


async def run_fixed_agent_dev_query(
    assembly: FixedAgentDevAssembly,
    query: BlindBenchmarkInput,
    *,
    generation: VerifiedGeneration,
    context: QueryContext,
    budget: Budget,
) -> AgentExecutionResult:
    """Run one non-formal blinded query through the existing public B06 trace port."""
    if not assembly.dev_only or assembly.route_adapter is not None:
        raise ValueError("single-query Dev runner requires the formal-ineligible text reranker route")
    if type(query) is not BlindBenchmarkInput:
        raise TypeError("B07 Dev query runner accepts only BlindBenchmarkInput")
    if type(generation) is not VerifiedGeneration:
        raise TypeError("B07 Dev query runner requires a verified persisted Generation")
    verified = verify_generation_manifest(generation.generation)
    if verified.generation_sha256 != generation.generation_sha256:
        raise ValueError("B07 Dev runner received a mismatched Generation digest")
    if (
        context.corpus_id != verified.generation.corpus_id
        or context.generation_id != verified.id
        or context.request_id != query.query_id
        or context.source_ids is not None
    ):
        raise ValueError("Dev query context differs from the blinded Generation binding")

    behavior = agent_behavior_profile("agent.fixed")
    reranker_config = assembly.component_manifest["separate_reranker"]
    if reranker_config.get("formal_eligible") is not False:
        raise ValueError("Dev-only reranker manifest unexpectedly permits formal use")
    execution_sha = _canonical_sha256(
        {
            "profile": "b07-dev-only-fixed-agent-execution-v1",
            "component_config_sha256": assembly.component_config_sha256,
            "reranker_config_sha256": reranker_config.get("config_sha256"),
            "formal_eligible": False,
        }
    )
    profile = AgentExecutionProfile(
        suite_id="dev/synthetic/fixed-agent/local-text-reranker",
        system_id="fixed_agent",
        route_id="agent.fixed",
        config_sha256=behavior.config_sha256,
        execution_config_sha256=execution_sha,
        generation_config_sha256=assembly.generation_config_sha256,
        generation_id=verified.id,
        generation_sha256=verified.generation_sha256,
    )
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
    result = await assembly.agent_service.investigate_with_trace(request, profile)
    if type(result) is not AgentExecutionResult or result.profile != profile:
        raise TypeError("B06 returned an unsupported or mismatched Dev trace result")
    return result


def _probe_reranker(
    factory: LocalDevRerankerFactory | DevOnlyTextRerankerFactory | None,
    knowledge_store: object | None,
) -> tuple[Reranker | None, str]:
    if isinstance(factory, DevOnlyTextRerankerFactory):
        if not factory.dev_only or factory.formal_eligible:
            return None, "deterministic text reranker must remain Dev-only and formal-ineligible"
        if not isinstance(knowledge_store, KnowledgeStore):
            return None, "Dev-only text reranker requires the supplied B03 KnowledgeStore port"
        try:
            instance = factory.build(knowledge_store, _new_probe_ledger())
            _validate_dev_only_text_reranker(instance, factory)
        except Exception as exc:  # noqa: BLE001 - preflight reports the failure class only.
            return None, f"Dev-only text reranker construction failed ({type(exc).__name__})"
        return instance, "deterministic text reranker verified; Dev-only and formal-ineligible"
    return _probe_local_reranker(factory)


def _probe_local_reranker(
    factory: LocalDevRerankerFactory | None,
) -> tuple[Reranker | None, str]:
    if factory is None:
        return (
            None,
            "no local implementation or model artifact is registered; the repository provides only CloudReranker",
        )
    try:
        artifact_sha256 = _artifact_sha256(factory.artifact_path)
    except (OSError, ValueError):
        return None, "local reranker model artifact is missing or cannot be hashed"
    if artifact_sha256 != factory.artifact_sha256:
        return None, "local reranker model artifact SHA differs from its declared SHA"
    try:
        probe_budget = BudgetLedger(
            Budget(deadline_utc=datetime.now(UTC) + timedelta(seconds=30))
        )
        instance = factory.build(probe_budget)
        _validate_reranker_instance(instance, factory)
    except Exception as exc:  # noqa: BLE001 - preflight reports the failure class only.
        return None, f"local reranker construction failed ({type(exc).__name__})"
    return instance, "local reranker implementation and model artifact verified"


def _validate_dev_only_text_reranker(
    instance: object,
    factory: DevOnlyTextRerankerFactory,
) -> None:
    if type(instance) is not DevOnlyTextReranker:
        raise TypeError("Dev-only text route requires the deterministic local text implementation")
    if not isinstance(instance, Reranker):
        raise TypeError("Dev-only text reranker does not implement the public Reranker port")
    if (
        instance.b07_dev_only_config != factory.config
        or instance.b07_dev_only_config_sha256 != factory.config_sha256
        or instance.budget is None
    ):
        raise ValueError("Dev-only text reranker implementation/config identity differs")


def _reranker_factory_identity(
    factory: LocalDevRerankerFactory | DevOnlyTextRerankerFactory,
) -> str:
    base = {
        "implementation": _implementation_descriptor(factory.build),
        "config": _safe_config(factory.config),
    }
    if isinstance(factory, DevOnlyTextRerankerFactory):
        base.update(
            {
                "dev_only": factory.dev_only,
                "formal_eligible": factory.formal_eligible,
                "config_sha256": factory.config_sha256,
            }
        )
    else:
        base.update(
            {
                "model_id": factory.model_id,
                "model_sha256": factory.artifact_sha256,
            }
        )
    return _canonical_sha256(base)


def _reranker_descriptor(
    instance: object,
    factory: LocalDevRerankerFactory | DevOnlyTextRerankerFactory,
) -> dict[str, str]:
    if isinstance(factory, DevOnlyTextRerankerFactory):
        return _implementation_descriptor(DevOnlyTextReranker)
    return _implementation_descriptor(instance)


def _new_probe_ledger() -> BudgetLedger:
    return BudgetLedger(Budget(deadline_utc=datetime.now(UTC) + timedelta(seconds=30)))


def _make_dev_evidence_verifier(
    generate: Callable[[LLMRequest], Awaitable[LLMResult]],
) -> EvidenceVerifier:
    async def generate_with_dev_output_cap(request: LLMRequest) -> LLMResult:
        bounded = request.model_copy(
            update={
                "max_output_tokens": max(
                    request.max_output_tokens,
                    _DEV_VERIFIER_MAX_OUTPUT_TOKENS,
                )
            }
        )
        return await generate(bounded)

    return StructuredEvidenceVerifier(
        generate_with_dev_output_cap,
        timeout_seconds=_DEV_VERIFIER_TIMEOUT_SECONDS,
    )


def _probe_channels(
    factory: LocalDevChannelFactory | None,
    ledger: BudgetLedger,
) -> tuple[dict[str, Retriever] | None, str | None]:
    if factory is None:
        return None, "per-investigation local B04 channel factory is missing"
    try:
        channels = factory.build(ledger)
    except Exception as exc:  # noqa: BLE001 - preflight retains only the failure class.
        return None, f"local B04 channel construction failed ({type(exc).__name__})"
    if not isinstance(channels, Mapping):
        return None, "local B04 channel factory did not return a mapping"
    return dict(channels), None


def _validate_channels(
    channels: Mapping[str, object],
    knowledge_store: object,
    ledger: BudgetLedger,
    services: Mapping[str, tuple[str, int]],
) -> str | None:
    if set(channels) != set(_CHANNEL_TYPES):
        return "requires exactly BM25, Dense, Symbol, and Entity channel adapters"
    for name, expected_type in _CHANNEL_TYPES.items():
        channel = channels[name]
        if not isinstance(channel, expected_type) or not isinstance(channel, Retriever):
            return f"{name} does not use the required B04 retriever adapter"
        if getattr(channel, "store", None) is not knowledge_store:
            return f"{name} does not share the supplied PostgresKnowledgeStore"
        index = getattr(channel, "index", None)
        if not isinstance(index, _CHANNEL_INDEX_TYPES[name]):
            return f"{name} index does not use the required local B04 implementation"

    dense_channel = channels["dense"]
    if not isinstance(dense_channel, DenseRetriever):
        return "dense does not use the required DenseRetriever adapter"
    dense_index = dense_channel.index
    if not isinstance(dense_index, QdrantDenseIndex):
        return "DenseRetriever does not use the required QdrantDenseIndex"
    provider = dense_index.provider
    if not isinstance(provider, LocalQwenEmbeddingProvider):
        return "Dense must use LocalQwenEmbeddingProvider; cloud/fake embedding is not accepted"
    if provider.budget is not ledger:
        return "Dense embedding provider must use the current per-investigation budget ledger"
    profile = provider.profile
    if not isinstance(profile, LocalQwenProfile) or profile.local_api_key is not None:
        return "Dense requires a no-key LocalQwenProfile"
    embedding_endpoint = urlsplit(profile.endpoint_url)
    if not _matches_loopback_service(
        embedding_endpoint.hostname,
        embedding_endpoint.port,
        services.get("embedding"),
    ):
        return "Dense embedding endpoint differs from the checked loopback service"

    qdrant_host, qdrant_port = _qdrant_local_endpoint(dense_index)
    if not _matches_loopback_service(qdrant_host, qdrant_port, services.get("qdrant")):
        return "Dense Qdrant client differs from the checked loopback service"
    return None


def _is_local_knowledge_store(
    knowledge_store: object | None,
    services: Mapping[str, tuple[str, int]],
) -> bool:
    if not isinstance(knowledge_store, KnowledgeStore) or not isinstance(
        knowledge_store, PostgresKnowledgeStore
    ):
        return False
    database = getattr(knowledge_store, "database", None)
    engine = getattr(database, "engine", None)
    url = getattr(engine, "url", None)
    return _matches_loopback_service(
        getattr(url, "host", None),
        getattr(url, "port", None) or 5432,
        services.get("postgres"),
    )


def _is_local_graph_retriever(
    graph_retriever: object,
    knowledge_store: object | None,
    services: Mapping[str, tuple[str, int]],
) -> bool:
    if not isinstance(graph_retriever, GraphRetriever):
        return False
    graph_store = graph_retriever.graph_store
    return (
        isinstance(graph_store, PostgresGraphStore)
        and graph_store.database is getattr(knowledge_store, "database", None)
        and graph_store.knowledge_store is knowledge_store
        and isinstance(graph_retriever.entity_index, EntityIndex)
        and _matches_loopback_service(
            getattr(getattr(graph_store.database.engine, "url", None), "host", None),
            getattr(getattr(graph_store.database.engine, "url", None), "port", None) or 5432,
            services.get("postgres"),
        )
    )


def _qdrant_local_endpoint(index: QdrantDenseIndex) -> tuple[str | None, int | None]:
    remote = getattr(index.client, "_client", None)
    return getattr(remote, "_host", None), getattr(remote, "_port", None)


def _matches_loopback_service(
    host: object,
    port: object,
    expected: tuple[str, int] | None,
) -> bool:
    if expected is None or not isinstance(host, str) or type(port) is not int:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback and (host, port) == expected


def _validate_reranker_instance(
    instance: object,
    factory: LocalDevRerankerFactory,
) -> None:
    if (
        isinstance(instance, CloudReranker)
        or type(instance).__module__ == "knowledge_weaver.providers.fake"
        or isinstance(instance, FakeProvider)
    ):
        raise TypeError("cloud and fake rerankers are not valid for this local Dev route")
    if not isinstance(instance, Reranker):
        raise TypeError("local reranker does not implement the public Reranker port")
    local_config = getattr(instance, "b07_dev_local_config", None)
    if not isinstance(local_config, Mapping):
        raise TypeError("local reranker must expose b07_dev_local_config")
    if (
        local_config.get("model_id") != factory.model_id
        or local_config.get("model_sha256") != factory.artifact_sha256
    ):
        raise ValueError("local reranker model identity differs from its verified artifact")
    endpoint = local_config.get("endpoint_url")
    if endpoint is not None:
        parsed = urlsplit(str(endpoint))
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
            raise ValueError("local reranker endpoint must be an explicit loopback HTTP URL")


def _probe_endpoint(component: str, host: str, port: int) -> FixedAgentDevCheck:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return FixedAgentDevCheck(component, False, "only a literal loopback address is allowed")
    if not address.is_loopback or not 1 <= port <= 65535:
        return FixedAgentDevCheck(component, False, "endpoint is not a valid loopback TCP address")
    try:
        with socket.create_connection((host, port), timeout=0.25):
            pass
    except OSError:
        return FixedAgentDevCheck(component, False, f"loopback service is not listening at {host}:{port}")
    return FixedAgentDevCheck(component, True, f"loopback TCP service is listening at {host}:{port}")


def _component_manifest(
    *,
    knowledge_store: object,
    channels: Mapping[str, object],
    channel_factory: LocalDevChannelFactory,
    graph_retriever: object,
    reranker: object,
    reranker_factory: LocalDevRerankerFactory | DevOnlyTextRerankerFactory | None,
    generation_profile: DevLocalOpenAIChatProfile,
    local_services: Mapping[str, tuple[str, int]],
) -> dict[str, Any]:
    if reranker_factory is None:
        raise ValueError("local reranker factory is required for a component manifest")
    if isinstance(reranker_factory, DevOnlyTextRerankerFactory):
        reranker_manifest: dict[str, Any] = {
            "implementation": _implementation_descriptor(DevOnlyTextReranker),
            "factory": _implementation_descriptor(reranker_factory.build),
            "config": _safe_config(reranker_factory.config),
            "config_sha256": reranker_factory.config_sha256,
            "dev_only": True,
            "formal_eligible": False,
            "execution_kind": "deterministic-local-text-computation",
        }
    else:
        reranker_manifest = {
            "implementation": _implementation_descriptor(reranker),
            "factory": _implementation_descriptor(reranker_factory.build),
            "model_id": reranker_factory.model_id,
            "model_sha256": reranker_factory.artifact_sha256,
            "config": _safe_config(reranker_factory.config),
            "dev_only": True,
            "formal_eligible": False,
            "execution_kind": "local-model-artifact",
        }
    return {
        "schema_version": 1,
        "route_id": "agent.fixed",
        "frozen_route_config_sha256": agent_behavior_profile("agent.fixed").config_sha256,
        "knowledge_store": _implementation_descriptor(knowledge_store),
        "hybrid_rrf": {
            "implementation": _implementation_descriptor(HybridRetriever),
            "fusion": "rrf",
            "reranker": None,
            "factory": _implementation_descriptor(channel_factory.build),
            "factory_config": _safe_config(channel_factory.config),
            "channels": {
                name: _implementation_descriptor(channels[name])
                for name in sorted(channels)
            },
        },
        "graph": _implementation_descriptor(graph_retriever),
        "separate_reranker": reranker_manifest,
        "generation": {
            "implementation": _implementation_descriptor(
                DevLocalOpenAICompatibleGenerationProvider
            ),
            "profile": _safe_config(generation_profile),
        },
        "evidence_verifier": {
            "factory": _implementation_descriptor(_make_dev_evidence_verifier),
            "implementation": _implementation_descriptor(StructuredEvidenceVerifier),
            "timeout_seconds": _DEV_VERIFIER_TIMEOUT_SECONDS,
            "max_output_tokens": _DEV_VERIFIER_MAX_OUTPUT_TOKENS,
        },
        "loopback_services": {
            name: {"host": host, "port": port}
            for name, (host, port) in sorted(local_services.items())
        },
    }


def _implementation_descriptor(value: object) -> dict[str, str]:
    component_type = value if inspect.isclass(value) else type(value)
    implementation = f"{component_type.__module__}.{component_type.__qualname__}"
    try:
        if isinstance(value, ModuleType):
            source = inspect.getsource(value)
        elif isinstance(value, type):
            source = inspect.getsource(value)
        elif isinstance(value, (MethodType, FunctionType, TracebackType, FrameType, CodeType)):
            source = inspect.getsource(value)
        else:
            raise TypeError("component instance source is resolved from its source file")
    except (OSError, TypeError):
        try:
            source_file = inspect.getsourcefile(component_type)
            source = Path(source_file).read_text(encoding="utf-8") if source_file else implementation
        except (OSError, TypeError):
            source = implementation
    return {
        "implementation": implementation,
        "implementation_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "config_sha256": _canonical_sha256(_safe_config(value)),
    }


def _safe_config(value: object, *, _depth: int = 0) -> Any:
    if _depth > 4:
        return {"type": _qualified_type(value)}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        return {
            str(key): _safe_config(item, _depth=_depth + 1)
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
            if not _SECRET_FIELD.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_safe_config(item, _depth=_depth + 1) for item in value]
    if is_dataclass(value) and not inspect.isclass(value):
        return {
            item.name: _safe_config(getattr(value, item.name), _depth=_depth + 1)
            for item in fields(value)
            if not _SECRET_FIELD.search(item.name)
        }
    if inspect.isclass(value) or callable(value):
        return {"callable": _qualified_type(value)}
    public_values = getattr(value, "__dict__", None)
    if isinstance(public_values, dict):
        result: dict[str, Any] = {}
        for key, item in sorted(public_values.items()):
            if key.startswith("_") or key in _SKIP_CONFIG_FIELDS or _SECRET_FIELD.search(key):
                continue
            if callable(item):
                continue
            if (
                isinstance(item, (str, bool, int, float, Enum, Path, tuple, list, dict, Mapping))
                or is_dataclass(item)
                or hasattr(item, "__dict__")
            ):
                result[key] = _safe_config(item, _depth=_depth + 1)
        return {"type": _qualified_type(value), "config": result}
    return {"type": _qualified_type(value)}


def _artifact_sha256(path: Path) -> str:
    resolved = path.expanduser().resolve(strict=True)
    if resolved.is_file():
        digest = hashlib.sha256()
        with resolved.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    if not resolved.is_dir():
        raise ValueError("model artifact must be a file or directory")
    entries = [item for item in resolved.rglob("*") if item.is_file() and not item.is_symlink()]
    if not entries:
        raise ValueError("model artifact directory is empty")
    digest = hashlib.sha256()
    for item in sorted(entries, key=lambda row: row.relative_to(resolved).as_posix()):
        relative = item.relative_to(resolved).as_posix()
        file_hash = _artifact_sha256(item)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _qualified_type(value: object) -> str:
    component_type = value if inspect.isclass(value) else type(value)
    return f"{component_type.__module__}.{component_type.__qualname__}"
