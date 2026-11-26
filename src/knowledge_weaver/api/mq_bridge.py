"""RabbitMQ provider bridge implementing the kw-external-v1 contract with KnowSphere."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from knowledge_weaver.ui.app import ApiError, KwApiClient

CONTRACT_VERSION = "kw-external-v1"
DEFAULT_EXCHANGE = "knowsphere.agent.exchange"
DEFAULT_COMMAND_QUEUE = "kw.commands"
DEFAULT_COMMAND_BINDING = "kw.command.#"
DEFAULT_EVENT_QUEUE = "knowsphere.agent.events"
DEFAULT_EVENT_BINDING = "kw.event.#"
DEFAULT_DLX = "knowsphere.agent.dlx"
DEFAULT_DLQ = "knowsphere.agent.dlq"


def corpus_id_for_space(space_id: str) -> str:
    """Map a KnowSphere spaceId 1:1 to a valid lowercase UUID KnowledgeWeaver corpus_id."""
    cleaned = space_id.strip()
    if not cleaned:
        raise ValueError("spaceId must be non-empty")
    try:
        return str(UUID(cleaned))
    except ValueError:
        return str(uuid5(NAMESPACE_URL, f"knowsphere:space:{cleaned}"))


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_command_envelope(raw_body: bytes | str) -> dict[str, Any]:
    """Parse and validate a KnowSphere PlatformMessageEnvelope JSON payload."""
    text = raw_body.decode("utf-8") if isinstance(raw_body, bytes) else raw_body
    envelope = json.loads(text)
    if not isinstance(envelope, dict):
        raise TypeError("Command envelope must be a JSON object")
    for required in ("messageId", "taskId", "spaceId"):
        if not envelope.get(required):
            raise ValueError(f"Command envelope missing required field: {required}")
    opaque_raw = envelope.get("opaquePayloadJson")
    opaque: dict[str, Any] = {}
    if isinstance(opaque_raw, str) and opaque_raw.strip():
        parsed = json.loads(opaque_raw)
        if isinstance(parsed, dict):
            opaque = parsed
    elif isinstance(opaque_raw, dict):
        opaque = opaque_raw
    return {
        "message_id": str(envelope["messageId"]),
        "task_id": str(envelope["taskId"]),
        "space_id": str(envelope["spaceId"]),
        "corpus_id": corpus_id_for_space(str(envelope["spaceId"])),
        "trace_id": str(envelope.get("traceId") or envelope["messageId"]),
        "logical_type": str(envelope.get("logicalType") or "TASK_DISPATCHED"),
        "payload": opaque,
    }


def build_running_event(*, task_id: str, trace_id: str, command_message_id: str) -> dict[str, Any]:
    """Build a kw-external-v1 RUNNING InboundAgentMessage."""
    return {
        "messageId": f"kw-evt:{command_message_id}:running",
        "taskId": task_id,
        "kind": "RUNNING",
        "traceId": trace_id,
        "resultDraft": None,
        "errorCode": None,
        "errorMessage": None,
        "retryable": False,
        "occurredAt": _utc_now_iso(),
    }


def build_failed_event(
    *,
    task_id: str,
    trace_id: str,
    command_message_id: str,
    error_code: str,
    error_message: str,
    retryable: bool,
) -> dict[str, Any]:
    """Build a kw-external-v1 FAILED InboundAgentMessage."""
    return {
        "messageId": f"kw-evt:{command_message_id}:failed",
        "taskId": task_id,
        "kind": "FAILED",
        "traceId": trace_id,
        "resultDraft": None,
        "errorCode": error_code,
        "errorMessage": error_message[:500],
        "retryable": retryable,
        "occurredAt": _utc_now_iso(),
    }


def _status_detail(answer_status: str | None) -> str:
    normalized = (answer_status or "").lower()
    if normalized == "answered":
        return "ANSWERED"
    if normalized == "conflicted":
        return "CONFLICTED"
    return "INSUFFICIENT"


def _format_locator_hint(locator: dict[str, Any] | None, relative_path: str | None) -> str | None:
    if not locator and not relative_path:
        return None
    path = relative_path or (locator.get("path") if locator else None) or "source"
    if locator and "line_start" in locator and "line_end" in locator:
        return f"{path}#L{locator['line_start']}-L{locator['line_end']}"
    if locator and "page" in locator:
        return f"{path}#page={locator['page']}"
    return str(path)


def build_completed_event(
    *,
    task_id: str,
    trace_id: str,
    command_message_id: str,
    investigation: dict[str, Any],
    evidences: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build a kw-external-v1 COMPLETED InboundAgentMessage from FastAPI investigation + evidence."""
    answer = investigation.get("answer") or {}
    claims = answer.get("claims") or []
    summary_parts = [str(c.get("text", "")).strip() for c in claims if c.get("text")]
    summary = "\n".join(summary_parts) if summary_parts else "No grounded claims found for the query."

    references: list[dict[str, Any]] = []
    seen_ev_ids: set[str] = set()
    gen_id = (answer.get("context") or {}).get("generation_id") or ""
    for claim in claims:
        for ev_id in claim.get("evidence_ids") or []:
            if ev_id in seen_ev_ids:
                continue
            seen_ev_ids.add(ev_id)
            ev = evidences.get(ev_id, {})
            references.append(
                {
                    "id": str(uuid5(NAMESPACE_URL, f"kw:ref:{task_id}:{ev_id}")),
                    "referenceType": "EVIDENCE_SUMMARY",
                    "knowledgeBaseId": None,
                    "resourceId": None,
                    "resourceVersionId": None,
                    "displayLabel": ev.get("source_name") or "KnowledgeWeaver Source",
                    "excerptSummary": (ev.get("quote") or "")[:400] or None,
                    "locatorHint": _format_locator_hint(ev.get("locator"), ev.get("relative_path")),
                    "opaqueRef": f"kw:evidence:{gen_id}:{ev_id}",
                    "authorizationRequired": True,
                }
            )

    return {
        "messageId": f"kw-evt:{command_message_id}:completed",
        "taskId": task_id,
        "kind": "COMPLETED",
        "traceId": trace_id,
        "resultDraft": {
            "id": str(uuid5(NAMESPACE_URL, f"kw:result:{task_id}:{command_message_id}")),
            "resultType": "ANSWER_SUMMARY",
            "title": "KnowledgeWeaver Investigation",
            "summary": summary,
            "statusDetail": _status_detail(answer.get("status")),
            "unansweredItems": list(answer.get("unanswered") or []),
            "references": references,
            "storageRef": None,
            "executionMode": "KNOWLEDGEWEAVER",
            "isMockResult": False,
            "agentContractVersion": CONTRACT_VERSION,
            "createdAt": _utc_now_iso(),
        },
        "errorCode": None,
        "errorMessage": None,
        "retryable": False,
        "occurredAt": _utc_now_iso(),
    }


