"""Unit and route tests for KnowledgeWeaver B08 FastAPI app, config, and CLI."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from knowledge_weaver import cli
from knowledge_weaver.api.app import create_app
from knowledge_weaver.api.config import AppConfig
from knowledge_weaver.api.models import ExperimentEvidence, JobView, SourceView
from knowledge_weaver.api.runtime import validate_source_path
from knowledge_weaver.core.errors import ErrorCode, KnowledgeWeaverError
from knowledge_weaver.ingestion.paths import PathSafetyError

if os.name == "nt" and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


class _FakeProduct:
    """In-memory stub of LocalProduct for isolated API contract testing."""

    def __init__(self) -> None:
        self.started = False
        self.closed = False
        self.sources: dict[str, SourceView] = {}
        self.jobs: dict[str, JobView] = {}

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def capabilities(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "services": {
                "postgres": "healthy",
                "qdrant": "healthy",
                "local_embedding": "configured",
                "local_chat": "configured",
                "external_providers": "disabled",
                "policy": "fixed; no trained SEGR weights loaded",
            },
            "can_answer_with_local_model": True,
        }

    async def register_source(self, body: Any) -> SourceView:
        source_id = "11111111-1111-1111-1111-111111111111"
        view = SourceView(
            id=source_id,
            corpus_id="local-default",
            kind=body.kind,
            display_name=body.display_name or "Test Source",
            indexed=False,
        )
        self.sources[source_id] = view
        return view

    async def list_sources(self) -> list[SourceView]:
        return list(self.sources.values())

    async def submit_ingestion(self, body: Any) -> JobView:
        if body.source_id not in self.sources:
            raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "Source not found.")
        job = JobView(
            job_id="22222222-2222-2222-2222-222222222222",
            operation="ingestion",
            state="succeeded",
            result={"chunks": 4},
        )
        self.jobs[job.job_id] = job
        return job

    async def get_job(self, job_id: str) -> JobView:
        if job_id not in self.jobs:
            raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "Job not found.")
        return self.jobs[job_id]

    async def submit_delete(self, source_id: str, body: Any) -> JobView:
        job = JobView(
            job_id="44444444-4444-4444-4444-444444444444",
            operation="delete",
            state="succeeded",
            result={"deleted": True},
        )
        self.jobs[job.job_id] = job
        return job

    async def retrieve(self, body: Any) -> dict[str, Any]:
        return {
            "channel": body.channel,
            "generation_id": "33333333-3333-3333-3333-333333333333",
            "result": {
                "hits": [
                    {
                        "chunk_id": "55555555-5555-5555-5555-555555555555",
                        "score": 0.91,
                        "text": "Token validation uses PyJWT.",
                        "evidence_ids": ["66666666-6666-6666-6666-666666666666"],
                    }
                ],
                "paths": [],
            },
        }

    async def submit_investigation(self, body: Any) -> JobView:
        job = JobView(
            job_id="77777777-7777-7777-7777-777777777777",
            operation="investigation",
            state="succeeded",
            result={
                "investigation_id": "88888888-8888-8888-8888-888888888888",
                "status": "answered",
                "answer": {
                    "id": "99999999-9999-9999-9999-999999999999",
                    "investigation_id": "88888888-8888-8888-8888-888888888888",
                    "context": {"generation_id": "33333333-3333-3333-3333-333333333333"},
                    "status": "answered",
                    "claims": [
                        {
                            "text": "Token validation uses PyJWT.",
                            "evidence_ids": ["66666666-6666-6666-6666-666666666666"],
                            "relation_ids": [],
                        }
                    ],
                    "unanswered": [],
                },
                "trace": [{"step": 1, "action": "hybrid"}],
                "policy_profile": body.policy,
                "training": "none; fixed/rule policy only",
            },
        )
        self.jobs[job.job_id] = job
        return job

    async def get_investigation(self, run_id: str) -> dict[str, Any]:
        job = await self.get_job(run_id)
        return {"run_id": run_id, "state": job.state, **(job.result or {})}

    async def evidence(self, evidence_id: str, corpus_id: str | None, generation_id: str) -> dict[str, Any]:
        return {
            "evidence_id": evidence_id,
            "generation_id": generation_id,
            "source_id": "11111111-1111-1111-1111-111111111111",
            "source_name": "Test Source",
            "relative_path": "auth.py",
            "locator": {"path": "auth.py", "line_start": 10, "line_end": 12},
            "quote": "def validate_token(): ...",
            "chunk_id": "55555555-5555-5555-5555-555555555555",
        }

    async def graph(self, body: Any) -> dict[str, Any]:
        return {
            "channel": "graph",
            "generation_id": "33333333-3333-3333-3333-333333333333",
            "result": {
                "hits": [],
                "paths": [
                    {
                        "edges": [
                            {
                                "source_name": "PyJWT.decode_complete",
                                "predicate": "calls",
                                "target_name": "PyJWS.decode_complete",
                            }
                        ]
                    }
                ],
            },
        }

    @staticmethod
    def experiments() -> list[ExperimentEvidence]:
        from knowledge_weaver.api.runtime import LocalProduct

        return LocalProduct.experiments()


def test_app_config_rejects_non_loopback_urls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("KW_DATABASE_URL", "postgresql+asyncpg://kw:pw@10.0.0.5:5432/kw")
    monkeypatch.setenv("KW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KW_ALLOWED_ROOTS", str(allowed))
    with pytest.raises(ValueError, match="loopback"):
        AppConfig.from_environment()


def test_validate_source_path_blocks_escape(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(PathSafetyError):
        validate_source_path(str(outside), (allowed,))


def test_fastapi_routes_and_error_envelopes() -> None:
    async def _run() -> None:
        product = _FakeProduct()
        app = create_app(product=product)  # type: ignore[arg-type]
        app.state.product = product
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            health = await client.get("/healthz")
            assert health.status_code == 200
            assert health.headers["X-Request-ID"]

            ready = await client.get("/readyz")
            assert ready.status_code == 200
            assert ready.json()["can_answer_with_local_model"] is True

            created = await client.post(
                "/v1/sources",
                json={"path": "D:/fixtures/repo", "kind": "python_repo", "display_name": "Repo"},
            )
            assert created.status_code == 201
            source_id = created.json()["id"]

            listed = await client.get("/v1/sources")
            assert listed.status_code == 200
            assert len(listed.json()) == 1

            ingested = await client.post(
                "/v1/ingestions",
                json={"source_id": source_id, "idempotency_key": "ingest-1"},
            )
            assert ingested.status_code == 202
            job_id = ingested.json()["job_id"]

            job = await client.get(f"/v1/jobs/{job_id}")
            assert job.status_code == 200
            assert job.json()["state"] == "succeeded"

            search = await client.post(
                "/v1/retrievals",
                json={"query": "token validation", "channel": "hybrid", "top_k": 5},
            )
            assert search.status_code == 200
            assert search.json()["channel"] == "hybrid"

            inv = await client.post(
                "/v1/investigations",
                json={"query": "How does token validation work?", "idempotency_key": "inv-1"},
            )
            assert inv.status_code == 202
            run_id = inv.json()["run_id"]

            inv_res = await client.get(f"/v1/investigations/{run_id}")
            assert inv_res.status_code == 200
            assert inv_res.json()["answer"]["status"] == "answered"

            ev = await client.get(
                "/v1/evidence/66666666-6666-6666-6666-666666666666",
                params={"generation_id": "33333333-3333-3333-3333-333333333333"},
            )
            assert ev.status_code == 200
            assert ev.json()["relative_path"] == "auth.py"

            graph = await client.get("/v1/graphs", params={"query": "decode_complete"})
            assert graph.status_code == 200
            assert len(graph.json()["result"]["paths"]) == 1

            exps = await client.get("/v1/experiments")
            assert exps.status_code == 200
            assert exps.json()[0]["status"] == "PASS_INTERNAL_SYNTHETIC_ONLY"

            bad = await client.post("/v1/investigations", json={"query": "q", "unknown_field": 1})
            assert bad.status_code == 422
            assert bad.json()["code"] == "SCHEMA_INVALID"

            deleted = await client.request(
                "DELETE",
                f"/v1/sources/{source_id}",
                json={"idempotency_key": "del-1"},
            )
            assert deleted.status_code == 202

    proactor = getattr(asyncio, "ProactorEventLoop", None)
    if os.name == "nt" and proactor is not None:
        with asyncio.Runner(loop_factory=proactor) as runner:
            runner.run(_run())
    else:
        asyncio.run(_run())


def test_cli_parser_and_evaluate_output(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["ui", "--port", "8502", "--api-url", "http://127.0.0.1:8001"])
    assert args.command == "ui"
    assert args.port == 8502
    assert args.api_url == "http://127.0.0.1:8001"

    def _fake_call(method: str, path: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"evidence_set": "B07 internal Dev", "status": "PASS_INTERNAL_SYNTHETIC_ONLY"}],
            request=httpx.Request(method, f"http://127.0.0.1:8000{path}"),
        )

    monkeypatch.setattr(cli, "_call", _fake_call)
    rc = cli._evaluate(parser.parse_args(["evaluate"]))
    assert rc == 0
    captured = json.loads(capsys.readouterr().out)
    assert captured["formal_test_started"] is False
