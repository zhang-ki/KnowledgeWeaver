from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from knowledge_weaver.indexing.bm25 import Bm25Index
from knowledge_weaver.indexing.dense import QdrantDenseIndex
from knowledge_weaver.models import IngestRequest, Source, SourceKind
from knowledge_weaver.providers.fake import FakeEmbeddingProvider
from knowledge_weaver.retrieval import Bm25Retriever, DenseRetriever, HybridRetriever
from knowledge_weaver.storage import Database, PipelineService
from knowledge_weaver.storage.schema import (
    CorpusRow,
    EvidenceRow,
    GenerationRow,
    ProvenanceRow,
    RelationRow,
    SourceRow,
)

pytestmark = pytest.mark.service


class _ConfiguredFakeEmbeddingProvider:
    """Offline query vectors for the live PostgreSQL/Qdrant integration check."""

    def __init__(self, dimension: int) -> None:
        self.provider = FakeEmbeddingProvider(dimension)
        self.configuration_fingerprint = f"fake-hash-config-{dimension}-l2"

    async def embed(self, texts, mode):
        batch = await self.provider.embed(texts, mode)
        return batch.model_copy(
            update={"configuration_fingerprint": self.configuration_fingerprint}
        )


async def _delete_provenance_dependents(database: Database, source_ids: list[str]) -> None:
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
async def test_hybrid_retrieval_hydrates_evidence_from_real_pg_and_qdrant() -> None:
    database_url = os.environ.get("KW_DATABASE_URL")
    qdrant_url = os.environ.get("KW_QDRANT_URL")
    qdrant_api_key = os.environ.get("KW_QDRANT_API_KEY")
    if not database_url or not qdrant_url or not qdrant_api_key:
        pytest.skip("Orchestrator did not provide real PostgreSQL/Qdrant test configuration")

    corpus_id, source_id = str(uuid4()), str(uuid4())
    request_id = str(uuid4())
    database = Database(database_url)
    with TemporaryDirectory(prefix="kwb04-live-", dir=Path.cwd()) as directory:
        root = Path(directory)
        source_root = root / "source"
        source_root.mkdir()
        (source_root / "retrieval_example.py").write_text(
            "def retrieve_hybrid_evidence():\n"
            "    return 'retrieval evidence from the source'\n",
            encoding="utf-8",
        )
        pipeline = PipelineService(
            database,
            data_dir=root / "data",
            allowed_roots=[root],
            qdrant_url=qdrant_url,
            qdrant_api_key=qdrant_api_key,
            embedding_provider=_ConfiguredFakeEmbeddingProvider(16),
        )
        assert isinstance(pipeline.bm25, Bm25Index)
        assert isinstance(pipeline.dense, QdrantDenseIndex)
        collection = pipeline.dense.collection(corpus_id)
        if await pipeline.dense.client.collection_exists(collection):
            pytest.fail("unique synthetic Qdrant collection already exists; refusing to alter it")

        context = None
        try:
            report = await pipeline.ingest(
                IngestRequest(
                    corpus_id=corpus_id,
                    source_spec=Source(
                        id=source_id,
                        corpus_id=corpus_id,
                        kind=SourceKind.python_repo,
                        logical_uri=str(source_root),
                        display_name="B04 live retrieval fixture",
                        created_at=datetime.now(UTC),
                    ),
                    idempotency_key=f"b04-live-{uuid4()}",
                )
            )
            assert report.generation_id is not None
            context = await pipeline.knowledge_store.resolve_context(corpus_id, request_id)
            context = context.model_copy(update={"source_ids": [source_id]})
            from knowledge_weaver.models import RetrievalQuery

            query = RetrievalQuery(
                text="retrieval evidence",
                context=context,
                top_k=5,
            )
            result = await HybridRetriever(
                {
                    "bm25": Bm25Retriever(pipeline.bm25, pipeline.knowledge_store),
                    "dense": DenseRetriever(pipeline.dense, pipeline.knowledge_store),
                }
            ).retrieve(query)

            assert result.hits
            assert all(hit.evidence_ids for hit in result.hits)
            assert any(
                {item["channel"] for item in hit.contributions} >= {"bm25", "dense"}
                for hit in result.hits
            )
            assert result.context.generation_id == report.generation_id
        finally:
            if context is not None:
                await pipeline.knowledge_store.release_lease(request_id)
            async with database.session() as session:
                generation_ids = list(
                    (
                        await session.scalars(
                            select(GenerationRow.id).where(GenerationRow.corpus_id == corpus_id)
                        )
                    ).all()
                )
                for generation_id in generation_ids:
                    await pipeline.dense.delete_generation(corpus_id, generation_id)
                source_ids = list(
                    (
                        await session.scalars(
                            select(SourceRow.id).where(SourceRow.corpus_id == corpus_id)
                        )
                    ).all()
                )
                await session.rollback()
            await _delete_provenance_dependents(database, source_ids)
            async with database.session() as session:
                corpus = await session.get(CorpusRow, corpus_id)
                if corpus is not None:
                    await session.delete(corpus)
                await session.commit()
            await pipeline.dense.close()
            await database.dispose()
