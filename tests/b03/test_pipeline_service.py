from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from knowledge_weaver.core.errors import KnowledgeWeaverError
from knowledge_weaver.models import DeleteSourceRequest, IngestRequest, Source, SourceKind
from knowledge_weaver.providers.fake import FakeEmbeddingProvider
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


async def _delete_provenance_dependents(session, provenance_ids: list[str]) -> None:
    if not provenance_ids:
        return
    await session.execute(delete(RelationRow).where(RelationRow.provenance_id.in_(provenance_ids)))
    await session.execute(delete(EvidenceRow).where(EvidenceRow.provenance_id.in_(provenance_ids)))
    await session.execute(delete(ProvenanceRow).where(ProvenanceRow.id.in_(provenance_ids)))


@pytest.fixture
def short_tmp_path():
    with TemporaryDirectory(prefix="kwb03-live-", dir=Path.cwd()) as directory:
        yield Path(directory)


@pytest_asyncio.fixture
async def live_pipeline(short_tmp_path):
    database_url = os.environ.get("KW_DATABASE_URL")
    qdrant_url = os.environ.get("KW_QDRANT_URL")
    qdrant_api_key = os.environ.get("KW_QDRANT_API_KEY")
    if not database_url or not qdrant_url or not qdrant_api_key:
        pytest.skip("real PG/Qdrant connection configuration is provided only by Orchestrator acceptance")
    database = Database(database_url)
    pipeline = PipelineService(
        database,
        data_dir=short_tmp_path / "d",
        allowed_roots=[short_tmp_path],
        qdrant_url=qdrant_url,
        qdrant_api_key=qdrant_api_key,
        embedding_provider=_ConfiguredFakeEmbeddingProvider(16),
    )
    pipeline._test_corpus_ids = []
    yield pipeline
    async with database.session() as session:
        for corpus_id in pipeline._test_corpus_ids:
            generation_ids = list((await session.scalars(select(GenerationRow.id).where(GenerationRow.corpus_id == corpus_id))).all())
            for generation_id in generation_ids:
                if pipeline.dense:
                    await pipeline.dense.delete_generation(corpus_id, generation_id)
            source_ids = list((await session.scalars(select(SourceRow.id).where(SourceRow.corpus_id == corpus_id))).all())
            provenance_ids = (
                list((await session.scalars(select(ProvenanceRow.id).where(ProvenanceRow.source_id.in_(source_ids)))).all())
                if source_ids
                else []
            )
            await _delete_provenance_dependents(session, provenance_ids)
            corpus = await session.get(CorpusRow, corpus_id)
            if corpus is not None:
                await session.delete(corpus)
        await session.commit()
    if pipeline.dense:
        await pipeline.dense.close()
    await database.dispose()


def _source(root: Path, corpus_id: str, source_id: str) -> Source:
    return Source(
        id=source_id,
        corpus_id=corpus_id,
        kind=SourceKind.markdown,
        logical_uri=str(root),
        display_name="synthetic B03 source",
        created_at=datetime.now(UTC),
    )


class _ConfiguredFakeEmbeddingProvider:
    def __init__(self, dimension: int) -> None:
        self.provider = FakeEmbeddingProvider(dimension)
        self.configuration_fingerprint = f"fake-hash-config-{dimension}-l2"

    async def embed(self, texts, mode):
        batch = await self.provider.embed(texts, mode)
        return batch.model_copy(update={"configuration_fingerprint": self.configuration_fingerprint})


@pytest.mark.asyncio
async def test_generation_lifecycle_evidence_filter_delete_rebuild_and_lease(live_pipeline, short_tmp_path) -> None:
    corpus_id, source_id = str(uuid4()), str(uuid4())
    live_pipeline._test_corpus_ids.append(corpus_id)
    root = short_tmp_path / "s"
    root.mkdir()
    file = root / "note.md"
    file.write_text("generation one has persistent evidence", encoding="utf-8")
    source = _source(root, corpus_id, source_id)
    first_request = IngestRequest(corpus_id=corpus_id, source_spec=source, idempotency_key="b03-lifecycle-1")
    first = await live_pipeline.ingest(first_request)
    repeated = await live_pipeline.ingest(first_request)
    assert repeated.generation_id == first.generation_id
    assert first.generation_id is not None
    generation = await live_pipeline.knowledge_store.generation(first.generation_id)
    context = await live_pipeline.knowledge_store.resolve_context(corpus_id, str(uuid4()))
    assert context.generation_id == first.generation_id
    live_pipeline.knowledge_store.lease_seconds = 48 * 60 * 60
    leased = await live_pipeline.knowledge_store.resolve_context(corpus_id, str(uuid4()))
    evidence = await live_pipeline.knowledge_store.get_chunk_evidence(generation.chunk_ids, leased)
    assert evidence and all(item.quote for item in evidence)
    file.write_text("generation two updates the source and removes previous text", encoding="utf-8")
    updated = await live_pipeline.ingest(IngestRequest(corpus_id=corpus_id, source_spec=source, idempotency_key="b03-lifecycle-2"))
    assert updated.generation_id != first.generation_id
    assert await live_pipeline.knowledge_store.get_chunks(generation.chunk_ids, leased)
    updated_generation = await live_pipeline.knowledge_store.generation(updated.generation_id)
    deleted_chunk_ids = list(updated_generation.chunk_ids)
    assert deleted_chunk_ids
    future = datetime.now(UTC) + timedelta(hours=25)
    leased_gc = await live_pipeline.collect_garbage(corpus_id, now=future)
    assert first.generation_id not in leased_gc
    assert updated.generation_id not in leased_gc
    await live_pipeline.knowledge_store.release_lease(leased.request_id)
    retained_gc = await live_pipeline.collect_garbage(corpus_id, now=datetime.now(UTC))
    assert first.generation_id not in retained_gc
    assert updated.generation_id not in retained_gc
    expired_gc = await live_pipeline.collect_garbage(corpus_id, now=future)
    assert first.generation_id in expired_gc
    assert updated.generation_id not in expired_gc
    deleted = await live_pipeline.delete_source(DeleteSourceRequest(corpus_id=corpus_id, source_id=source_id, idempotency_key="b03-delete-1"))
    assert deleted.generation_id is not None and deleted.generation_id != updated.generation_id
    current = await live_pipeline.knowledge_store.resolve_context(corpus_id, str(uuid4()))
    assert await live_pipeline.knowledge_store.get_chunks([], current) == []
    with pytest.raises(KnowledgeWeaverError, match="outside the query generation"):
        await live_pipeline.knowledge_store.get_chunks(deleted_chunk_ids, current)
    rebuilt = await live_pipeline.ingest(IngestRequest(corpus_id=corpus_id, source_spec=source, idempotency_key="b03-lifecycle-3"))
    assert rebuilt.generation_id != deleted.generation_id
    after_rebuild = await live_pipeline.knowledge_store.resolve_context(corpus_id, str(uuid4()))
    rebuilt_generation = await live_pipeline.knowledge_store.generation(rebuilt.generation_id)
    assert await live_pipeline.knowledge_store.get_chunk_evidence(rebuilt_generation.chunk_ids, after_rebuild)


