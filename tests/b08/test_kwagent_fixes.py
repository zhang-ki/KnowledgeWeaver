"""Targeted regression tests for kwagent / provider / index fixes exposed on DashScope DeepSeek Flash."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from knowledge_weaver.agent import AgentServiceImpl, StructuredEvidenceVerifier
from knowledge_weaver.core.budget import BudgetLedger
from knowledge_weaver.core.config import RemoteUseApproval, RuntimeSettings
from knowledge_weaver.core.errors import ErrorCode, ProviderUnavailable
from knowledge_weaver.core.ids import deterministic_id, sha256_text
from knowledge_weaver.indexing.symbol import EntityIndex, SymbolIndex
from knowledge_weaver.models import (
    AnswerStatus,
    Budget,
    Chunk,
    ChunkSpan,
    CitationCheck,
    CountKind,
    Evidence,
    IndexChannel,
    IndexQuery,
    InvestigationRequest,
    Language,
    LLMRequest,
    LLMResult,
    ModelTokenUsage,
    PolicyKind,
    Query,
    QueryContext,
    RetrievalHit,
    RetrievalQuery,
    RetrievalResult,
    SubQuestion,
    Usage,
    VerifyRequest,
)
from knowledge_weaver.providers import CloudGenerationProvider, OpenAIChatProfile
from knowledge_weaver.providers._common import StructuredResponseInvalid
from knowledge_weaver.providers.http import HttpxJsonTransport

_APPROVAL = RemoteUseApproval(
    external_data_approved=True,
    budget_approved=True,
    calls_explicitly_enabled=True,
)
_ONLINE = RuntimeSettings(allow_remote=True, model_offline=False)


def _run_pure_async(coroutine: Any) -> Any:
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("local operation unexpectedly awaited I/O")


def _ledger() -> BudgetLedger:
    return BudgetLedger(
        Budget(
            deadline_utc=datetime.now(UTC) + timedelta(minutes=5),
            max_llm_calls=6,
            max_total_tokens=4000,
        )
    )


def _llm_request() -> LLMRequest:
    return LLMRequest(
        system_instruction="Return a JSON object.",
        untrusted_context="synthetic input",
        response_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        max_output_tokens=64,
        timeout_seconds=2,
    )


@pytest.mark.asyncio
async def test_dashscope_disables_thinking_by_default_and_injects_json_schema() -> None:
    captured_payloads: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "req-1",
                "model": "deepseek-v4.1-flash",
                "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
            },
        )

    profile = OpenAIChatProfile.from_environment(
        {
            "KW_LLM_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "KW_LLM_MODEL": "deepseek-v4.1-flash",
            "KW_LLM_API_KEY": "fixture-secret",
        }
    )
    assert profile.disable_thinking is None

    override_false = OpenAIChatProfile.from_environment(
        {
            "KW_LLM_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "KW_LLM_MODEL": "deepseek-v4.1-flash",
            "KW_LLM_API_KEY": "fixture-secret",
            "KW_LLM_DISABLE_THINKING": "false",
        }
    )
    assert override_false.disable_thinking is False

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = CloudGenerationProvider(
            profile,
            _APPROVAL,
            _ledger(),
            settings=_ONLINE,
            transport=HttpxJsonTransport(client),
        )
        result = await provider.generate(_llm_request())
        assert result.parsed_json == {"ok": True}
        assert captured_payloads[0]["enable_thinking"] is False
        assert "Required JSON Schema" in captured_payloads[0]["messages"][0]["content"]

        provider_unmodified = CloudGenerationProvider(
            override_false,
            _APPROVAL,
            _ledger(),
            settings=_ONLINE,
            transport=HttpxJsonTransport(client),
        )
        await provider_unmodified.generate(_llm_request())
        assert "enable_thinking" not in captured_payloads[1]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_reasoning_exhausted_raises_structured_diagnostic() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "req-2",
                "model": "deepseek-v4.1-flash",
                "choices": [
                    {
                        "message": {"content": "", "reasoning_content": "step by step..."},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 64, "total_tokens": 84},
            },
        )

    profile = OpenAIChatProfile(
        endpoint_url="https://provider.example/v1/chat/completions",
        model="deepseek-v4.1-flash",
        api_key=SecretStr("fixture-secret"),
        disable_thinking=False,
        max_attempts=1,
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = CloudGenerationProvider(
            profile,
            _APPROVAL,
            _ledger(),
            settings=_ONLINE,
            transport=HttpxJsonTransport(client),
        )
        with pytest.raises(StructuredResponseInvalid) as raised:
            await provider.generate(_llm_request())
        assert raised.value.response_diagnostic["reasoning_exhausted"] is True
    finally:
        await client.aclose()


def test_symbol_and_entity_index_match_qualified_suffix_and_nl_queries() -> None:
    with TemporaryDirectory(prefix="kwb08-sym-", dir=Path.cwd()) as directory:
        root = Path(directory)
        corpus, generation = str(uuid4()), str(uuid4())
        source_py, source_other = str(uuid4()), str(uuid4())
        chunk_validate, chunk_badge, chunk_other = str(uuid4()), str(uuid4()), str(uuid4())
        ctx_py = QueryContext(
            corpus_id=corpus,
            generation_id=generation,
            request_id=str(uuid4()),
            deadline_utc=datetime.now(UTC),
            source_ids=[source_py],
        )

        symbol = SymbolIndex(root)
        symbol.build(
            corpus,
            generation,
            [
                {"chunk_id": chunk_validate, "source_id": source_py, "name": "py_mod.pipeline.validate_prefix"},
                {"chunk_id": chunk_badge, "source_id": source_py, "name": "py_mod.pipeline.issue_badge"},
                {"chunk_id": chunk_other, "source_id": source_other, "name": "other.validate_prefix"},
            ],
        )
        exact_suffix = _run_pure_async(
            symbol.search(IndexQuery(channel=IndexChannel.symbol, context=ctx_py, text="validate_prefix", top_k=5))
        )
        assert [item.chunk_id for item in exact_suffix] == [chunk_validate]

        nl_hits = _run_pure_async(
            symbol.search(
                IndexQuery(
                    channel=IndexChannel.symbol,
                    context=ctx_py,
                    text="What does validate_prefix check and what does issue_badge return?",
                    top_k=5,
                )
            )
        )
        assert {item.chunk_id for item in nl_hits} == {chunk_validate, chunk_badge}

        entity = EntityIndex(root)
        entity.build(
            corpus,
            generation,
            [
                {
                    "chunk_id": chunk_validate,
                    "source_id": source_py,
                    "entity_id": str(uuid4()),
                    "canonical_name": "py_mod.pipeline.validate_prefix",
                    "namespace": "py_mod",
                    "identity_key": "validate_prefix",
                    "aliases": [],
                }
            ],
        )
        entity_hits = _run_pure_async(
            entity.search(
                IndexQuery(
                    channel=IndexChannel.entity,
                    context=ctx_py,
                    text="What does validate_prefix check?",
                    top_k=5,
                )
            )
        )
        assert [item.chunk_id for item in entity_hits] == [chunk_validate]


@pytest.mark.asyncio
async def test_agent_records_verifier_failure_into_observation_error() -> None:
    now = datetime.now(UTC)
    context = QueryContext(
        corpus_id=deterministic_id("b08_corpus", "1"),
        generation_id=deterministic_id("b08_gen", "1"),
        request_id=deterministic_id("b08_req", "1"),
        deadline_utc=now + timedelta(seconds=30),
    )
    text = "The validate_prefix function checks prefix kw_."
    chunk_id = deterministic_id("b08_chunk", text)
    doc_id = deterministic_id("b08_doc", text)
    prov_id = deterministic_id("b08_prov", text)
    locator = {"kind": "markdown", "path": "policy.md", "line_start": 1, "line_end": 1}
    chunk = Chunk(
        id=chunk_id,
        document_id=doc_id,
        is_leaf=True,
        text=text,
        text_hash=sha256_text(text),
        spans=[ChunkSpan(chunk_start=0, chunk_end=len(text), document_start=0, document_end=len(text), locator=locator)],
        structure_type="paragraph",
        chunker_fingerprint="b08-fixture-v1",
    )
    evidence = Evidence(
        id=deterministic_id("b08_ev", text),
        chunk_id=chunk_id,
        start=0,
        end=len(text),
        quote=text,
        locator=locator,
        provenance_id=prov_id,
    )

    class _Store:
        async def get_chunks(self, ids: list[str], _context: QueryContext) -> list[Chunk]:
            return [chunk] if chunk_id in ids else []

        async def get_evidence(self, ids: list[str], _context: QueryContext) -> list[Evidence]:
            return [evidence] if evidence.id in ids else []

        async def get_chunk_evidence(self, ids: list[str], _context: QueryContext) -> list[Evidence]:
            return [evidence] if chunk_id in ids else []

        async def validate_citations(self, ids: list[str], _context: QueryContext) -> CitationCheck:
            return CitationCheck(valid_ids=[i for i in ids if i == evidence.id], invalid=[])

    class _Retriever:
        def __init__(self, channel: str) -> None:
            self.channel = channel

        async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
            return RetrievalResult(
                context=query.context,
                hits=[RetrievalHit(chunk_id=chunk_id, evidence_ids=[evidence.id], score=1.0, channel=self.channel, rank=1)],
                usage=Usage(tool_calls=1, count_kind=CountKind.exact),
            )

    class _FailingVerifier:
        async def evaluate(self, _request: Any) -> Any:
            raise ProviderUnavailable("verifier output rejected", category="response_invalid", retryable=False)

    class _AnswerProvider:
        async def generate(self, _request: LLMRequest) -> LLMResult:
            return LLMResult(
                parsed_json={"status": "insufficient", "claims": [], "unanswered": ["q"]},
                usage=ModelTokenUsage(input_tokens=10, output_tokens=5, count_kind=CountKind.exact),
                model="fixture",
                revision="1",
            )

    trace_entries: list[dict[str, Any]] = []
    service = AgentServiceImpl(
        _Store(),
        {name: _Retriever(name) for name in ("bm25", "dense", "symbol", "entity", "graph")},
        _AnswerProvider(),
        verifier_factory=lambda _gen: _FailingVerifier(),
        trace_callback=lambda _inv_id, entry: trace_entries.append(entry),
    )

    query_id = deterministic_id("b08_q", "1")
    request = InvestigationRequest(
        query=Query(
            id=query_id,
            text="What does validate_prefix check?",
            context=context,
            subquestions=[SubQuestion(id=deterministic_id("b08_sq", "1"), text="What does validate_prefix check?")],
        ),
        context=context,
        budget=Budget(
            max_steps=1,
            max_tool_calls=4,
            max_llm_calls=4,
            max_total_tokens=4000,
            deadline_utc=context.deadline_utc,
            max_graph_edges=50,
        ),
        policy=PolicyKind.fixed,
        language=Language.en,
    )

    answer = await service.investigate(request)
    assert answer.status is AnswerStatus.insufficient
    obs_errors = [
        entry["observation"]["error"]
        for entry in trace_entries
        if isinstance(entry.get("observation"), dict) and entry["observation"].get("error") is not None
    ]
    assert obs_errors, f"expected observation error in trace_entries={trace_entries}"
    assert ErrorCode.PROVIDER_UNAVAILABLE.value in obs_errors[0]
    assert "verifier output rejected" in obs_errors[0]


def test_structured_verifier_budget_and_reason_enum() -> None:
    captured: list[LLMRequest] = []

    async def _fake_generate(request: LLMRequest) -> LLMResult:
        captured.append(request)
        return LLMResult(
            parsed_json={"rows": []},
            usage=ModelTokenUsage(input_tokens=5, output_tokens=2, count_kind=CountKind.exact),
            model="fixture",
            revision="1",
        )

    async def _run() -> None:
        now = datetime.now(UTC)
        context = QueryContext(
            corpus_id=deterministic_id("b08_vc", "1"),
            generation_id=deterministic_id("b08_vg", "1"),
            request_id=deterministic_id("b08_vr", "1"),
            deadline_utc=now + timedelta(seconds=30),
        )
        ev = Evidence(
            id=deterministic_id("b08_vev", "1"),
            chunk_id=deterministic_id("b08_vch", "1"),
            start=0,
            end=5,
            quote="hello",
            locator={"kind": "markdown", "path": "a.md", "line_start": 1, "line_end": 1},
            provenance_id=deterministic_id("b08_vpr", "1"),
        )
        query = Query(
            id=deterministic_id("b08_vq", "1"),
            text="hello?",
            context=context,
            subquestions=[SubQuestion(id=deterministic_id("b08_vsq", "1"), text="hello?")],
        )
        verifier = StructuredEvidenceVerifier(_fake_generate)
        await verifier.evaluate(VerifyRequest(query=query, facets=query.subquestions, evidence=[ev], paths=[]))

    asyncio.run(_run())
    assert len(captured) == 1
    assert captured[0].max_output_tokens >= 1024
    reason_prop = captured[0].response_schema["properties"]["rows"]["items"]["properties"]["reason_code"]
    assert reason_prop["enum"] == ["supports", "partial", "unsupported", "contradicts", "unrelated"]
