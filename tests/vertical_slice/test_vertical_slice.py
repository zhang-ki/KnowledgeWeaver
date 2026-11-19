from __future__ import annotations

import asyncio
import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from experiments.vertical_slice import providers as provider_module
from experiments.vertical_slice.agent import InvestigationBudget, make_agent
from experiments.vertical_slice.fixture_pdf import PDF_LINES, build_pdf, parse_fixture_pdf
from experiments.vertical_slice.ingest import PDF_PATH, build_corpus
from experiments.vertical_slice.offline_runtime import drive_ready_coroutine
from experiments.vertical_slice.providers import (
    FakeEmbeddingProvider,
    FakeGenerationProvider,
    DeepSeekGenerationProvider,
    LLMRequest,
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


def test_fake_embedding_is_deterministic():
    embedder = FakeEmbeddingProvider()
    first = _run(embedder.embed(["SourceSpan evidence"], mode="query"))
    second = _run(embedder.embed(["SourceSpan evidence"], mode="query"))
    assert first.vectors == second.vectors
    assert first.dimension == 32
    assert first.model == "fake-hash-embedding-v1"


class _MockJsonResponse:
    """HTTP response fixture for mocked provider-contract tests; never a live model result."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _configure_local_qwen(monkeypatch: pytest.MonkeyPatch, endpoint: str = "http://127.0.0.1:56334/v1") -> None:
    monkeypatch.setenv("KW_EMBEDDING_BASE_URL", endpoint)
    monkeypatch.setenv("KW_EMBEDDING_MODEL", "qwen-local-alias")
    for name in (
        "KW_LOCAL_EMBEDDING_API_KEY",
        "KW_EMBEDDING_API_KEY",
        "KW_LLM_API_KEY",
        "KW_LLM_BASE_URL",
        "KW_LLM_MODEL",
        "KW_ALLOW_REMOTE",
        "KW_MODEL_OFFLINE",
    ):
        monkeypatch.delenv(name, raising=False)


def _immediate_thread_call(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", run_inline)


def _qwen_response(vectors: list[list[float]]) -> dict[str, Any]:
    return {
        "data": [{"index": index, "embedding": vector} for index, vector in enumerate(vectors)],
        "model": "server-response-model-is-not-trusted",
        "usage": {"prompt_tokens": len(vectors)},
    }


def test_mock_local_qwen_works_without_cloud_credentials_or_remote_approval(monkeypatch: pytest.MonkeyPatch):
    """A mocked loopback response verifies the local request contract, not Qwen inference."""
    _configure_local_qwen(monkeypatch)
    _immediate_thread_call(monkeypatch)
    requests: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def mock_open(request, timeout, *, local_only):
        assert local_only
        payload = json.loads(request.data.decode("utf-8"))
        headers = {name.casefold(): value for name, value in request.header_items()}
        requests.append((request.full_url, payload, headers))
        return _MockJsonResponse(_qwen_response([[0.25, -0.5] for _ in payload["input"]]))

    monkeypatch.setattr(provider_module, "_open_json_request", mock_open)
    provider = QwenEmbeddingProvider()
    query_batch = _run(provider.embed(["query text"], mode="query"))
    document_batch = _run(provider.embed(["document text"], mode="document"))

    assert [request[0] for request in requests] == [
        "http://127.0.0.1:56334/v1/embeddings",
        "http://127.0.0.1:56334/v1/embeddings",
    ]
    assert requests[0][1] == {"model": "qwen-local-alias", "input": ["query text"]}
    assert requests[1][1] == {"model": "qwen-local-alias", "input": ["document text"]}
    assert all("authorization" not in request[2] for request in requests)
    assert query_batch.vectors == [[0.25, -0.5]]
    assert document_batch.dimension == 2
    assert query_batch.model == "qwen-local-alias"
    assert query_batch.revision == "server-response-model-is-not-trusted"
    assert query_batch.usage == {"input_tokens": 1, "count_kind": "exact"}


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.example.test/v1",
        "http://127.0.0.2:56334/v1",
        "http://[::2]:56334/v1",
        "http://localhost.evil.test/v1",
        "http://user:password@localhost:56334/v1",
    ],
)
def test_qwen_rejects_non_loopback_endpoint_before_network(monkeypatch: pytest.MonkeyPatch, endpoint: str):
    _configure_local_qwen(monkeypatch, endpoint)
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("mock network must not be reached for a rejected endpoint")

    monkeypatch.setattr(provider_module, "_open_json_request", fail_if_called)
    with pytest.raises(ProviderUnavailable, match="loopback") as error:
        QwenEmbeddingProvider()._embed_sync(["synthetic text"], mode="document")
    assert not called
    assert "password" not in str(error.value)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:56334/v1",
        "http://127.0.0.1:56334/v1",
        "http://[::1]:56334/v1",
    ],
)
def test_qwen_loopback_host_allowlist_accepts_only_named_hosts(endpoint: str):
    assert QwenEmbeddingProvider._validate_loopback_endpoint(endpoint) == endpoint


def test_local_qwen_transport_disables_proxy_and_redirects(monkeypatch: pytest.MonkeyPatch):
    captured_handlers: list[Any] = []

    class MockOpener:
        def open(self, request, timeout):
            assert request.full_url == "http://127.0.0.1:56334/v1/embeddings"
            return _MockJsonResponse({"ok": True})

    def mock_build_opener(*handlers):
        captured_handlers.extend(handlers)
        return MockOpener()

    monkeypatch.setattr(provider_module.urllib.request, "build_opener", mock_build_opener)
    request = provider_module.urllib.request.Request("http://127.0.0.1:56334/v1/embeddings")
    with provider_module._open_json_request(request, timeout=1.0, local_only=True) as response:
        assert json.loads(response.read().decode("utf-8")) == {"ok": True}
    proxy_handler = next(item for item in captured_handlers if isinstance(item, provider_module.urllib.request.ProxyHandler))
    redirect_handler = next(item for item in captured_handlers if isinstance(item, provider_module._NoRedirectHandler))
    assert proxy_handler.proxies == {}
    assert redirect_handler.redirect_request(None, None, 302, "", {}, "https://external.example") is None


@pytest.mark.parametrize(
    ("response", "input_count", "expected_message"),
    [
        ({"data": [{"index": 0, "embedding": [0.1, 0.2]}]}, 2, "response shape is invalid"),
        (
            {"data": [{"index": 1, "embedding": [0.1]}, {"index": 0, "embedding": [0.2]}]},
            2,
            "response order is invalid",
        ),
        (
            {"data": [{"index": 0, "embedding": [0.1, 0.2]}, {"index": 1, "embedding": [0.3]}]},
            2,
            "dimensions are inconsistent",
        ),
        ({"data": [{"index": 0, "embedding": []}]}, 1, "non-empty vector"),
        ({"data": [{"index": 0, "embedding": [float("inf")]}]}, 1, "vector values are invalid"),
        ({"data": [{"index": 0, "embedding": [float("nan")]}]}, 1, "vector values are invalid"),
        ({"data": [{"index": 0, "embedding": [True]}]}, 1, "vector values are invalid"),
        ({"data": [{"index": 0, "embedding": ["0.5"]}]}, 1, "vector values are invalid"),
    ],
    ids=["count", "order", "dimension", "empty-vector", "infinite", "nan", "boolean", "string"],
)
def test_mock_qwen_invalid_embedding_responses_are_rejected_without_echo(
    monkeypatch: pytest.MonkeyPatch, response: dict[str, Any], input_count: int, expected_message: str
):
    """Mocked response-shape failures are contract tests, never real-model evidence."""
    _configure_local_qwen(monkeypatch)
    monkeypatch.setattr(provider_module, "_post_json", lambda *args, **kwargs: response)
    with pytest.raises(ProviderUnavailable, match=expected_message) as error:
        QwenEmbeddingProvider()._embed_sync(["input"] * input_count, "query")
    assert "server-response-model-is-not-trusted" not in str(error.value)


def test_qwen_uses_only_dedicated_optional_local_key(monkeypatch: pytest.MonkeyPatch):
    _configure_local_qwen(monkeypatch)
    monkeypatch.setenv("KW_LOCAL_EMBEDDING_API_KEY", "unit-test-local-placeholder")
    monkeypatch.setenv("KW_EMBEDDING_API_KEY", "unused-legacy-placeholder")
    captured: list[dict[str, str]] = []

    def mock_open(request, timeout, *, local_only):
        assert local_only
        captured.append({name.casefold(): value for name, value in request.header_items()})
        return _MockJsonResponse(_qwen_response([[0.1]]))

    monkeypatch.setattr(provider_module, "_open_json_request", mock_open)
    QwenEmbeddingProvider()._embed_sync(["text"], "document")
    assert captured[0]["authorization"] == "Bearer unit-test-local-placeholder"
    assert "unused-legacy-placeholder" not in captured[0]["authorization"]


def _open_remote_approval() -> RemoteUseApproval:
    return RemoteUseApproval(
        external_data_approved=True,
        budget_approved=True,
        calls_explicitly_enabled=True,
        currency_cap=0.01,
    )


def _llm_request() -> LLMRequest:
    return LLMRequest("system instruction", '{"evidence":[]}', {"type": "object"}, 64, 2.0)


def test_deepseek_cloud_provider_remains_closed_without_remote_approval(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KW_LLM_BASE_URL", "https://api.deepseek.example/v1")
    monkeypatch.setenv("KW_LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("KW_LLM_API_KEY", "unit-test-cloud-placeholder")
    monkeypatch.delenv("KW_ALLOW_REMOTE", raising=False)
    monkeypatch.delenv("KW_MODEL_OFFLINE", raising=False)
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("closed cloud gate must reject before any request")

    monkeypatch.setattr(provider_module, "_post_json", fail_if_called)
    with pytest.raises(ProviderUnavailable, match="gate is closed") as error:
        _run(DeepSeekGenerationProvider(RemoteUseApproval()).generate(_llm_request()))
    assert not called
    assert error.value.category == "approval_gate"
    assert error.value.retryable is False
    assert "unit-test-cloud-placeholder" not in str(error.value)


def test_uncapped_remote_approval_still_requires_every_explicit_gate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KW_ALLOW_REMOTE", "true")
    monkeypatch.setenv("KW_MODEL_OFFLINE", "false")
    RemoteUseApproval(
        external_data_approved=True,
        budget_approved=True,
        calls_explicitly_enabled=True,
        currency_cap=None,
    ).require_open()

    for approval in (
        RemoteUseApproval(False, True, True, None),
        RemoteUseApproval(True, False, True, None),
        RemoteUseApproval(True, True, False, None),
        RemoteUseApproval(True, True, True, 0),
    ):
        with pytest.raises(ProviderUnavailable, match="gate is closed"):
            approval.require_open()

    for variable, closed_value in (("KW_ALLOW_REMOTE", "false"), ("KW_MODEL_OFFLINE", "true")):
        monkeypatch.setenv(variable, closed_value)
        with pytest.raises(ProviderUnavailable, match="gate is closed"):
            RemoteUseApproval(True, True, True, None).require_open()
        monkeypatch.setenv("KW_ALLOW_REMOTE", "true")
        monkeypatch.setenv("KW_MODEL_OFFLINE", "false")


def test_mock_deepseek_uses_separate_cloud_config_and_parses_json(monkeypatch: pytest.MonkeyPatch):
    """This mocked chat response checks the JSON contract only; no DeepSeek call is made."""
    _immediate_thread_call(monkeypatch)
    monkeypatch.setenv("KW_ALLOW_REMOTE", "true")
    monkeypatch.setenv("KW_MODEL_OFFLINE", "false")
    monkeypatch.setenv("KW_LLM_BASE_URL", "https://api.deepseek.example/v1")
    monkeypatch.setenv("KW_LLM_MODEL", "deepseek-chat-alias")
    monkeypatch.setenv("KW_LLM_API_KEY", "unit-test-cloud-placeholder")
    monkeypatch.setenv("KW_EMBEDDING_BASE_URL", "http://127.0.0.1:56334/v1")
    monkeypatch.setenv("KW_EMBEDDING_MODEL", "qwen-local-alias")
    monkeypatch.setenv("KW_LOCAL_EMBEDDING_API_KEY", "unit-test-local-placeholder")
    captured: list[tuple[str, dict[str, Any], str | None]] = []

    def mock_post(url, payload, api_key, timeout):
        captured.append((url, payload, api_key))
        return {
            "id": "mock-response-id",
            "model": "deepseek-resolved-alias",
            "choices": [{"message": {"content": '{"status":"answered","claims":[]}'}}],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 7,
                "total_tokens": 11,
                "prompt_cache_hit_tokens": 2,
                "prompt_cache_miss_tokens": 1,
            },
        }

    monkeypatch.setattr(provider_module, "_post_json", mock_post)
    result = _run(DeepSeekGenerationProvider(_open_remote_approval()).generate(_llm_request()))
    assert result.parsed_json == {"status": "answered", "claims": []}
    assert result.model == "deepseek-chat-alias"
    assert result.revision == "deepseek-resolved-alias"
    assert result.usage == {
        "input_tokens": 4,
        "output_tokens": 7,
        "count_kind": "exact",
        "prompt_tokens": 4,
        "completion_tokens": 7,
        "total_tokens": 11,
        "prompt_cache_hit_tokens": 2,
        "prompt_cache_miss_tokens": 1,
    }
    assert captured[0][0] == "https://api.deepseek.example/v1/chat/completions"
    assert captured[0][2] == "unit-test-cloud-placeholder"
    assert "unit-test-local-placeholder" not in str(captured[0])


def test_mock_deepseek_missing_usage_is_marked_unavailable(monkeypatch: pytest.MonkeyPatch):
    """A mocked response without usage remains valid and does not invent token counts."""
    _immediate_thread_call(monkeypatch)
    monkeypatch.setenv("KW_ALLOW_REMOTE", "true")
    monkeypatch.setenv("KW_MODEL_OFFLINE", "false")
    monkeypatch.setenv("KW_LLM_BASE_URL", "https://api.deepseek.example/v1")
    monkeypatch.setenv("KW_LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("KW_LLM_API_KEY", "unit-test-cloud-placeholder")
    monkeypatch.setattr(
        provider_module,
        "_post_json",
        lambda *args, **kwargs: {
            "model": "deepseek-resolved-alias",
            "choices": [{"message": {"content": '{"status":"answered"}'}}],
        },
    )
    result = _run(DeepSeekGenerationProvider(_open_remote_approval()).generate(_llm_request()))
    assert result.revision == "deepseek-resolved-alias"
    assert result.usage == {"input_tokens": 0, "output_tokens": 0, "count_kind": "unavailable"}


@pytest.mark.parametrize("missing_variable", ["KW_LLM_BASE_URL", "KW_LLM_MODEL", "KW_LLM_API_KEY"])
def test_mock_deepseek_missing_configuration_is_classified_without_transport(
    monkeypatch: pytest.MonkeyPatch,
    missing_variable: str,
):
    _immediate_thread_call(monkeypatch)
    monkeypatch.setenv("KW_ALLOW_REMOTE", "true")
    monkeypatch.setenv("KW_MODEL_OFFLINE", "false")
    monkeypatch.setenv("KW_LLM_BASE_URL", "https://api.deepseek.example/v1?mock-private-query")
    monkeypatch.setenv("KW_LLM_MODEL", "mock-private-model")
    monkeypatch.setenv("KW_LLM_API_KEY", "unit-test-cloud-placeholder")
    monkeypatch.delenv(missing_variable, raising=False)
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("missing DeepSeek configuration must fail before transport")

    monkeypatch.setattr(provider_module, "_post_json", fail_if_called)
    with pytest.raises(ProviderUnavailable) as error:
        _run(DeepSeekGenerationProvider(_open_remote_approval()).generate(_llm_request()))
    assert not called
    assert error.value.category == "configuration_missing"
    assert error.value.retryable is False
    for private_value in ("mock-private-query", "mock-private-model", "unit-test-cloud-placeholder"):
        assert private_value not in str(error.value)


@pytest.mark.parametrize(
    "content",
    [
        "mock-private-response-not-json unit-test-cloud-placeholder",
        "[]",
    ],
)
def test_mock_deepseek_invalid_json_content_is_classified_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
):
    _immediate_thread_call(monkeypatch)
    monkeypatch.setenv("KW_ALLOW_REMOTE", "true")
    monkeypatch.setenv("KW_MODEL_OFFLINE", "false")
    monkeypatch.setenv("KW_LLM_BASE_URL", "https://api.deepseek.example/v1")
    monkeypatch.setenv("KW_LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("KW_LLM_API_KEY", "unit-test-cloud-placeholder")
    monkeypatch.setattr(
        provider_module,
        "_post_json",
        lambda *args, **kwargs: {"choices": [{"message": {"content": content}}]},
    )
    with pytest.raises(ProviderUnavailable) as error:
        _run(DeepSeekGenerationProvider(_open_remote_approval()).generate(_llm_request()))
    assert error.value.category == "response_invalid"
    assert error.value.retryable is False
    assert "mock-private-response-not-json" not in str(error.value)
    assert "unit-test-cloud-placeholder" not in str(error.value)


@pytest.mark.parametrize(
    "usage",
    [
        "mock-private-usage unit-test-cloud-placeholder",
        {"prompt_tokens": -1, "completion_tokens": 2},
        {"prompt_tokens": "mock-private-token-count"},
    ],
)
def test_mock_deepseek_invalid_usage_is_classified_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    usage: Any,
):
    _immediate_thread_call(monkeypatch)
    monkeypatch.setenv("KW_ALLOW_REMOTE", "true")
    monkeypatch.setenv("KW_MODEL_OFFLINE", "false")
    monkeypatch.setenv("KW_LLM_BASE_URL", "https://api.deepseek.example/v1")
    monkeypatch.setenv("KW_LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("KW_LLM_API_KEY", "unit-test-cloud-placeholder")
    monkeypatch.setattr(
        provider_module,
        "_post_json",
        lambda *args, **kwargs: {
            "choices": [{"message": {"content": '{"status":"answered"}'}}],
            "usage": usage,
        },
    )
    with pytest.raises(ProviderUnavailable) as error:
        _run(DeepSeekGenerationProvider(_open_remote_approval()).generate(_llm_request()))
    assert error.value.category == "usage_invalid"
    assert error.value.retryable is False
    for private_value in ("mock-private-usage", "unit-test-cloud-placeholder", "mock-private-token-count"):
        assert private_value not in str(error.value)


def test_mock_deepseek_parse_and_transport_errors_are_sanitized(monkeypatch: pytest.MonkeyPatch):
    """All error responses are mock fixtures; request bodies and tokens stay out of errors."""
    _immediate_thread_call(monkeypatch)
    post_json_impl = provider_module._post_json
    monkeypatch.setenv("KW_ALLOW_REMOTE", "true")
    monkeypatch.setenv("KW_MODEL_OFFLINE", "false")
    monkeypatch.setenv("KW_LLM_BASE_URL", "https://api.deepseek.example/v1")
    monkeypatch.setenv("KW_LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("KW_LLM_API_KEY", "unit-test-cloud-placeholder")
    monkeypatch.setattr(
        provider_module,
        "_post_json",
        lambda *args, **kwargs: {"choices": [{"message": {"content": "mock-private-response-not-json"}}]},
    )
    with pytest.raises(ProviderUnavailable, match="JSON contract") as parse_error:
        _run(DeepSeekGenerationProvider(_open_remote_approval()).generate(_llm_request()))
    assert "mock-private-response-not-json" not in str(parse_error.value)

    monkeypatch.setattr(provider_module, "_post_json", post_json_impl)

    def mock_urlopen_failure(request, timeout):
        raise urllib.error.URLError("mock-private-response unit-test-cloud-placeholder")

    monkeypatch.setattr(provider_module.urllib.request, "urlopen", mock_urlopen_failure)
    with pytest.raises(ProviderUnavailable, match="network request failed") as request_error:
        provider_module._post_json(
            "https://api.deepseek.example/v1/chat/completions?mock-private-query",
            {},
            "unit-test-cloud-placeholder",
            1.0,
        )
    assert "mock-private-response" not in str(request_error.value)
    assert "unit-test-cloud-placeholder" not in str(request_error.value)
    assert "mock-private-query" not in str(request_error.value)
    assert request_error.value.category == "network_error"
    assert request_error.value.retryable is True


@pytest.mark.parametrize(
    ("status", "category", "retryable"),
    [
        (401, "http_auth", False),
        (403, "http_auth", False),
        (408, "http_transient", True),
        (425, "http_transient", True),
        (429, "http_transient", True),
        (503, "http_server_error", True),
        (400, "http_client_error", False),
    ],
)
def test_mock_http_errors_keep_only_safe_classification(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    category: str,
    retryable: bool,
):
    """Synthetic HTTP failures never expose response bodies, headers, URL, or API key."""
    body = io.BytesIO(b"mock-private-body unit-test-cloud-placeholder")
    failure = urllib.error.HTTPError(
        "https://api.deepseek.example/v1/chat/completions?mock-private-query",
        status,
        "mock-private-status-text",
        {"Authorization": "mock-private-header"},
        body,
    )

    def mock_urlopen(request, timeout):
        raise failure

    monkeypatch.setattr(provider_module.urllib.request, "urlopen", mock_urlopen)
    with pytest.raises(ProviderUnavailable) as error:
        provider_module._post_json(
            "https://api.deepseek.example/v1/chat/completions?mock-private-query",
            {},
            "unit-test-cloud-placeholder",
            1.0,
        )
    assert error.value.http_status == status
    assert error.value.category == category
    assert error.value.retryable is retryable
    message = str(error.value)
    for private_value in (
        "mock-private-body",
        "unit-test-cloud-placeholder",
        "mock-private-query",
        "mock-private-header",
        "mock-private-status-text",
    ):
        assert private_value not in message
    assert body.tell() == 0


@pytest.mark.parametrize(
    ("failure", "category"),
    [
        (TimeoutError("mock-private-timeout unit-test-cloud-placeholder"), "timeout"),
        (urllib.error.URLError(TimeoutError("mock-private-urllib-timeout unit-test-cloud-placeholder")), "timeout"),
        (ConnectionError("mock-private-network unit-test-cloud-placeholder"), "network_error"),
    ],
)
def test_mock_network_and_timeout_failures_are_retryable_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    category: str,
):
    def mock_urlopen(request, timeout):
        raise failure

    monkeypatch.setattr(provider_module.urllib.request, "urlopen", mock_urlopen)
    with pytest.raises(ProviderUnavailable) as error:
        provider_module._post_json(
            "https://api.deepseek.example/v1/chat/completions?mock-private-query",
            {},
            "unit-test-cloud-placeholder",
            1.0,
        )
    assert error.value.category == category
    assert error.value.retryable is True
    assert error.value.http_status is None
    for private_value in (
        "mock-private-timeout",
        "mock-private-urllib-timeout",
        "mock-private-network",
        "unit-test-cloud-placeholder",
        "mock-private-query",
    ):
        assert private_value not in str(error.value)


def test_invalid_request_configuration_is_sanitized_and_not_retryable(monkeypatch: pytest.MonkeyPatch):
    def mock_invalid_request(request, timeout, *, local_only):
        raise ValueError(f"invalid URL {request.full_url} mock-private-query")

    monkeypatch.setattr(provider_module, "_open_json_request", mock_invalid_request)
    with pytest.raises(ProviderUnavailable) as error:
        provider_module._post_json(
            "https://api.deepseek.example/v1/path?mock-private-query\n",
            {},
            "unit-test-cloud-placeholder",
            1.0,
        )
    assert error.value.category == "request_invalid"
    assert error.value.retryable is False
    assert error.value.http_status is None
    assert "mock-private-query" not in str(error.value)
    assert "unit-test-cloud-placeholder" not in str(error.value)


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
