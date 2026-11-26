"""End-to-end loopback HTTP smoke test for KnowledgeWeaver B08 FastAPI + UI client."""

from __future__ import annotations

import socket
import threading
import time
from contextlib import closing

import httpx
import uvicorn

from knowledge_weaver.api.app import create_app
from knowledge_weaver.ui.app import KwApiClient
from tests.b08.test_api_and_cli import _FakeProduct


def _free_loopback_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_b08_loopback_http_e2e_roundtrip() -> None:
    port = _free_loopback_port()
    product = _FakeProduct()
    app = create_app(product=product)  # type: ignore[arg-type]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                if httpx.get(f"{base_url}/healthz", timeout=0.5, trust_env=False).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise AssertionError("Loopback FastAPI server failed to become healthy")

        client = KwApiClient(base_url=base_url)
        try:
            assert client.readyz()["status"] == "ready"
            src = client.register_source(path="D:/fixtures/repo", kind="python_repo", display_name="Repo")
            job = client.submit_ingestion(source_id=src["id"], idempotency_key="e2e-ingest")
            assert client.get_job(job["job_id"])["state"] == "succeeded"
            search = client.retrieve(query="token", channel="hybrid", top_k=5)
            assert len(search["result"]["hits"]) == 1
            inv = client.submit_investigation(query="How does token validation work?", idempotency_key="e2e-inv")
            inv_res = client.get_investigation(inv["run_id"])
            assert inv_res["answer"]["status"] == "answered"
            ev_id = inv_res["answer"]["claims"][0]["evidence_ids"][0]
            gen_id = inv_res["answer"]["context"]["generation_id"]
            ev = client.get_evidence(evidence_id=ev_id, generation_id=gen_id)
            assert ev["relative_path"] == "auth.py"
            graph = client.get_graph(query="decode_complete")
            assert len(graph["result"]["paths"]) == 1
            exps = client.list_experiments()
            assert exps[0]["status"] == "PASS_INTERNAL_SYNTHETIC_ONLY"
        finally:
            client.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
