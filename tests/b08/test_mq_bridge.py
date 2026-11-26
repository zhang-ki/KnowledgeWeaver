"""Unit tests for KnowledgeWeaver RabbitMQ kw-external-v1 bridge."""

from __future__ import annotations

import json
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from knowledge_weaver.api.config import _corpus_id
from knowledge_weaver.api.mq_bridge import (
    CONTRACT_VERSION,
    KnowledgeWeaverMqBridge,
    corpus_id_for_space,
    parse_command_envelope,
)
from knowledge_weaver.ui.app import KwApiClient


def test_space_id_maps_to_isolated_corpus_id() -> None:
    space_uuid = "02F86E70-2D2E-4142-9389-3C1AC0B9BD86"
    mapped_uuid = corpus_id_for_space(space_uuid)
    assert mapped_uuid == "02f86e70-2d2e-4142-9389-3c1ac0b9bd86"
    assert _corpus_id(mapped_uuid) == mapped_uuid

    mapped_non_uuid = corpus_id_for_space("space-123")
    assert mapped_non_uuid == str(uuid5(NAMESPACE_URL, "knowsphere:space:space-123"))
    assert str(UUID(mapped_non_uuid)) == mapped_non_uuid
    assert _corpus_id(mapped_non_uuid) == mapped_non_uuid


def test_mq_bridge_translates_command_to_running_and_completed_events() -> None:
    expected_corpus_id = corpus_id_for_space("sp-1")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/investigations" and request.method == "POST":
            body = json.loads(request.content.decode())
            assert body["corpus_id"] == expected_corpus_id
            assert body["idempotency_key"] == "mq:outbox:msg-1"
            return httpx.Response(202, json={"run_id": "run-1", "state": "queued"})
        if request.url.path == "/v1/investigations/run-1":
            return httpx.Response(
                200,
                json={
                    "run_id": "run-1",
                    "state": "succeeded",
                    "answer": {
                        "id": "ans-1",
                        "investigation_id": "inv-1",
                        "context": {"generation_id": "gen-1"},
                        "status": "answered",
                        "claims": [
                            {
                                "text": "PyJWT verifies token signatures.",
                                "evidence_ids": ["ev-1"],
                                "relation_ids": [],
                            }
                        ],
                        "unanswered": [],
                    },
                },
            )
        if request.url.path == "/v1/evidence/ev-1":
            return httpx.Response(
                200,
                json={
                    "evidence_id": "ev-1",
                    "generation_id": "gen-1",
                    "source_name": "PyJWT Docs",
                    "relative_path": "jwt/api_jwt.py",
                    "locator": {"path": "jwt/api_jwt.py", "line_start": 42, "line_end": 50},
                    "quote": "def decode(...):",
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api = KwApiClient(client=httpx.Client(base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(handler)))
    bridge = KnowledgeWeaverMqBridge(api)

    envelope = json.dumps(
        {
            "schemaVersion": "ks-platform-msg-v1",
            "contractStatus": "CONFIRMED_BOTH",
            "messageId": "outbox:msg-1",
            "taskId": "11111111-1111-1111-1111-111111111111",
            "spaceId": "sp-1",
            "logicalType": "TASK_DISPATCHED",
            "traceId": "trace-1",
            "opaquePayloadJson": json.dumps({"query": "How does PyJWT verify signatures?"}),
        }
    )

    parsed = parse_command_envelope(envelope)
    assert parsed["corpus_id"] == expected_corpus_id

    events: list[tuple[str, dict[str, Any]]] = bridge.process_command(envelope)
    assert len(events) == 2
    assert events[0][0] == "kw.event.task.running"
    assert events[0][1]["kind"] == "RUNNING"

    key, completed = events[1]
    assert key == "kw.event.task.completed"
    assert completed["kind"] == "COMPLETED"
    draft = completed["resultDraft"]
    assert draft["executionMode"] == "KNOWLEDGEWEAVER"
    assert draft["resultType"] == "ANSWER_SUMMARY"
    assert draft["statusDetail"] == "ANSWERED"
    assert draft["agentContractVersion"] == CONTRACT_VERSION
    assert draft["references"][0]["referenceType"] == "EVIDENCE_SUMMARY"
    assert draft["references"][0]["locatorHint"] == "jwt/api_jwt.py#L42-L50"
    assert draft["references"][0]["opaqueRef"] == "kw:evidence:gen-1:ev-1"


def test_mq_bridge_emits_running_before_polling_completes() -> None:
    observed_order: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/investigations" and request.method == "POST":
            observed_order.append("post")
            return httpx.Response(202, json={"run_id": "run-2", "state": "queued"})
        if request.url.path == "/v1/investigations/run-2":
            observed_order.append("poll")
            return httpx.Response(
                200,
                json={
                    "run_id": "run-2",
                    "state": "succeeded",
                    "answer": {
                        "id": "ans-2",
                        "investigation_id": "inv-2",
                        "context": {"generation_id": "gen-2"},
                        "status": "insufficient",
                        "claims": [],
                        "unanswered": ["q"],
                    },
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api = KwApiClient(client=httpx.Client(base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(handler)))
    bridge = KnowledgeWeaverMqBridge(api)
    envelope = json.dumps(
        {
            "schemaVersion": "ks-platform-msg-v1",
            "contractStatus": "CONFIRMED_BOTH",
            "messageId": "outbox:msg-2",
            "taskId": "22222222-2222-2222-2222-222222222222",
            "spaceId": "sp-2",
            "logicalType": "TASK_DISPATCHED",
            "traceId": "trace-2",
            "opaquePayloadJson": json.dumps({"query": "Check status"}),
        }
    )

    bridge.process_command(envelope, on_event=lambda rk, _evt: observed_order.append(rk))
    assert observed_order == ["post", "kw.event.task.running", "poll", "kw.event.task.completed"]
