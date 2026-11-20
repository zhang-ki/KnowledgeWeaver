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

    async def generate(self, request: LLMRequest) -> LLMResult:
        context = json.loads(request.untrusted_context)
        if "Assess only whether" in request.system_instruction:
            payload = {
                "rows": [
                    {
                        "facet_id": pair["facet_id"],
                        "evidence_id": pair["evidence_id"],
                        "support": 1,
                        "conflict": False,
                        "reason_code": "supports",
                    }
                    for pair in context["evaluate_pairs"]
                ]
            }
        else:
            evidence_ids = [item["id"] for item in context["evidence"]]
            path_evidence_ids = [
                evidence_id
                for path in context["paths"]
                for step in path["steps"]
                for evidence_id in step["evidence_ids"]
            ]
            relation_ids = [
                step["relation_id"] for path in context["paths"] for step in path["steps"]
            ]
            payload = {
                "status": "answered",
                "claims": [
                    {
                        "text": "The retrieved source contains the requested call relationship.",
                        "evidence_ids": path_evidence_ids[:1] or evidence_ids[:1],
                        "relation_ids": relation_ids[:1],
                    }
                ],
                "unanswered": [],
            }
        return LLMResult(
            parsed_json=payload,
            usage=ModelTokenUsage(input_tokens=16, output_tokens=12, count_kind=CountKind.exact),
            model="deterministic-integration-fixture",
            revision="schema-output-v1",
        )


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


@pytest.mark.asyncio
@pytest.mark.service
async def test_agent_service_uses_real_pg_qdrant_qwen_and_b04_b05_assembly() -> None:
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
        max_tool_calls=16,
        max_llm_calls=12,
        max_total_tokens=50_000,
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
            "def normalize(value):\n"
            "    return value.strip()\n\n"
            "def evidence():\n"
            "    return normalize(' synthetic evidence ')\n",
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
            assert report.counts.get("entities", 0) >= 2
            assert report.counts.get("relations", 0) >= 1
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
            query_text = "How does evidence() call normalize()?"
            query = Query(
                id=str(uuid4()),
                text=query_text,
                context=context,
                subquestions=[
                    SubQuestion(
                        id=str(uuid4()),
                        text=query_text,
                        required_source_types=[SourceKind.python_repo],
                    )
                ],
            )

            def make_retrievers(ledger: BudgetLedger):
                pipeline.dense.provider = LocalQwenEmbeddingProvider(profile, ledger)
                return {
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

            agent = AgentServiceImpl(
                pipeline.knowledge_store,
                make_retrievers,
                _DeterministicIntegrationProvider(),
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
                        policy=PolicyKind.rule,
                        features={
                            "enable_expansion": False,
                            "enable_reranker": False,
                            "enable_sufficiency_stop": False,
                        },
                    ),
                ),
            )

            trace = agent.get_trace(answer.investigation_id)
            assert any(
                (entry.get("selected_action") or {}).get("tool") == "graph"
                for entry in trace
            )
            assert any(
                (entry.get("selected_action") or {}).get("tool") == "dense"
                for entry in trace
            )
            assert answer.status.value == "answered"
            assert answer.claims and answer.claims[0].relation_ids
            assert answer.usage.tool_calls > 0
            assert answer.usage.llm_calls > 0
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
