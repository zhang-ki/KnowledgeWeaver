from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from knowledge_weaver.core.budget import BudgetLedger
from knowledge_weaver.core.errors import ErrorCode, KnowledgeWeaverError
from knowledge_weaver.models import (
    Budget,
    Chunk,
    ChunkSpan,
    CountKind,
    Evidence,
    IndexCandidate,
    IndexChannel,
    IndexQuery,
    PathStep,
    PythonLocator,
    QueryContext,
    RetrievalHit,
    RetrievalQuery,
    RetrievalResult,
    ScoreDirection,
    StructureType,
    Usage,
)
from knowledge_weaver.models import (
    Path as RetrievalPath,
)
from knowledge_weaver.retrieval import (
    RRF_K,
    Bm25Retriever,
    DenseRetriever,
    EntityRetriever,
    HybridRetriever,
    SymbolRetriever,
    reciprocal_rank_fusion,
)


def uid(number: int) -> str:
    return str(UUID(int=number))


@pytest.fixture
def context() -> QueryContext:
    return QueryContext(
        corpus_id=uid(10),
        generation_id=uid(20),
        request_id=uid(30),
        deadline_utc=datetime.now(UTC) + timedelta(seconds=5),
    )


@pytest.fixture
def retrieval_query(context: QueryContext) -> RetrievalQuery:
    return RetrievalQuery(text="query", context=context, top_k=10)


def make_chunk(chunk_id: str, *, leaf: bool = True) -> Chunk:
    body = f"evidence for {chunk_id}"
    return Chunk(
        id=chunk_id,
        document_id=uid(100),
        is_leaf=leaf,
        text=body,
        text_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        spans=[
            ChunkSpan(
                chunk_start=0,
                chunk_end=len(body),
                document_start=0,
                document_end=len(body),
                locator=PythonLocator(
                    kind="python", path="fixture.py", line_start=1, line_end=1
                ),
            )
        ],
        structure_type=StructureType.code,
        chunker_fingerprint="b04-fixture-v1",
    )


def make_evidence(evidence_id: str, chunk_id: str) -> Evidence:
    quote = f"evidence for {chunk_id}"
    return Evidence(
        id=evidence_id,
        chunk_id=chunk_id,
        start=0,
        end=len(quote),
        quote=quote,
        locator=PythonLocator(kind="python", path="fixture.py", line_start=1, line_end=1),
        provenance_id=uid(200),
    )


def make_hit(
    chunk_id: str,
    *,
    rank: int = 1,
    score: float = 1.0,
    channel: str = "test",
    evidence_ids: list[str] | None = None,
    path_ids: list[str] | None = None,
) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk_id,
        evidence_ids=evidence_ids or [],
        score=score,
        channel=channel,
        rank=rank,
        contributions=[
            {
                "channel": channel,
                "rank": rank,
                "raw_score": score,
                "score_direction": "higher",
            }
        ],
        path_ids=path_ids or [],
    )


def make_result(context: QueryContext, hits: list[RetrievalHit]) -> RetrievalResult:
    return RetrievalResult(context=context, hits=hits, usage=Usage())


class FakeIndex:
    def __init__(
        self,
        rows: list[tuple[str, float]],
        source_by_chunk: dict[str, str] | None = None,
    ) -> None:
        self.rows = rows
        self.source_by_chunk = source_by_chunk or {}
        self.queries: list[IndexQuery] = []

    async def search(self, query: IndexQuery) -> list[IndexCandidate]:
        self.queries.append(query)
        rows = list(self.rows)
        if query.context.source_ids is not None:
            allowed = set(query.context.source_ids)
            rows = [
                row
                for row in rows
                if self.source_by_chunk.get(row[0]) in allowed
            ]
        return [
            IndexCandidate(
                chunk_id=chunk_id,
                rank=rank,
                raw_score=score,
                score_direction=ScoreDirection.higher,
            )
            for rank, (chunk_id, score) in enumerate(rows[: query.top_k], start=1)
        ]


class FakeStore:
    def __init__(
        self,
        chunks: dict[str, Any],
        evidence: list[Any],
        source_by_chunk: dict[str, str] | None = None,
    ) -> None:
        self.chunks = chunks
        self.evidence = evidence
        self.source_by_chunk = source_by_chunk or {}

    async def get_chunks(self, ids: list[str], context: QueryContext) -> list[Any]:
        if context.source_ids is not None and any(
            self.source_by_chunk.get(chunk_id) not in set(context.source_ids)
            for chunk_id in ids
        ):
            raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "filtered")
        missing = set(ids) - set(self.chunks)
        if missing:
            raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "missing")
        return [self.chunks[chunk_id] for chunk_id in ids]

    async def get_chunk_evidence(self, ids: list[str], context: QueryContext) -> list[Any]:
        return [item for item in self.evidence if item.chunk_id in ids]


