from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from experiments.vertical_slice.agent import InvestigationBudget, make_agent
from experiments.vertical_slice.fixture_pdf import PDF_LINES, build_pdf, parse_fixture_pdf
from experiments.vertical_slice.ingest import PDF_PATH, build_corpus
from experiments.vertical_slice.offline_runtime import drive_ready_coroutine
from experiments.vertical_slice.providers import (
    FakeEmbeddingProvider,
    FakeGenerationProvider,
    LLMResult,
    ProviderUnavailable,
    QwenEmbeddingProvider,
    RemoteUseApproval,
)
from experiments.vertical_slice.retrieval import QdrantCandidateIndex
from experiments.vertical_slice.storage import (
    PostgresEvidenceStore,
    QdrantDenseIndex,
    qdrant_point_id,
    write_snapshot,
)


def _corpus():
    if not PDF_PATH.exists():
        build_pdf(PDF_PATH)
    return build_corpus(write_manifest=False)


def _run(awaitable):
    return drive_ready_coroutine(awaitable)


def test_pdf_and_python_ingestion_has_exact_page_and_line_evidence():
    corpus = _corpus()
    assert tuple(parse_fixture_pdf(PDF_PATH)) == PDF_LINES
    assert {item.source_kind for item in corpus.documents} == {"pdf", "python_repo"}
    assert all(item.quote for item in corpus.evidence)
    assert all(item.locator["kind"] in {"pdf", "python"} for item in corpus.evidence)
    pdf_evidence = next(item for item in corpus.evidence if item.source_kind == "pdf" and "ingestion pipeline stores" in item.quote)
    code_evidence = next(item for item in corpus.evidence if item.source_kind == "python_repo" and "span: SourceSpan" in item.quote)
    assert pdf_evidence.locator == {"kind": "pdf", "page": 1, "block": 2}
    assert code_evidence.locator["line_start"] == 15
    assert code_evidence.start == 0 and code_evidence.end == len(code_evidence.quote)
    assert len({item.id for item in corpus.evidence}) == len(corpus.evidence)


def test_bm25_and_dense_take_different_routes_and_close_two_hop_path():
    corpus = _corpus()
    agent = make_agent(corpus)
    exact = _run(agent.investigate("What type annotates EvidenceRecord.span?"))
    cross = _run(agent.investigate("How does the ingestion pipeline connect Evidence to source spans?"))
    assert exact.route_history == ["bm25"]
    assert exact.status == "answered"
    assert cross.route_history == ["dense", "graph"]
    assert cross.status == "answered"
    path_action = cross.action_history[-1]
    assert path_action["returned_path_count"] == 1
    assert set(path_action["returned_paths"][0]["source_kinds"]) == {"pdf", "python_repo"}
    assert len(cross.citations) >= 2
    assert {item.locator["kind"] for item in cross.citations} == {"pdf", "python"}
    assert len(cross.claims[0].relation_ids) == 2
    assert cross.budget_used["tool_calls"] == 2
    assert all(len(action["candidate_scores"][0]["features"]) == 12 for action in exact.action_history)


def test_no_evidence_is_insufficient_without_claims():
    result = _run(make_agent(_corpus()).investigate("What color is the admin console?"))
    assert result.status == "insufficient"
    assert result.claims == []
    assert result.citations == []
    assert result.stop_reason == "insufficient_evidence"


def test_conflict_discloses_both_sources_and_both_citations():
    result = _run(make_agent(_corpus()).investigate("Does the demo lookup cache survive process restart?"))
    assert result.status == "conflicted"
    assert "conflict" in result.answer.casefold()
    assert len(result.claims) == 2
    assert {item.locator["kind"] for item in result.citations} == {"pdf", "python"}
    assert len({item.evidence_id for item in result.citations}) >= 2


def test_timeout_returns_insufficient_without_generated_claims():
    agent = make_agent(_corpus(), FakeGenerationProvider(timeout=True))
    result = _run(agent.investigate("What type annotates EvidenceRecord.span?"))
    assert result.status == "insufficient"
    assert result.stop_reason == "deadline_exceeded"
    assert result.claims == []
    assert result.citations == []


