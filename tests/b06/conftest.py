from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from knowledge_weaver.core.ids import deterministic_id, sha256_text
from knowledge_weaver.models import (
    Budget,
    Chunk,
    ChunkSpan,
    CitationCheck,
    Evidence,
    IndexCandidate,
    IndexQuery,
    LLMRequest,
    LLMResult,
    ModelTokenUsage,
    Query,
    QueryContext,
    RetrievalHit,
    RetrievalQuery,
    RetrievalResult,
    ScoreDirection,
    SourceKind,
    SubQuestion,
    Usage,
)
from knowledge_weaver.retrieval import (
    Bm25Retriever,
    DenseRetriever,
    EntityRetriever,
    SymbolRetriever,
)


def make_context() -> QueryContext:
    now = datetime.now(UTC)
    return QueryContext(
        corpus_id=deterministic_id("b06_corpus", "fixture"),
        generation_id=deterministic_id("b06_generation", "one"),
        request_id=deterministic_id("b06_request", now.isoformat()),
        deadline_utc=now + timedelta(seconds=30),
    )


def make_chunk_evidence(
    *,
    text: str = "The build_index function stores a graph index.",
    duplicate_group: str | None = None,
    kind: str = "markdown",
) -> tuple[Chunk, Evidence]:
    chunk_id = deterministic_id("b06_chunk", {"text": text, "kind": kind})
    document_id = deterministic_id("b06_document", {"text": text, "kind": kind})
    provenance_id = deterministic_id("b06_provenance", text)
    locator: dict[str, Any]
    if kind == "python":
        locator = {"kind": "python", "path": "src/index.py", "line_start": 1, "line_end": 1, "qualname": "build_index"}
    else:
        locator = {"kind": "markdown", "path": "docs/index.md", "line_start": 1, "line_end": 1}
    chunk = Chunk(
        id=chunk_id,
        document_id=document_id,
        is_leaf=True,
        text=text,
        text_hash=sha256_text(text),
        spans=[ChunkSpan(chunk_start=0, chunk_end=len(text), document_start=0, document_end=len(text), locator=locator)],
        structure_type="code" if kind == "python" else "paragraph",
        duplicate_group=duplicate_group,
        chunker_fingerprint="b06-fixture-v1",
    )
    evidence = Evidence(
        id=deterministic_id("b06_evidence", {"chunk": chunk_id, "quote": text}),
        chunk_id=chunk_id,
        start=0,
        end=len(text),
        quote=text,
        locator=locator,
        provenance_id=provenance_id,
    )
    return chunk, evidence


def make_query(
    context: QueryContext,
    text: str = "What does build_index do?",
    *,
    required_source_types: list[SourceKind] | None = None,
) -> Query:
    query_id = deterministic_id("b06_query", {"text": text, "context": context.request_id})
    facet = SubQuestion(
        id=deterministic_id("b06_facet", query_id),
        text=text,
        required_source_types=required_source_types or [],
    )
    return Query(id=query_id, text=text, context=context, subquestions=[facet])


def make_budget(context: QueryContext, **updates: Any) -> Budget:
    values = {
        "max_steps": 6,
        "max_tool_calls": 8,
        "max_llm_calls": 8,
        "max_total_tokens": 12_000,
        "deadline_utc": context.deadline_utc,
        "max_graph_edges": 200,
    }
    values.update(updates)
    return Budget(**values)


class FakeStore:
    def __init__(self, chunks: dict[str, Chunk], evidence: dict[str, Evidence]) -> None:
        self.chunks = chunks
        self.evidence = evidence
        self.validation_calls = 0

    async def get_chunks(self, ids: list[str], context: QueryContext) -> list[Chunk]:
        return [self.chunks[item] for item in ids if item in self.chunks]

    async def get_evidence(self, ids: list[str], context: QueryContext) -> list[Evidence]:
        return [self.evidence[item] for item in ids if item in self.evidence]

    async def get_chunk_evidence(self, ids: list[str], context: QueryContext) -> list[Evidence]:
        return [item for item in self.evidence.values() if item.chunk_id in ids]

    async def validate_citations(self, ids: list[str], context: QueryContext) -> CitationCheck:
        self.validation_calls += 1
        valid = [item for item in ids if item in self.evidence]
        invalid = [{"id": item, "reason": "missing"} for item in ids if item not in self.evidence]
        return CitationCheck(valid_ids=valid, invalid=invalid)


