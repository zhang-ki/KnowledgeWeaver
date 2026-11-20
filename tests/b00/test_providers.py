from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from knowledge_weaver.core.budget import BudgetLedger
from knowledge_weaver.core.config import RemoteUseApproval, RuntimeSettings
from knowledge_weaver.core.errors import ProviderUnavailable
from knowledge_weaver.models import Budget, LLMRequest
from knowledge_weaver.providers import (
    CloudGenerationProvider,
    LocalQwenEmbeddingProvider,
    LocalQwenProfile,
    OpenAIChatProfile,
)
from knowledge_weaver.providers.http import HttpxJsonTransport

APPROVAL = RemoteUseApproval(
    external_data_approved=True, budget_approved=True, calls_explicitly_enabled=True
)
ONLINE = RuntimeSettings(allow_remote=True, model_offline=False)


def ledger(*, calls: int = 6, tokens: int = 2000, currency: Decimal | None = None) -> BudgetLedger:
    return BudgetLedger(Budget(
        deadline_utc=datetime.now(UTC) + timedelta(minutes=5), max_llm_calls=calls,
        max_total_tokens=tokens, max_currency_amount=currency,
    ))


def request() -> LLMRequest:
    return LLMRequest(
        system_instruction="Return a JSON object.", untrusted_context="synthetic test input",
        response_schema={
            "type": "object", "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"], "additionalProperties": False,
        }, max_output_tokens=16, timeout_seconds=2,
    )


def profile() -> OpenAIChatProfile:
    return OpenAIChatProfile(
        endpoint_url="https://provider.example/v1/chat/completions", model="fixture-model",
        api_key=SecretStr("fixture-secret"), retry_backoff_seconds=0,
    )


