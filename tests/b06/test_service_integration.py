from __future__ import annotations

import json
import os
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from knowledge_weaver.agent import AgentServiceImpl
from knowledge_weaver.agent.verifier import StructuredEvidenceVerifier
from knowledge_weaver.core.budget import BudgetLedger
from knowledge_weaver.extraction import KnowledgeExtractor
from knowledge_weaver.graph import GraphRetriever
from knowledge_weaver.models import (
    Budget,
    CountKind,
    IngestRequest,
    InvestigationRequest,
    LLMRequest,
    LLMResult,
    ModelTokenUsage,
    PolicyKind,
    Query,
    RetrievalQuery,
    Source,
    SourceKind,
    SubQuestion,
)
from knowledge_weaver.providers import LocalQwenEmbeddingProvider, LocalQwenProfile
from knowledge_weaver.retrieval import (
    Bm25Retriever,
    DenseRetriever,
    EntityRetriever,
    SymbolRetriever,
)
from knowledge_weaver.storage import Database, PipelineService
from knowledge_weaver.storage.schema import (
    CorpusRow,
    EvidenceRow,
    GenerationRow,
    ProvenanceRow,
    RelationRow,
    SourceRow,
)


async def _service_call(label: str, operation: Awaitable[Any]) -> Any:
    try:
        return await operation
    except Exception as exc:  # noqa: BLE001 - hide provider/client exception details from test output.
        pytest.fail(f"{label} failed ({type(exc).__name__}); inspect local service logs securely", pytrace=False)


class _DeterministicIntegrationProvider:
    """Only supplies schema-valid model outputs; storage and retrieval remain real."""

    def __init__(
        self,
        *,
        scenario: str,
        partial_facet_id: str | None = None,
        answerable: bool,
    ) -> None:
        self.scenario = scenario
        self.partial_facet_id = partial_facet_id
        self.answerable = answerable
        self.route_selections: list[dict[str, Any]] = []
        self.route_errors: list[str] = []
        self.multihop_supported_facets: set[str] = set()
        self.multihop_supported_pairs: set[tuple[str, str]] = set()

    async def generate(self, request: LLMRequest) -> LLMResult:
        context = json.loads(request.untrusted_context)
        if "Choose exactly one offered action_id" in request.system_instruction:
            candidates = context["candidates"]
            if self.scenario == "no_entity_seed":
                already_selected_entity = any(
                    item["tool"] == "entity" for item in self.route_selections
                )
                preferred_tool = "dense" if already_selected_entity else "entity"
                preferred_hops = None
            else:
                graph_candidates = [
                    item
                    for item in candidates
                    if item["tool"] == "graph" and item["hops"] == 2
                ]
                already_selected_dense = any(
                    item["tool"] == "dense" for item in self.route_selections
                )
                if graph_candidates:
                    preferred_tool = "graph"
                    preferred_hops = 2
                elif not already_selected_dense:
                    preferred_tool = "dense"
                    preferred_hops = None
                else:
                    preferred_tool = "entity"
                    preferred_hops = None

            selected = next(
                (
                    item
                    for item in candidates
                    if item["tool"] == preferred_tool
                    and (preferred_hops is None or item["hops"] == preferred_hops)
                ),
                None,
            )
            if selected is None:
                self.route_errors.append(f"no {preferred_tool} candidate was available")
                selected = candidates[0]
            selection = {
                "tool": selected["tool"],
                "hops": selected["hops"],
                "facet_id": selected["facet_id"],
            }
            self.route_selections.append(selection)
            payload = {"action_id": selected["action_id"], "reason_code": "facet_gap"}
        elif "Assess only whether" in request.system_instruction:
            multihop_paths = [
                path for path in context["paths"] if len(path["steps"]) >= 2
            ]
            multihop_evidence_ids = {
                evidence_id
                for path in multihop_paths
                for step in path["steps"]
                for evidence_id in step["evidence_ids"]
            }
            self.multihop_supported_pairs.update(
                (pair["facet_id"], pair["evidence_id"])
                for pair in context["evaluate_pairs"]
                if pair["evidence_id"] in multihop_evidence_ids
            )
            self.multihop_supported_facets.update(
                facet_id for facet_id, _ in self.multihop_supported_pairs
            )
            payload = {
                "rows": [
                    {
                        "facet_id": pair["facet_id"],
                        "evidence_id": pair["evidence_id"],
                        "support": (
                            1
                            if pair["evidence_id"] in multihop_evidence_ids
                            or pair["facet_id"] == self.partial_facet_id
                            else 0
                        ),
                        "conflict": False,
                        "reason_code": (
                            "supports"
                            if pair["evidence_id"] in multihop_evidence_ids
                            or pair["facet_id"] == self.partial_facet_id
                            else "unsupported"
                        ),
                    }
                    for pair in context["evaluate_pairs"]
                ]
            }
        else:
            evidence_ids = [item["id"] for item in context["evidence"]]
            multihop_paths = [
                path for path in context["paths"] if len(path["steps"]) >= 2
            ]
            path = max(multihop_paths, key=lambda item: len(item["steps"])) if multihop_paths else None
            path_evidence_ids = (
                list(
                    dict.fromkeys(
                        evidence_id
                        for step in path["steps"]
                        for evidence_id in step["evidence_ids"]
                    )
                )
                if path is not None
                else []
            )
            relation_ids = (
                [step["relation_id"] for step in path["steps"]]
                if path is not None
                else []
            )
            claim_evidence_ids = path_evidence_ids or evidence_ids[:1]
            facets = context.get("facets", [])
            if not self.answerable:
                payload = {
                    "status": "insufficient",
                    "claims": [],
                    "unanswered": [item["text"] for item in facets],
                }
            else:
                payload = {
                    "status": "answered" if claim_evidence_ids else "insufficient",
                    "claims": (
                        [
                            {
                                "text": "The synthetic call chain reaches the normalization function.",
                                "evidence_ids": claim_evidence_ids,
                                "relation_ids": relation_ids,
                            }
                        ]
                        if claim_evidence_ids
                        else []
                    ),
                    "unanswered": [],
                }
        return LLMResult(
            parsed_json=payload,
            usage=ModelTokenUsage(input_tokens=16, output_tokens=12, count_kind=CountKind.exact),
            model="deterministic-integration-fixture",
            revision="schema-output-v1",
        )