class FakeIndex:
    def __init__(self, chunk_ids: list[str] | None = None) -> None:
        self.chunk_ids = chunk_ids or []
        self.queries: list[IndexQuery] = []

    async def search(self, query: IndexQuery) -> list[IndexCandidate]:
        self.queries.append(query)
        return [
            IndexCandidate(
                chunk_id=chunk_id,
                rank=rank,
                raw_score=1.0 / rank,
                score_direction=ScoreDirection.higher,
            )
            for rank, chunk_id in enumerate(self.chunk_ids[: query.top_k], start=1)
        ]


class EmptyEntityIndex:
    def __init__(self) -> None:
        self.queries: list[IndexQuery] = []

    async def search(self, query: IndexQuery) -> list[IndexCandidate]:
        self.queries.append(query)
        return []


class EmptyGraphStore:
    async def snapshot(self, context: QueryContext) -> Any:
        raise AssertionError("an empty EntityIndex must not fetch a graph snapshot")


class FakeRetriever:
    def __init__(self, result_factory: Callable[[RetrievalQuery], RetrievalResult]) -> None:
        self.result_factory = result_factory
        self.queries: list[RetrievalQuery] = []

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        self.queries.append(query)
        return self.result_factory(query)


class FakeVerifier:
    def __init__(self, *, support: float = 1.0, conflict: bool = False) -> None:
        self.support = support
        self.conflict = conflict
        self.calls = 0

    async def evaluate(self, request: Any) -> Any:
        from knowledge_weaver.models import SupportMatrix, SupportRow

        self.calls += 1
        return SupportMatrix(
            rows=[
                SupportRow(
                    facet_id=facet.id,
                    evidence_id=evidence.id,
                    support=self.support,
                    conflict=self.conflict,
                    reason_code="supports" if self.support else "unsupported",
                )
                for facet in request.facets
                for evidence in request.evidence
            ],
            usage=Usage(count_kind="exact"),
        )


class RecordingProvider:
    def __init__(self, *, bad_citations: bool = False) -> None:
        self.requests: list[LLMRequest] = []
        self.bad_citations = bad_citations

    async def generate(self, request: LLMRequest) -> LLMResult:
        self.requests.append(request)
        context = json.loads(request.untrusted_context)
        if "Choose exactly one offered action_id" in request.system_instruction:
            action_id = context["candidates"][0]["action_id"]
            payload = {"action_id": action_id, "reason_code": "facet_gap"}
        elif "Assess only whether" in request.system_instruction:
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
            selected = "00000000-0000-0000-0000-000000000001" if self.bad_citations else evidence_ids[0]
            payload = {
                "status": "answered",
                "claims": [{"text": "The supplied passage describes the function.", "evidence_ids": [selected], "relation_ids": []}],
                "unanswered": [],
            }
        return LLMResult(
            parsed_json=payload,
            usage=ModelTokenUsage(input_tokens=10, output_tokens=8, count_kind="exact"),
            model="fixture-model-v1",
            revision="fixture-revision-1",
        )


class FakeReranker:
    def __init__(self) -> None:
        self.calls = 0

    async def rerank(self, query: str, hits: list[RetrievalHit], context: QueryContext, limit: int) -> list[RetrievalHit]:
        self.calls += 1
        return [hit.model_copy(update={"rank": rank, "rerank_score": 1.0 / rank}) for rank, hit in enumerate(reversed(hits[:limit]), 1)]


@pytest.fixture
def evidence_bundle() -> tuple[FakeStore, Chunk, Evidence]:
    chunk, evidence = make_chunk_evidence()
    store = FakeStore({chunk.id: chunk}, {evidence.id: evidence})
    return store, chunk, evidence


@pytest.fixture
def real_b04_retrievers(evidence_bundle: tuple[FakeStore, Chunk, Evidence]) -> tuple[dict[str, Any], FakeIndex, EmptyEntityIndex]:
    store, chunk, _ = evidence_bundle
    index = FakeIndex([chunk.id])
    bm25 = Bm25Retriever(index, store)
    dense = DenseRetriever(index, store)
    symbol = SymbolRetriever(index, store)
    entity = EntityRetriever(index, store)
    graph_index = EmptyEntityIndex()

    from knowledge_weaver.graph import GraphRetriever

    graph = GraphRetriever(EmptyGraphStore(), graph_index, store)
    return {"bm25": bm25, "dense": dense, "symbol": symbol, "entity": entity, "graph": graph}, index, graph_index
