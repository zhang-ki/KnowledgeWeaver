from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge_weaver.indexing.bm25 import TOKENIZER_VERSION, Bm25Index, tokenize
from knowledge_weaver.indexing.dense import QdrantDenseIndex
from knowledge_weaver.indexing.symbol import EntityIndex, SymbolIndex
from knowledge_weaver.models import IndexChannel, IndexQuery, QueryContext
from knowledge_weaver.providers.fake import FakeEmbeddingProvider


@pytest.fixture
def short_tmp_path():
    with TemporaryDirectory(prefix="kwb03-", dir=Path.cwd()) as directory:
        yield Path(directory)


def _context(corpus_id: str, generation_id: str, source_ids: list[str] | None = None) -> QueryContext:
    return QueryContext(
        corpus_id=corpus_id,
        generation_id=generation_id,
        request_id=str(uuid4()),
        deadline_utc=datetime.now(UTC),
        source_ids=source_ids,
    )


def _run_pure_async(coroutine):
    """Run a coroutine whose implementation performs no awaitable I/O."""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("local index operation unexpectedly awaited I/O")


def test_bm25_tokenizer_is_fixed_for_latin_identifiers_and_cjk() -> None:
    assert TOKENIZER_VERSION == "kw-bm25-latin-camel-snake-cjk-char-v1"
    assert tokenize("parseHTTPResponse foo_bar 中文知识") == [
        "parse", "http", "response", "foo", "bar", "中", "文", "知", "识"
    ]


def test_bm25_generation_and_source_filter_precede_top_k(short_tmp_path) -> None:
    corpus, generation = str(uuid4()), str(uuid4())
    source_a, source_b = str(uuid4()), str(uuid4())
    index = Bm25Index(short_tmp_path)
    path = index.build(corpus, generation, [
        {"chunk_id": str(uuid4()), "source_id": source_a, "text": "generation publish facts"},
        {"chunk_id": str(uuid4()), "source_id": source_b, "text": "generation publish facts"},
    ])
    assert (short_tmp_path / corpus / generation / "bm25.json").is_file()
    assert path.endswith("bm25.json")
    results = _run_pure_async(index.search(IndexQuery(channel=IndexChannel.bm25, context=_context(corpus, generation, [source_b]), text="publish", top_k=1)))
    assert len(results) == 1
    empty = _run_pure_async(index.search(IndexQuery(channel=IndexChannel.bm25, context=_context(corpus, generation, []), text="publish", top_k=5)))
    assert empty == []


def test_symbol_exact_prefix_and_entity_alias_scoping(short_tmp_path) -> None:
    corpus, generation = str(uuid4()), str(uuid4())
    source = str(uuid4())
    chunk_exact, chunk_prefix, chunk_alias = (str(uuid4()) for _ in range(3))
    symbol = SymbolIndex(short_tmp_path)
    symbol.build(corpus, generation, [
        {"chunk_id": chunk_exact, "source_id": source, "name": "pkg.mod.parse"},
        {"chunk_id": chunk_prefix, "source_id": source, "name": "pkg.mod.parser"},
    ])
    results = _run_pure_async(symbol.search(IndexQuery(channel=IndexChannel.symbol, context=_context(corpus, generation, [source]), text="pkg.mod.par", top_k=5)))
    assert {item.chunk_id for item in results} == {chunk_exact, chunk_prefix}
    entity = EntityIndex(short_tmp_path)
    entity.build(corpus, generation, [
        {"chunk_id": chunk_alias, "source_id": source, "entity_id": str(uuid4()), "canonical_name": "Quartz", "namespace": "src-a", "identity_key": "a", "aliases": ["QZ"]},
        {"chunk_id": chunk_exact, "source_id": source, "entity_id": str(uuid4()), "canonical_name": "Quartz", "namespace": "src-b", "identity_key": "b", "aliases": ["QZ"]},
    ])
    aliases = _run_pure_async(entity.search(IndexQuery(channel=IndexChannel.entity, context=_context(corpus, generation, [source]), text="qz", top_k=5)))
    assert {item.chunk_id for item in aliases} == {chunk_exact, chunk_alias}
    filtered = _run_pure_async(entity.search(IndexQuery(channel=IndexChannel.entity, context=_context(corpus, generation, []), text="qz", top_k=5)))
    assert filtered == []


@pytest.mark.parametrize(
    "payload_update",
    [
        pytest.param({"dimension": 3}, id="dimension"),
        pytest.param({"model": "different-model"}, id="metadata"),
    ],
)
@pytest.mark.asyncio
async def test_dense_search_rejects_published_dimension_and_metadata_mismatch(payload_update) -> None:
    class QueryClient:
        def __init__(self) -> None:
            self.queried = False

        async def collection_exists(self, _collection: str) -> bool:
            return True

        async def query_points(self, *_args, **_kwargs):
            self.queried = True
            payload = {
                "embedding_mode": "document",
                "dimension": 4,
                "normalized": True,
                "model": "fake-hash-embedding-v1",
                "revision": "fixed-v1",
                "chunk_id": str(uuid4()),
            }
            payload.update(payload_update)
            return SimpleNamespace(points=[SimpleNamespace(payload=payload, score=0.9)])

        async def close(self) -> None:
            return None

    corpus, generation = str(uuid4()), str(uuid4())
    index = QdrantDenseIndex("http://127.0.0.1:1", FakeEmbeddingProvider(4))
    client = QueryClient()
    index.client = client
    query = IndexQuery(
        channel=IndexChannel.dense,
        context=_context(corpus, generation),
        text="sample query",
        top_k=1,
    )

    with pytest.raises(ValueError, match="query embedding does not match published document embedding fingerprint"):
        await index.search(query)

    assert client.queried
    await index.close()