class FakeRetriever:
    def __init__(self, result: RetrievalResult | None = None, *, error: Exception | None = None, delay: float = 0) -> None:
        self.result = result
        self.error = error
        self.delay = delay
        self.queries: list[RetrievalQuery] = []

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        self.queries.append(query)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class FakeReranker:
    def __init__(self, *, invent_evidence: bool = False) -> None:
        self.invent_evidence = invent_evidence
        self.call: tuple[str, list[RetrievalHit], QueryContext, int] | None = None

    async def rerank(
        self, query: str, hits: list[RetrievalHit], context: QueryContext, limit: int
    ) -> list[RetrievalHit]:
        self.call = (query, hits, context, limit)
        ranked = list(reversed(hits[:limit]))
        result = []
        for rank, hit in enumerate(ranked, start=1):
            update: dict[str, Any] = {"rank": rank, "rerank_score": float(rank)}
            if self.invent_evidence:
                update["evidence_ids"] = [*hit.evidence_ids, uid(999)]
            else:
                update.update({"evidence_ids": [], "path_ids": [], "contributions": []})
            result.append(hit.model_copy(update=update))
        return result


@pytest.mark.parametrize(
    ("retriever_type", "expected_channel"),
    [
        (Bm25Retriever, IndexChannel.bm25),
        (DenseRetriever, IndexChannel.dense),
        (SymbolRetriever, IndexChannel.symbol),
        (EntityRetriever, IndexChannel.entity),
    ],
)
@pytest.mark.asyncio
async def test_each_index_retriever_maps_channel_and_hydrates_leaf_evidence(
    retriever_type: type,
    expected_channel: IndexChannel,
    retrieval_query: RetrievalQuery,
) -> None:
    parent_id, leaf_id = uid(301), uid(302)
    parent_evidence, leaf_evidence = uid(401), uid(402)
    index = FakeIndex([(parent_id, 9.0), (leaf_id, 8.0)])
    store = FakeStore(
        {parent_id: make_chunk(parent_id, leaf=False), leaf_id: make_chunk(leaf_id)},
        [
            make_evidence(parent_evidence, parent_id),
            make_evidence(leaf_evidence, leaf_id),
        ],
    )
    result = await retriever_type(index, store).retrieve(
        retrieval_query.model_copy(update={"top_k": 2})
    )

    assert index.queries[0].channel == expected_channel
    assert [hit.chunk_id for hit in result.hits] == [leaf_id]
    assert result.hits[0].evidence_ids == [leaf_evidence]
    assert result.hits[0].contributions[0] == {
        "channel": expected_channel.value,
        "rank": 2,
        "raw_score": 8.0,
        "score_direction": "higher",
    }


@pytest.mark.asyncio
async def test_empty_source_filter_returns_empty_before_index_call(
    context: QueryContext,
) -> None:
    index = FakeIndex([(uid(501), 1.0)])
    store = FakeStore({}, [])
    query = RetrievalQuery(
        text="query", context=context.model_copy(update={"source_ids": []}), top_k=3
    )

    result = await Bm25Retriever(index, store).retrieve(query)
    assert result.hits == []
    assert index.queries == []


@pytest.mark.asyncio
async def test_source_filter_reaches_index_before_top_k(
    context: QueryContext,
) -> None:
    source_a, source_b = uid(601), uid(602)
    first, allowed = uid(603), uid(604)
    index = FakeIndex(
        [(first, 10.0), (allowed, 9.0)],
        {first: source_a, allowed: source_b},
    )
    store = FakeStore(
        {allowed: make_chunk(allowed)},
        [make_evidence(uid(605), allowed)],
        {allowed: source_b},
    )
    query = RetrievalQuery(
        text="query", context=context.model_copy(update={"source_ids": [source_b]}), top_k=1
    )

    result = await Bm25Retriever(index, store).retrieve(query)

    assert index.queries[0].context.source_ids == [source_b]
    assert [hit.chunk_id for hit in result.hits] == [allowed]