def test_claim_with_unknown_evidence_id_is_rejected():
    class HallucinatedProvider:
        async def generate(self, request):
            return LLMResult(
                {
                    "status": "answered",
                    "answer": "An unsupported answer.",
                    "claims": [{"text": "An unsupported answer.", "evidence_ids": ["missing-evidence-id"]}],
                    "unanswered": [],
                },
                "test-fake",
                "fixed",
                {"count_kind": "unavailable"},
            )

    result = _run(make_agent(_corpus(), HallucinatedProvider()).investigate("What type annotates EvidenceRecord.span?"))
    assert result.status == "insufficient"
    assert result.claims == []
    assert result.citations == []


def test_budget_stop_does_not_answer_before_required_graph_action():
    agent = make_agent(_corpus())
    result = _run(
        agent.investigate(
            "How does the ingestion pipeline connect Evidence to source spans?",
            InvestigationBudget(max_steps=1, max_tool_calls=4, max_llm_calls=8, max_total_tokens=12000),
        )
    )
    assert result.status == "budget_exhausted"
    assert result.route_history == ["dense"]
    assert result.claims == []
    assert result.budget_used["tool_calls"] <= 1


def test_fake_embedding_is_deterministic_and_remote_gate_stays_closed():
    embedder = FakeEmbeddingProvider()
    first = _run(embedder.embed(["SourceSpan evidence"], mode="query"))
    second = _run(embedder.embed(["SourceSpan evidence"], mode="query"))
    assert first.vectors == second.vectors
    assert first.dimension == 32
    assert first.model == "fake-hash-embedding-v1"
    with pytest.raises(ProviderUnavailable, match="gate is closed"):
        _run(QwenEmbeddingProvider(RemoteUseApproval()).embed(["synthetic only"], mode="query"))


def test_safe_snapshot_and_isolation_names(tmp_path: Path):
    corpus = _corpus()
    path = tmp_path / "snapshot.json"
    digest = write_snapshot(path, corpus)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert digest and payload["generation_id"] == corpus.generation_id
    assert len(payload["evidence"]) == len(corpus.evidence)
    assert PostgresEvidenceStore.validate_schema("p3_vertical_slice_test") == "p3_vertical_slice_test"
    with pytest.raises(ValueError):
        PostgresEvidenceStore.validate_schema("public")
    with pytest.raises(ProviderUnavailable, match="loopback"):
        PostgresEvidenceStore("postgresql://kw:unused@remote.example:5432/knowledgeweaver")
    assert qdrant_point_id(corpus.generation_id, corpus.chunks[0].id, "fake-v1") == qdrant_point_id(
        corpus.generation_id, corpus.chunks[0].id, "fake-v1"
    )


class _MemoryQdrant:
    def __init__(self) -> None:
        self.collection = False
        self.points: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, path, payload))
        if method == "GET" and not self.collection:
            raise ProviderUnavailable("Qdrant request failed with HTTP 404")
        if method == "PUT" and path.startswith("/collections/p3_") and "points" not in path:
            self.collection = True
            return {"result": True}
        if method == "PUT" and "points?wait=true" in path:
            self.points.extend(payload["points"])
            return {"result": {"status": "completed"}}
        if method == "POST" and "/points/query" in path:
            return {"result": {"points": [{"id": item["id"], "score": 0.5, "payload": item["payload"]} for item in self.points]}}
        return {"result": True}


def test_qdrant_adapter_uses_generation_filter_and_p3_ids(monkeypatch: pytest.MonkeyPatch):
    async def immediate_thread_call(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", immediate_thread_call)

    async def scenario():
        corpus = _corpus()
        client = _MemoryQdrant()
        index = QdrantDenseIndex(client, "p3_vertical_slice_test", FakeEmbeddingProvider())
        wrapped = QdrantCandidateIndex(index)
        count = await wrapped.build(corpus.chunks, corpus.generation_id, corpus.corpus_id)
        hits = await wrapped.search("pipeline evidence", top_k=2, generation_id=corpus.generation_id)
        query = next(call for call in client.calls if call[0] == "POST")
        assert count == len(corpus.chunks)
        assert hits
        assert query[2]["filter"]["must"][0]["key"] == "generation_id"
        assert all(item["payload"]["generation_id"] == corpus.generation_id for item in client.points)

    _run(scenario())
