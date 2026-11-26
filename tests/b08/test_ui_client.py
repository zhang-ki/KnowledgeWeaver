"""Tests for KnowledgeWeaver B08 Streamlit UI API client and graph helpers."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from knowledge_weaver.ui.app import ApiError, KwApiClient, build_graph_dot


def test_kw_api_client_roundtrip_and_error_handling() -> None:
    seen: list[tuple[str, str, dict[str, Any] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        seen.append((request.method, request.url.path, body))
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/readyz":
            return httpx.Response(200, json={"status": "ready", "services": {"postgres": "healthy"}})
        if request.url.path == "/v1/sources" and request.method == "GET":
            return httpx.Response(200, json=[{"id": "s1", "display_name": "Repo", "source_kind": "python_repo"}])
        if request.url.path == "/v1/investigations" and request.method == "POST":
            assert body is not None
            assert body["policy"] == "fixed"
            assert body["max_steps"] == 4
            return httpx.Response(202, json={"id": "run-1", "state": "queued"})
        if request.url.path == "/v1/graphs":
            return httpx.Response(200, json={"channel": "graph", "generation_id": "g1", "result": {"paths": []}})
        if request.url.path == "/v1/bad":
            return httpx.Response(
                400,
                json={"error": {"code": "VALIDATION_ERROR", "message": "Invalid input", "request_id": "req-1"}},
            )
        return httpx.Response(404, json={"error": {"code": "MISSING_REFERENCE", "message": "Not found"}})

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url="http://127.0.0.1:8000", transport=transport)
    client = KwApiClient(client=http_client)

    assert client.healthz()["status"] == "ok"
    assert client.readyz()["status"] == "ready"
    assert client.list_sources()[0]["id"] == "s1"
    inv = client.submit_investigation(query="test", policy="fixed", max_steps=4, idempotency_key="k1")
    assert inv["id"] == "run-1"
    assert client.get_graph(query="q", source_ids=["s1"])["channel"] == "graph"

    with pytest.raises(ApiError) as exc_info:
        client._request("GET", "/v1/bad")
    assert exc_info.value.code == "VALIDATION_ERROR"
    assert exc_info.value.request_id == "req-1"


def test_build_graph_dot() -> None:
    assert build_graph_dot([]) is None
    dot = build_graph_dot(
        [
            {
                "edges": [
                    {
                        "source_name": "PyJWT.decode_complete",
                        "predicate": "calls",
                        "target_name": "PyJWS.decode_complete",
                    }
                ]
            }
        ]
    )
    assert dot is not None
    assert '"PyJWT.decode_complete" -> "PyJWS.decode_complete" [label="calls"];' in dot