def test_rrf_golden_manual_scores_duplicate_and_stable_tie() -> None:
    fixture_path = Path(__file__).parents[1] / "fixtures" / "b04" / "rrf_golden.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert fixture["k"] == RRF_K == 60
    channel_hits = {
        name: [
            make_hit(
                row["chunk_id"],
                rank=row["rank"],
                score=row["raw_score"],
                channel=name,
            ).model_copy(
                update={
                    "contributions": [
                        {
                            "channel": name,
                            "rank": row["rank"],
                            "raw_score": row["raw_score"],
                            "score_direction": row["score_direction"],
                        }
                    ]
                }
            )
            for row in rows
        ]
        for name, rows in fixture["channels"].items()
    }

    fused = reciprocal_rank_fusion(channel_hits)

    assert [hit.chunk_id for hit in fused] == fixture["expected_order"]
    assert {hit.chunk_id: hit.score for hit in fused} == pytest.approx(
        fixture["expected_scores"]
    )
    first = channel_hits["bm25"][0]
    duplicate = channel_hits["bm25"][2]
    assert len(
        [item for item in reciprocal_rank_fusion({"bm25": [first, duplicate]})[0].contributions]
    ) == 1
    tied = reciprocal_rank_fusion(
        {
            "one": [make_hit(uid(701), channel="one"), make_hit(uid(702), channel="one")],
            "two": [make_hit(uid(702), channel="two"), make_hit(uid(701), channel="two")],
        }
    )
    assert [hit.chunk_id for hit in tied] == sorted([uid(701), uid(702)])


@pytest.mark.asyncio
async def test_fake_graph_retriever_fuses_without_graph_implementation_dependency(
    context: QueryContext,
) -> None:
    chunk_id, evidence_id = uid(801), uid(802)
    path_id = uid(803)
    path = RetrievalPath(
        id=path_id,
        entity_ids=[uid(804), uid(805)],
        steps=[
            PathStep(
                relation_id=uid(806),
                from_id=uid(804),
                to_id=uid(805),
                evidence_ids=[evidence_id],
            )
        ],
    )
    graph = FakeRetriever(
        RetrievalResult(
            context=context,
            hits=[
                make_hit(chunk_id, score=0.8, channel="graph", path_ids=[path_id])
            ],
            paths=[path],
            usage=Usage(),
        )
    )
    lexical = FakeRetriever(make_result(context, [make_hit(chunk_id, channel="bm25")]))
    hybrid = HybridRetriever({"graph": graph, "bm25": lexical})

    result = await hybrid.retrieve(RetrievalQuery(text="query", context=context, top_k=5))

    assert len(result.hits) == 1
    assert result.hits[0].evidence_ids == [evidence_id]
    assert result.hits[0].path_ids == [path_id]
    assert [item["channel"] for item in result.hits[0].contributions] == ["bm25", "graph"]
    assert result.paths == [path]


@pytest.mark.asyncio
async def test_rerank_preserves_evidence_paths_and_contributions(
    context: QueryContext,
) -> None:
    chunk_a, chunk_b = uid(901), uid(902)
    evidence_id, path_id = uid(903), uid(904)
    path = RetrievalPath(
        id=path_id,
        entity_ids=[uid(905), uid(906)],
        steps=[PathStep(relation_id=uid(907), from_id=uid(905), to_id=uid(906), evidence_ids=[evidence_id])],
    )
    retriever = FakeRetriever(
        RetrievalResult(
            context=context,
            hits=[
                make_hit(chunk_a, rank=1, channel="graph", path_ids=[path_id]),
                make_hit(chunk_b, rank=2, channel="graph", evidence_ids=[uid(908)]),
            ],
            paths=[path],
            usage=Usage(),
        )
    )
    reranker = FakeReranker()

    run = await HybridRetriever({"graph": retriever}, reranker).retrieve_detailed(
        RetrievalQuery(text="query", context=context, top_k=2)
    )

    assert run.rerank_record.status == "applied"
    assert run.rerank_record.ranked_chunk_ids[0] == chunk_b
    reranked = run.result.hits[1]
    assert reranked.chunk_id == chunk_a
    assert reranked.evidence_ids == [evidence_id]
    assert reranked.path_ids == [path_id]
    assert reranked.contributions[0]["channel"] == "graph"
    assert run.result.paths == [path]


@pytest.mark.asyncio
async def test_reranker_cannot_introduce_evidence_and_failure_is_degraded(
    context: QueryContext,
) -> None:
    hit = make_hit(uid(1001), evidence_ids=[uid(1002)], channel="graph")
    reranker = FakeReranker(invent_evidence=True)
    run = await HybridRetriever(
        {"graph": FakeRetriever(make_result(context, [hit]))}, reranker
    ).retrieve_detailed(RetrievalQuery(text="query", context=context, top_k=1))

    assert run.rerank_record.status == "degraded"
    assert run.result.partial is True
    assert run.result.hits[0].evidence_ids == [uid(1002)]
    assert run.result.warnings == ["RERANK_FAILED:ValueError"]


@pytest.mark.asyncio
async def test_channel_top_k_is_independent_of_final_top_k(context: QueryContext) -> None:
    a = uid(1101)
    first = FakeRetriever(make_result(context, [make_hit(a, channel="first")]))
    second = FakeRetriever(make_result(context, [make_hit(uid(1102), channel="second")]))
    hybrid = HybridRetriever(
        {"first": first, "second": second}, channel_top_k={"first": 7, "second": 3}
    )

    result = await hybrid.retrieve(RetrievalQuery(text="query", context=context, top_k=1))

    assert first.queries[0].top_k == 7
    assert second.queries[0].top_k == 3
    assert len(result.hits) == 1