@pytest.mark.asyncio
async def test_generation_retries_rate_limit_and_charges_each_attempt() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": "synthetic limit"})
        return httpx.Response(200, json={
            "id": "fixture-request", "model": "fixture-revision",
            "choices": [{"message": {"content": "{\"ok\":true}"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = CloudGenerationProvider(
            profile(), APPROVAL, ledger(), settings=ONLINE,
            transport=HttpxJsonTransport(client), sleep=_no_sleep,
        )
        result = await provider.generate(request())
        usage = await provider.budget.snapshot()
    finally:
        await client.aclose()

    assert calls == 2
    assert result.parsed_json == {"ok": True}
    assert result.revision == "fixture-revision"
    assert usage.llm_calls == 2
    assert usage.count_kind == "estimated"


@pytest.mark.asyncio
async def test_offline_gate_blocks_before_transport() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = CloudGenerationProvider(
            profile(), APPROVAL, ledger(), settings=RuntimeSettings(allow_remote=True),
            transport=HttpxJsonTransport(client),
        )
        with pytest.raises(ProviderUnavailable) as raised:
            await provider.generate(request())
    finally:
        await client.aclose()

    assert raised.value.category == "approval_gate"
    assert calls == 0


@pytest.mark.asyncio
async def test_schema_rejection_is_sanitized_and_not_retried() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "{\"ok\":\"not-bool\"}"}}],
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = CloudGenerationProvider(
            profile(), APPROVAL, ledger(), settings=ONLINE,
            transport=HttpxJsonTransport(client), sleep=_no_sleep,
        )
        with pytest.raises(ProviderUnavailable) as raised:
            await provider.generate(request())
        usage = await provider.budget.snapshot()
    finally:
        await client.aclose()

    assert raised.value.category == "response_invalid"
    assert calls == 1
    assert usage.llm_calls == 1
    assert usage.count_kind == "estimated"


@pytest.mark.asyncio
async def test_cancellation_settles_reserved_budget() -> None:
    entered = asyncio.Event()

    class BlockingTransport:
        async def post_json(self, *args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

    budget = ledger()
    provider = CloudGenerationProvider(
        profile(), APPROVAL, budget, settings=ONLINE, transport=BlockingTransport(),
    )
    task = asyncio.create_task(provider.generate(request()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    usage = await budget.snapshot()
    assert usage.llm_calls == 1
    assert usage.input_tokens > 0
    assert usage.count_kind == "estimated"


@pytest.mark.asyncio
async def test_local_qwen_embedding_is_loopback_only_and_budgeted_at_zero_cost() -> None:
    class EmbeddingTransport:
        async def post_json(self, endpoint_url, payload, headers, **kwargs):
            assert endpoint_url == "http://127.0.0.1:8080/v1/embeddings"
            assert payload["input"] == ["query text"]
            return {"model": "qwen-revision", "data": [{"index": 0, "embedding": [3, 4]}]}

    with pytest.raises(ValueError, match="loopback"):
        LocalQwenProfile(endpoint_url="https://remote.example/embeddings", model="qwen")

    budget = ledger(currency=Decimal("1.00"))
    provider = LocalQwenEmbeddingProvider(
        LocalQwenProfile.from_environment({
            "KW_EMBEDDING_BASE_URL": "http://127.0.0.1:8080/v1",
            "KW_EMBEDDING_MODEL": "qwen-local",
        }), budget, transport=EmbeddingTransport(),
    )
    batch = await provider.embed(["query text"], "query")
    usage = await budget.snapshot()

    assert batch.dimension == 2
    assert batch.normalized is False
    assert batch.revision == "qwen-revision"
    assert batch.configuration_fingerprint
    assert usage.llm_calls == 1
    assert usage.currency_cost is None
    assert usage.count_kind == "estimated"


@pytest.mark.asyncio
async def test_embedding_fingerprints_preserve_mode_and_share_secret_free_configuration() -> None:
    seen_inputs: list[list[str]] = []

    class EmbeddingTransport:
        async def post_json(self, _endpoint_url, payload, _headers, **_kwargs):
            seen_inputs.append(payload["input"])
            return {"model": "resolved-qwen-revision", "data": [{"index": 0, "embedding": [3, 4]}]}

    async def embed_with(profile: LocalQwenProfile, mode: str):
        provider = LocalQwenEmbeddingProvider(profile, ledger(), transport=EmbeddingTransport())
        return await provider.embed(["same text"], mode)

    base = LocalQwenProfile(
        endpoint_url="http://127.0.0.1:8080/v1/embeddings",
        model="qwen-local",
        local_api_key=SecretStr("first-secret"),
        query_prefix="query: ",
        document_prefix="passage: ",
    )
    query_batch = await embed_with(base, "query")
    document_batch = await embed_with(base, "document")
    different_secret_batch = await embed_with(
        base.model_copy(update={"local_api_key": SecretStr("second-secret")}), "query"
    )
    different_prefix_batch = await embed_with(
        base.model_copy(update={"document_prefix": "document: "}), "query"
    )

    assert seen_inputs[:2] == [["query: same text"], ["passage: same text"]]
    assert query_batch.fingerprint != document_batch.fingerprint
    assert query_batch.fingerprint == "8740fce7cb8045f249b146920f766b61c303409deb738198263f901f15d89b63"
    assert document_batch.fingerprint == "3808d92eaf771e7c00bf896a4834b780c66ad7149d1a7795ca1c2924ea77c98a"
    assert query_batch.configuration_fingerprint == document_batch.configuration_fingerprint
    assert query_batch.configuration_fingerprint == different_secret_batch.configuration_fingerprint
    assert query_batch.configuration_fingerprint != different_prefix_batch.configuration_fingerprint


async def _no_sleep(seconds: float) -> None:
    return None

@pytest.mark.asyncio
async def test_profile_driven_reranker_preserves_citations_and_paths() -> None:
    from knowledge_weaver.models import QueryContext, RetrievalHit
    from knowledge_weaver.providers import CloudReranker, RerankProfile

    context = QueryContext(
        corpus_id="11111111-1111-4111-8111-111111111111",
        generation_id="22222222-2222-4222-8222-222222222222",
        request_id="33333333-3333-4333-8333-333333333333",
        deadline_utc=datetime.now(UTC) + timedelta(minutes=1),
    )
    hits = [
        RetrievalHit(
            chunk_id=f"00000000-0000-4000-8000-00000000020{index}",
            evidence_ids=[f"00000000-0000-4000-8000-00000000060{index}"],
            score=1 / (60 + index), channel="bm25", rank=index,
            contributions=[{"channel": "bm25", "rank": index, "rrf_score": 1 / (60 + index)}],
            path_ids=["00000000-0000-4000-8000-000000000901"],
        )
        for index in (1, 2)
    ]

    class Resolver:
        async def resolve_texts(self, chunk_ids, query_context):
            return {chunk_id: f"text {chunk_id[-1]}" for chunk_id in chunk_ids}

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        assert payload["query_text"] == "synthetic query"
        assert payload["limit"] == 2
        return httpx.Response(200, json={
            "rankings": [{"position": 1, "score": 0.1}, {"position": 0, "score": 0.8}],
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        reranker = CloudReranker(
            RerankProfile(
                profile_id="fixture-reranker", endpoint_url="https://provider.example/rerank",
                model="fixture-model", api_key=SecretStr("fixture-secret"),
                query_field="query_text", documents_field="items", limit_field="limit",
                results_field="rankings", index_field="position", score_field="score",
                score_direction="lower", max_attempts=1,
            ),
            APPROVAL, ledger(), Resolver(), settings=ONLINE,
            transport=HttpxJsonTransport(client),
        )
        ranked = await reranker.rerank("synthetic query", hits, context, 2)
    finally:
        await client.aclose()

    assert [hit.chunk_id for hit in ranked] == [hits[1].chunk_id, hits[0].chunk_id]
    assert ranked[0].evidence_ids == hits[1].evidence_ids
    assert ranked[0].path_ids == hits[1].path_ids
    assert ranked[0].contributions == hits[1].contributions
    assert ranked[0].rerank_score == 0.1