@dataclass(frozen=True)
class MqBridgeConfig:
    amqp_url: str
    api_url: str = "http://127.0.0.1:8000"
    exchange: str = DEFAULT_EXCHANGE
    command_queue: str = DEFAULT_COMMAND_QUEUE
    command_binding: str = DEFAULT_COMMAND_BINDING
    event_queue: str = DEFAULT_EVENT_QUEUE
    event_binding: str = DEFAULT_EVENT_BINDING
    dlx: str = DEFAULT_DLX
    dlq: str = DEFAULT_DLQ
    poll_timeout_seconds: int = 120

    @classmethod
    def from_environment(cls) -> MqBridgeConfig:
        amqp_url = os.environ.get(
            "KW_RABBITMQ_URL",
            "amqp://knowsphere:knowsphere_local_dev@127.0.0.1:5672/%2F",
        )
        return cls(
            amqp_url=amqp_url,
            api_url=os.environ.get("KW_API_URL", "http://127.0.0.1:8000"),
            exchange=os.environ.get("KW_MQ_EXCHANGE", DEFAULT_EXCHANGE),
            command_queue=os.environ.get("KW_MQ_COMMAND_QUEUE", DEFAULT_COMMAND_QUEUE),
            event_queue=os.environ.get("KW_MQ_EVENT_QUEUE", DEFAULT_EVENT_QUEUE),
        )