@pytest.mark.asyncio
async def test_active_generation_rejects_missing_and_cross_corpus_targets(live_pipeline) -> None:
    corpus_id, other_corpus_id, generation_id = (str(uuid4()) for _ in range(3))
    live_pipeline._test_corpus_ids.extend((corpus_id, other_corpus_id))
    now = datetime.now(UTC)
    async with live_pipeline.knowledge_store.database.session() as session:
        session.add_all([
            CorpusRow(id=corpus_id, active_generation_id=None, created_at=now),
            CorpusRow(id=other_corpus_id, active_generation_id=None, created_at=now),
            GenerationRow(
                id=generation_id,
                corpus_id=corpus_id,
                state="published",
                source_version_ids=[],
                index_manifest={},
                embedding_fingerprint="test-fingerprint",
                generation_hash="0" * 64,
                created_at=now,
                published_at=now,
            ),
        ])
        await session.flush()
        await session.execute(update(CorpusRow).where(CorpusRow.id == corpus_id).values(active_generation_id=generation_id))
        await session.commit()

    async with live_pipeline.knowledge_store.database.session() as session:
        await session.execute(update(CorpusRow).where(CorpusRow.id == other_corpus_id).values(active_generation_id=generation_id))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with live_pipeline.knowledge_store.database.session() as session:
        await session.execute(update(CorpusRow).where(CorpusRow.id == corpus_id).values(active_generation_id=str(uuid4())))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_failed_publish_preserves_active_and_concurrent_cas(live_pipeline, short_tmp_path) -> None:
    corpus_id, source_id = str(uuid4()), str(uuid4())
    live_pipeline._test_corpus_ids.append(corpus_id)
    root = short_tmp_path / "s"
    root.mkdir()
    (root / "note.md").write_text("cas stable source", encoding="utf-8")
    source = _source(root, corpus_id, source_id)
    first = await live_pipeline.ingest(IngestRequest(corpus_id=corpus_id, source_spec=source, idempotency_key="b03-cas-base"))
    assert first.generation_id
    before = await live_pipeline.knowledge_store.resolve_context(corpus_id, str(uuid4()))
    (root / "note.md").write_text("candidate facts", encoding="utf-8")
    for stage in ("after_facts", "after_indexes", "before_cas", "after_cas"):
        live_pipeline.failure_injector = lambda observed, failure_stage=stage: (_ for _ in ()).throw(RuntimeError("injected")) if observed == failure_stage else None
        with pytest.raises(RuntimeError, match="injected"):
            await live_pipeline.ingest(IngestRequest(corpus_id=corpus_id, source_spec=source, idempotency_key=f"b03-fail-{stage}"))
    live_pipeline.failure_injector = None
    assert (await live_pipeline.knowledge_store.resolve_context(corpus_id, str(uuid4()))).generation_id == before.generation_id
    (root / "note.md").write_text("concurrent publishers race", encoding="utf-8")
    other_root = short_tmp_path / "o"
    other_root.mkdir()
    (other_root / "other.md").write_text("other concurrent source", encoding="utf-8")
    source_b = _source(other_root, corpus_id, str(uuid4()))
    waiters = 0
    both_ready = asyncio.Event()

    async def synchronize(stage: str) -> None:
        nonlocal waiters
        if stage == "before_cas":
            waiters += 1
            if waiters == 2:
                both_ready.set()
            await both_ready.wait()

    live_pipeline.failure_injector = synchronize
    outcomes = await asyncio.gather(
        live_pipeline.ingest(IngestRequest(corpus_id=corpus_id, source_spec=source, idempotency_key="b03-race-a")),
        live_pipeline.ingest(IngestRequest(corpus_id=corpus_id, source_spec=source_b, idempotency_key="b03-race-b")),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, BaseException) for item in outcomes) == 1
    assert sum(isinstance(item, KnowledgeWeaverError) for item in outcomes) == 1
    live_pipeline.failure_injector = None