class _RecordingRetriever:
    """Capture outputs while delegating every retrieval to the real service."""

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.index = getattr(delegate, "index", None)
        self.provider = getattr(delegate, "provider", None)
        self.results: list[Any] = []

    async def retrieve(self, query: Any) -> Any:
        result = await self.delegate.retrieve(query)
        self.results.append(result)
        return result


async def _delete_owned_provenance(database: Database, source_ids: list[str]) -> None:
    if not source_ids:
        return
    async with database.session() as session:
        provenance_ids = list(
            (
                await session.scalars(
                    select(ProvenanceRow.id).where(ProvenanceRow.source_id.in_(source_ids))
                )
            ).all()
        )
        if provenance_ids:
            await session.execute(
                delete(RelationRow).where(RelationRow.provenance_id.in_(provenance_ids))
            )
            await session.execute(
                delete(EvidenceRow).where(EvidenceRow.provenance_id.in_(provenance_ids))
            )
            await session.execute(
                delete(ProvenanceRow).where(ProvenanceRow.id.in_(provenance_ids))
            )
        await session.commit()


async def _run_real_service_scenario(scenario: str) -> None:
    required = (
        "KW_DATABASE_URL",
        "KW_QDRANT_URL",
        "KW_QDRANT_API_KEY",
        "KW_EMBEDDING_BASE_URL",
        "KW_EMBEDDING_MODEL",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.skip(
            "Orchestrator must inject real PG/Qdrant and loopback Qwen settings; missing: "
            + ", ".join(missing)
        )

    try:
        profile = LocalQwenProfile.from_environment()
        database = Database(os.environ["KW_DATABASE_URL"])
    except Exception as exc:  # noqa: BLE001 - configuration errors can contain endpoint details.
        pytest.fail(f"local service profile setup failed ({type(exc).__name__})", pytrace=False)
    corpus_id, source_id, request_id = str(uuid4()), str(uuid4()), str(uuid4())
    service_budget = Budget(
        deadline_utc=datetime.now(UTC) + timedelta(minutes=10),
        max_steps=6,
        max_tool_calls=12,
        max_llm_calls=8,
        max_total_tokens=12_000,
        max_graph_edges=200,
    )
    ingest_budget = BudgetLedger(
        Budget(
            deadline_utc=datetime.now(UTC) + timedelta(minutes=10),
            max_tool_calls=20,
            max_llm_calls=20,
            max_total_tokens=100_000,
        )
    )

    with TemporaryDirectory(prefix="kwb06-agent-service-", dir=Path.cwd()) as temporary:
        root = Path(temporary)
        source_root = root / "source"
        source_root.mkdir()
        (source_root / "service_fixture.py").write_text(
            "def normalizevalue(value):\n"
            "    return value.strip()\n\n"
            "def dispatchstep(value):\n"
            "    return normalizevalue(value)\n\n"
            "def entrypoint(value):\n"
            "    return dispatchstep(value)\n",
            encoding="utf-8",
        )
        pipeline = PipelineService(
            database,
            data_dir=root / "data",
            allowed_roots=[root],
            qdrant_url=os.environ["KW_QDRANT_URL"],
            qdrant_api_key=os.environ["KW_QDRANT_API_KEY"],
            embedding_provider=LocalQwenEmbeddingProvider(profile, ingest_budget),
            extractor=KnowledgeExtractor(corpus_id=corpus_id),
        )
        assert pipeline.dense is not None
        collection = pipeline.dense.collection(corpus_id)
        collection_owned = False
        context = None
        recorders: dict[str, _RecordingRetriever] = {}
        try:
            if await _service_call(
                "Qdrant collection preflight",
                pipeline.dense.client.collection_exists(collection),
            ):
                pytest.fail("unique synthetic Qdrant collection already exists; refusing to alter it")
            collection_owned = True
            report = await _service_call(
                "synthetic ingest",
                pipeline.ingest(
                    IngestRequest(
                        corpus_id=corpus_id,
                        source_spec=Source(
                            id=source_id,
                            corpus_id=corpus_id,
                            kind=SourceKind.python_repo,
                            logical_uri=str(source_root),
                            display_name="B06 synthetic service integration",
                            created_at=datetime.now(UTC),
                        ),
                        idempotency_key=f"b06-agent-service-{uuid4()}",
                    ),
                )
            )
            assert report.generation_id is not None
            assert report.counts.get("entities", 0) >= 3
            assert report.counts.get("relations", 0) >= 2
            context = await _service_call(
                "query context resolution",
                pipeline.knowledge_store.resolve_context(corpus_id, request_id),
            )
            context = context.model_copy(
                update={
                    "deadline_utc": datetime.now(UTC) + timedelta(minutes=5),
                    "source_ids": [source_id],
                }
            )
            if scenario == "seeded_graph":
                direct_facet = SubQuestion(
                    id=str(uuid4()),
                    text="What function does entrypoint call directly?",
                    required_source_types=[SourceKind.python_repo],
                )
                path_facet = SubQuestion(
                    id=str(uuid4()),
                    text="entrypoint",
                    required_source_types=[SourceKind.python_repo],
                )
                facets = [direct_facet, path_facet]
                query_text = (
                    "How does entrypoint reach normalizevalue through dispatchstep?"
                )
                integration_provider = _DeterministicIntegrationProvider(
                    scenario=scenario,
                    partial_facet_id=direct_facet.id,
                    answerable=True,
                )
            elif scenario == "no_entity_seed":
                query_text = (
                    "How does an aurora ledger reach a missing prism through a phantom relay?"
                )
                facets = [
                    SubQuestion(
                        id=str(uuid4()),
                        text=query_text,
                        required_source_types=[SourceKind.python_repo],
                    )
                ]
                integration_provider = _DeterministicIntegrationProvider(
                    scenario=scenario,
                    answerable=False,
                )
            else:
                raise AssertionError(f"unknown service scenario: {scenario}")

            query = Query(id=str(uuid4()), text=query_text, context=context, subquestions=facets)

            if scenario == "seeded_graph":
                seed_probe = await _service_call(
                    "persisted Entity seed preflight",
                    EntityRetriever(
                        pipeline.entity, pipeline.knowledge_store
                    ).retrieve(
                        RetrievalQuery(
                            text="entrypoint",
                            context=context,
                            top_k=5,
                        )
                    ),
                )
                assert seed_probe.hits

            def make_retrievers(ledger: BudgetLedger):
                pipeline.dense.provider = LocalQwenEmbeddingProvider(profile, ledger)
                retrievers = {
                    "bm25": Bm25Retriever(pipeline.bm25, pipeline.knowledge_store),
                    "dense": DenseRetriever(pipeline.dense, pipeline.knowledge_store),
                    "symbol": SymbolRetriever(pipeline.symbol, pipeline.knowledge_store),
                    "entity": EntityRetriever(pipeline.entity, pipeline.knowledge_store),
                    "graph": GraphRetriever(
                        pipeline.graph_store,
                        pipeline.entity,
                        pipeline.knowledge_store,
                    ),
                }
                for name in ("dense", "entity", "graph"):
                    recorders[name] = _RecordingRetriever(retrievers[name])
                    retrievers[name] = recorders[name]
                return retrievers

            agent = AgentServiceImpl(
                pipeline.knowledge_store,
                make_retrievers,
                integration_provider,
                verifier_factory=lambda generate: StructuredEvidenceVerifier(generate),
            )
            answer = await _service_call(
                "B06 investigation",
                agent.investigate(
                    InvestigationRequest(
                        query=query,
                        context=context,
                        budget=service_budget.model_copy(
                            update={"deadline_utc": context.deadline_utc}
                        ),
                        policy=PolicyKind.llm,
                        features={
                            "enable_expansion": False,
                            "enable_reranker": False,
                            "enable_sufficiency_stop": True,
                            "enable_dynamic_stop": True,
                        },
                    ),
                ),
            )

            trace = agent.get_trace(answer.investigation_id)
            assert answer.usage.tool_calls <= service_budget.max_tool_calls
            assert answer.usage.llm_calls <= service_budget.max_llm_calls
            assert answer.usage.total_tokens <= service_budget.max_total_tokens
            assert answer.usage.tool_calls > 0
            assert answer.usage.llm_calls > 0

            if scenario == "no_entity_seed":
                assert recorders["entity"].results
                assert all(not result.hits for result in recorders["entity"].results)
                assert any(result.hits for result in recorders["dense"].results)
                assert not recorders["graph"].results
                assert all(
                    (entry.get("selected_action") or {}).get("tool") != "graph"
                    for entry in trace
                )
                assert trace[-1]["stop_reason"] == "repeated_no_gain"
                assert answer.status.value == "insufficient"
                assert not answer.claims
                assert not integration_provider.route_errors
            else:
                assert any(result.hits for result in recorders["dense"].results)
                assert any(result.hits for result in recorders["entity"].results)
                graph_entries = [
                    entry
                    for entry in trace
                    if (entry.get("selected_action") or {}).get("tool") == "graph"
                ]
                assert graph_entries
                assert any(
                    entry["selected_action"].get("hops") == 2 for entry in graph_entries
                )
                assert recorders["graph"].results
                graph_paths = [
                    path
                    for result in recorders["graph"].results
                    for path in result.paths
                ]
                multihop_paths = [path for path in graph_paths if len(path.steps) >= 2]
                assert multihop_paths, [len(path.steps) for path in graph_paths]
                snapshot = await _service_call(
                    "persisted graph snapshot verification",
                    pipeline.graph_store.snapshot(context),
                )
                relation_by_id = {relation.id: relation for relation in snapshot.relations}
                evidence_ids = {evidence.id for evidence in snapshot.evidence}

                claim_evidence_ids = {
                    evidence_id
                    for claim in answer.claims
                    for evidence_id in claim.evidence_ids
                }
                claim_relation_ids = {
                    relation_id
                    for claim in answer.claims
                    for relation_id in claim.relation_ids
                }
                closed_path = None
                for path in sorted(
                    multihop_paths,
                    key=lambda item: len(item.steps),
                    reverse=True,
                ):
                    path_relation_ids = {step.relation_id for step in path.steps}
                    path_evidence_ids = {
                        evidence_id
                        for step in path.steps
                        for evidence_id in step.evidence_ids
                    }
                    if (
                        path_relation_ids <= claim_relation_ids
                        and path_evidence_ids <= claim_evidence_ids
                        and all(
                            (path_facet.id, evidence_id)
                            in integration_provider.multihop_supported_pairs
                            for evidence_id in path_evidence_ids
                        )
                    ):
                        closed_path = path
                        break
                assert closed_path is not None
                assert path_facet.id in integration_provider.multihop_supported_facets
                assert len(closed_path.entity_ids) == len(closed_path.steps) + 1
                assert all(
                    left.to_id == right.from_id
                    for left, right in zip(closed_path.steps, closed_path.steps[1:])
                )
                for step in closed_path.steps:
                    assert step.relation_id in relation_by_id
                    assert step.evidence_ids
                    assert set(step.evidence_ids) <= evidence_ids
                    assert set(step.evidence_ids) <= set(
                        relation_by_id[step.relation_id].evidence_ids
                    )
                citation_check = await _service_call(
                    "answer citation validation",
                    pipeline.knowledge_store.validate_citations(
                        sorted(claim_evidence_ids), context
                    ),
                )
                assert not citation_check.invalid
                assert set(citation_check.valid_ids) == claim_evidence_ids
                assert answer.status.value == "answered", {
                    "unanswered": answer.unanswered,
                    "routes": integration_provider.route_selections,
                    "trace": [
                        {
                            "tool": (entry.get("selected_action") or {}).get("tool"),
                            "hops": (entry.get("selected_action") or {}).get("hops"),
                            "stop_reason": entry.get("stop_reason"),
                            "observation": entry.get("observation"),
                        }
                        for entry in trace
                    ],
                }
                assert not integration_provider.route_errors
        finally:
            try:
                try:
                    if context is not None:
                        await _service_call(
                            "query lease release",
                            pipeline.knowledge_store.release_lease(request_id),
                        )
                    async with database.session() as session:
                        generations = list(
                            (
                                await session.scalars(
                                    select(GenerationRow.id).where(
                                        GenerationRow.corpus_id == corpus_id
                                    )
                                )
                            ).all()
                        )
                        for generation_id in generations:
                            await _service_call(
                                "owned generation deletion",
                                pipeline.dense.delete_generation(corpus_id, generation_id),
                            )
                        source_ids = list(
                            (
                                await session.scalars(
                                    select(SourceRow.id).where(SourceRow.corpus_id == corpus_id)
                                )
                            ).all()
                        )
                        await session.rollback()
                    await _delete_owned_provenance(database, source_ids)
                    async with database.session() as session:
                        corpus = await session.get(CorpusRow, corpus_id)
                        if corpus is not None:
                            await session.delete(corpus)
                        await session.commit()
                except Exception as exc:  # noqa: BLE001 - keep service credentials out of failures.
                    pytest.fail(
                        f"owned database resource cleanup failed ({type(exc).__name__})",
                        pytrace=False,
                    )
            finally:
                try:
                    if collection_owned and await _service_call(
                        "owned Qdrant collection check",
                        pipeline.dense.client.collection_exists(collection),
                    ):
                        await _service_call(
                            "owned Qdrant collection deletion",
                            pipeline.dense.client.delete_collection(collection),
                        )
                finally:
                    try:
                        await _service_call("Qdrant client close", pipeline.dense.close())
                    finally:
                        await _service_call("PostgreSQL engine dispose", database.dispose())


@pytest.mark.asyncio
@pytest.mark.service
async def test_agent_service_without_entity_seed_stops_after_repeated_no_gain() -> None:
    await _run_real_service_scenario("no_entity_seed")


@pytest.mark.asyncio
@pytest.mark.service
async def test_agent_service_with_entity_seed_retrieves_two_hop_graph_evidence() -> None:
    await _run_real_service_scenario("seeded_graph")