class KnowledgeWeaverMqBridge:
    """Bridges KnowSphere RabbitMQ commands to KnowledgeWeaver FastAPI /v1 endpoints."""

    def __init__(self, api_client: KwApiClient, *, poll_timeout_seconds: int = 120) -> None:
        self.api = api_client
        self.poll_timeout_seconds = poll_timeout_seconds

    def process_command(
        self,
        raw_body: bytes | str,
        *,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Execute a single command envelope and return [(routing_key, event_dict), ...]."""
        cmd = parse_command_envelope(raw_body)
        events: list[tuple[str, dict[str, Any]]] = []

        def _emit(routing_key: str, evt: dict[str, Any]) -> None:
            events.append((routing_key, evt))
            if on_event is not None:
                on_event(routing_key, evt)

        running_event = build_running_event(
            task_id=cmd["task_id"],
            trace_id=cmd["trace_id"],
            command_message_id=cmd["message_id"],
        )

        payload = cmd["payload"]
        query = str(payload.get("query") or payload.get("question") or "").strip()
        if not query:
            _emit("kw.event.task.running", running_event)
            _emit(
                "kw.event.task.failed",
                build_failed_event(
                    task_id=cmd["task_id"],
                    trace_id=cmd["trace_id"],
                    command_message_id=cmd["message_id"],
                    error_code="INVALID_INPUT",
                    error_message="Command payload missing query/question text.",
                    retryable=False,
                ),
            )
            return events

        source_ids = payload.get("sourceIds") or payload.get("source_ids")
        policy = str(payload.get("policy") or "fixed")
        max_steps = int(payload.get("maxSteps") or payload.get("max_steps") or 6)

        try:
            submitted = self.api._request_dict(
                "POST",
                "/v1/investigations",
                json={
                    "query": query,
                    "corpus_id": cmd["corpus_id"],
                    "source_ids": source_ids if isinstance(source_ids, list) and source_ids else None,
                    "policy": policy if policy in {"fixed", "rule"} else "fixed",
                    "max_steps": max(1, min(6, max_steps)),
                    "idempotency_key": f"mq:{cmd['message_id']}",
                },
            )
            _emit("kw.event.task.running", running_event)
            run_id = str(submitted["run_id"])
            deadline = time.monotonic() + self.poll_timeout_seconds
            inv_state: dict[str, Any] = {}
            while time.monotonic() < deadline:
                inv_state = self.api.get_investigation(run_id)
                if inv_state.get("state") in {"succeeded", "failed"}:
                    break
                time.sleep(0.25)

            if inv_state.get("state") != "succeeded":
                _emit(
                    "kw.event.task.failed",
                    build_failed_event(
                        task_id=cmd["task_id"],
                        trace_id=cmd["trace_id"],
                        command_message_id=cmd["message_id"],
                        error_code="INVESTIGATION_FAILED",
                        error_message=str(inv_state.get("error") or "Investigation did not succeed."),
                        retryable=False,
                    ),
                )
                return events

            answer = inv_state.get("answer") or {}
            gen_id = (answer.get("context") or {}).get("generation_id")
            evidences: dict[str, dict[str, Any]] = {}
            if gen_id:
                for claim in answer.get("claims") or []:
                    for ev_id in claim.get("evidence_ids") or []:
                        if ev_id not in evidences:
                            try:
                                evidences[ev_id] = self.api.get_evidence(
                                    evidence_id=ev_id,
                                    generation_id=gen_id,
                                )
                            except ApiError:
                                pass

            _emit(
                "kw.event.task.completed",
                build_completed_event(
                    task_id=cmd["task_id"],
                    trace_id=cmd["trace_id"],
                    command_message_id=cmd["message_id"],
                    investigation=inv_state,
                    evidences=evidences,
                ),
            )
            return events
        except ApiError as exc:
            if not events:
                _emit("kw.event.task.running", running_event)
            _emit(
                "kw.event.task.failed",
                build_failed_event(
                    task_id=cmd["task_id"],
                    trace_id=cmd["trace_id"],
                    command_message_id=cmd["message_id"],
                    error_code=exc.code,
                    error_message=exc.message,
                    retryable=exc.status_code >= 500 or exc.status_code == 0,
                ),
            )
            return events


def run_mq_bridge(config: MqBridgeConfig) -> None:
    """Connect to RabbitMQ and consume KnowSphere commands until interrupted."""
    import pika

    api_client = KwApiClient(base_url=config.api_url, timeout=30.0)
    bridge = KnowledgeWeaverMqBridge(api_client, poll_timeout_seconds=config.poll_timeout_seconds)

    params = pika.URLParameters(config.amqp_url)
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    channel.confirm_delivery()

    channel.exchange_declare(exchange=config.dlx, exchange_type="topic", durable=True)
    channel.queue_declare(queue=config.dlq, durable=True)
    channel.queue_bind(queue=config.dlq, exchange=config.dlx, routing_key="#")

    channel.exchange_declare(exchange=config.exchange, exchange_type="topic", durable=True)
    channel.queue_declare(
        queue=config.command_queue,
        durable=True,
        arguments={"x-dead-letter-exchange": config.dlx},
    )
    channel.queue_bind(queue=config.command_queue, exchange=config.exchange, routing_key=config.command_binding)

    channel.queue_declare(
        queue=config.event_queue,
        durable=True,
        arguments={"x-dead-letter-exchange": config.dlx},
    )
    channel.queue_bind(queue=config.event_queue, exchange=config.exchange, routing_key=config.event_binding)

    def _on_message(ch: Any, method: Any, properties: Any, body: bytes) -> None:
        def _publish(routing_key: str, evt: dict[str, Any]) -> None:
            ch.basic_publish(
                exchange=config.exchange,
                routing_key=routing_key,
                body=json.dumps(evt).encode("utf-8"),
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=2,
                    message_id=evt["messageId"],
                    correlation_id=str(uuid4()),
                ),
                mandatory=False,
            )

        try:
            bridge.process_command(body, on_event=_publish)
            ch.basic_ack(delivery_tag=method.delivery_tag)
        except (ValueError, TypeError, json.JSONDecodeError, KeyError):
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=config.command_queue, on_message_callback=_on_message)
    try:
        channel.start_consuming()
    finally:
        api_client.close()
        if connection.is_open:
            connection.close()