@pytest.mark.asyncio
async def test_timeout_and_channel_error_are_explicitly_degraded(
    context: QueryContext,
) -> None:
    one = uid(1201)
    failed = FakeRetriever(error=RuntimeError("private detail is not copied to warnings"))
    short_context = context.model_copy(
        update={"deadline_utc": datetime.now(UTC) + timedelta(milliseconds=30)}
    )
    fast = FakeRetriever(make_result(short_context, [make_hit(one, channel="fast")]))
    slow = FakeRetriever(make_result(short_context, []), delay=0.2)

    result = await HybridRetriever({"fast": fast, "slow": slow, "failed": failed}).retrieve(
        RetrievalQuery(text="query", context=short_context, top_k=5)
    )

    assert result.partial is True
    assert result.hits[0].chunk_id == one
    assert "slow:DEADLINE_EXCEEDED" in result.warnings
    assert "failed:CHANNEL_FAILED:RuntimeError" in result.warnings
    assert all("private detail" not in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_every_channel_failure_is_not_reported_as_successful_empty_result(
    context: QueryContext,
) -> None:
    hybrid = HybridRetriever(
        {
            "one": FakeRetriever(error=RuntimeError("one")),
            "two": FakeRetriever(error=RuntimeError("two")),
        }
    )

    with pytest.raises(KnowledgeWeaverError) as exc:
        await hybrid.retrieve(RetrievalQuery(text="query", context=context, top_k=1))
    assert exc.value.code == ErrorCode.INDEX_NOT_READY


@pytest.mark.asyncio
async def test_mixed_generation_is_rejected(context: QueryContext) -> None:
    wrong_context = context.model_copy(update={"generation_id": uid(1301)})
    retriever = FakeRetriever(make_result(wrong_context, [make_hit(uid(1302))]))

    with pytest.raises(KnowledgeWeaverError) as exc:
        await HybridRetriever({"wrong": retriever}).retrieve(
            RetrievalQuery(text="query", context=context, top_k=1)
        )
    assert exc.value.code == ErrorCode.GENERATION_CONFLICT


@pytest.mark.asyncio
async def test_shared_budget_limits_channel_calls(context: QueryContext) -> None:
    budget = BudgetLedger(
        Budget(max_tool_calls=1, deadline_utc=context.deadline_utc)
    )
    hybrid = HybridRetriever(
        {
            "first": FakeRetriever(make_result(context, [make_hit(uid(1401))])),
            "second": FakeRetriever(make_result(context, [make_hit(uid(1402))])),
        },
        budget=budget,
    )

    result = await hybrid.retrieve(RetrievalQuery(text="query", context=context, top_k=2))
    usage = await budget.snapshot()

    assert result.partial is True
    assert usage.tool_calls == 1
    assert any("BUDGET_EXHAUSTED" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_index_missing_reference_and_query_bounds_are_rejected(
    retrieval_query: RetrievalQuery,
) -> None:
    index = FakeIndex([(uid(1501), 1.0)])
    store = FakeStore({}, [])
    with pytest.raises(KnowledgeWeaverError) as exc:
        await Bm25Retriever(index, store).retrieve(retrieval_query)
    assert exc.value.code == ErrorCode.MISSING_REFERENCE

    with pytest.raises(ValueError):
        RetrievalQuery(text="query", context=retrieval_query.context, top_k=0)


@pytest.mark.asyncio
async def test_reranker_receives_at_most_forty_candidates(context: QueryContext) -> None:
    hits = [make_hit(uid(1600 + i), rank=i + 1, channel="graph") for i in range(45)]
    reranker = FakeReranker()
    run = await HybridRetriever(
        {"graph": FakeRetriever(make_result(context, hits))}, reranker
    ).retrieve_detailed(RetrievalQuery(text="query", context=context, top_k=100))

    assert reranker.call is not None
    assert len(reranker.call[1]) == 40
    assert reranker.call[3] == 40
    assert len(run.result.hits) == 45
    assert run.rerank_record.status == "applied"


@pytest.mark.asyncio
async def test_all_zero_source_filter_and_timeout_boundary(context: QueryContext) -> None:
    empty = context.model_copy(
        update={
            "source_ids": [],
            "deadline_utc": datetime.now(UTC) + timedelta(milliseconds=1),
        }
    )
    result = await Bm25Retriever(FakeIndex([(uid(1701), 1.0)]), FakeStore({}, [])).retrieve(
        RetrievalQuery(text="query", context=empty, top_k=100)
    )
    assert result.hits == []
    assert result.usage.count_kind == CountKind.exact
