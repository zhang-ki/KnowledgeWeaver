from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

_DEFAULT_API = "http://127.0.0.1:8000"


def _api_base() -> str:
    value = os.environ.get("KW_API_URL", _DEFAULT_API).strip().rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("KW_API_URL must point to a loopback HTTP address")
    return value


def _json_output(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _client() -> httpx.Client:
    return httpx.Client(timeout=30.0, follow_redirects=False, trust_env=False)


def _call(method: str, path: str, **kwargs: Any) -> httpx.Response:
    base = _api_base()
    with _client() as client:
        response = client.request(method, f"{base}{path}", **kwargs)
    if response.is_error:
        try:
            _json_output(response.json())
        except ValueError:
            print(f"Local API returned HTTP {response.status_code}.", file=sys.stderr)
        raise SystemExit(2)
    return response


def _doctor(_: argparse.Namespace) -> int:
    missing = [
        name
        for name in ("KW_DATABASE_URL",)
        if not os.environ.get(name, "").strip()
    ]
    optional = {
        "KW_QDRANT_URL": "KW_QDRANT_API_KEY",
        "KW_EMBEDDING_BASE_URL": "KW_EMBEDDING_MODEL",
        "KW_LOCAL_CHAT_URL": "KW_LOCAL_CHAT_MODEL",
    }
    incomplete = [
        name
        for name, companion in optional.items()
        if bool(os.environ.get(name, "").strip())
        != bool(os.environ.get(companion, "").strip())
    ]
    missing_modules = [
        name
        for name in ("fastapi", "streamlit", "uvicorn", "sqlalchemy", "qdrant_client")
        if importlib.util.find_spec(name) is None
    ]
    result: dict[str, Any] = {
        "api_url": _DEFAULT_API,
        "environment": "local only",
        "external_providers": "disabled",
        "missing_required_settings": missing,
        "incomplete_optional_settings": incomplete,
        "missing_packages": missing_modules,
    }
    try:
        response = _call("GET", "/readyz")
        result["api"] = "reachable"
        result["readiness"] = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        result["api"] = "unavailable"
        result["api_error"] = type(exc).__name__
    _json_output(result)
    ready = (
        result["api"] == "reachable"
        and not missing
        and not incomplete
        and not missing_modules
        and result.get("readiness", {}).get("services", {}).get("postgres") == "healthy"
    )
    return 0 if ready else 2


def _ingest(args: argparse.Namespace) -> int:
    source = _call(
        "POST",
        "/v1/sources",
        json={
            "path": args.path,
            "kind": args.kind,
            "display_name": args.display_name,
            "license": args.license,
        },
    ).json()
    result = _call(
        "POST",
        "/v1/ingestions",
        json={
            "source_id": source["id"],
            "idempotency_key": args.idempotency_key or str(uuid4()),
            "strict": not args.allow_rejected_files,
        },
    ).json()
    _json_output({"source": source, "job": result})
    return 0


def _search(args: argparse.Namespace) -> int:
    response = _call(
        "POST",
        "/v1/retrievals",
        json={
            "query": args.query,
            "channel": args.channel,
            "top_k": args.top_k,
            "source_ids": args.source_id,
        },
    )
    _json_output(response.json())
    return 0


def _investigate(args: argparse.Namespace) -> int:
    submitted = _call(
        "POST",
        "/v1/investigations",
        json={
            "query": args.query,
            "idempotency_key": args.idempotency_key or str(uuid4()),
            "source_ids": args.source_id,
            "policy": args.policy,
            "max_steps": args.max_steps,
            "timeout_seconds": args.timeout,
        },
    ).json()
    if not args.wait:
        _json_output(submitted)
        return 0
    deadline = time.monotonic() + args.timeout + 15
    while time.monotonic() < deadline:
        result = _call("GET", f"/v1/investigations/{submitted['run_id']}").json()
        if result.get("state") != "running":
            _json_output(result)
            return 0 if result.get("state") == "succeeded" else 2
        time.sleep(1)
    _json_output({"run_id": submitted["run_id"], "state": "running", "poll": "kw investigate --run-id"})
    return 2


def _evaluate(_: argparse.Namespace) -> int:
    response = _call("GET", "/v1/experiments")
    _json_output(
        {
            "scope": "verified internal Dev evidence only",
            "formal_test_started": False,
            "evidence": response.json(),
        }
    )
    return 0


def _poll_run(args: argparse.Namespace) -> int:
    result = _call("GET", f"/v1/investigations/{args.run_id}").json()
    _json_output(result)
    return 0 if result.get("state") == "succeeded" else 2


def _serve(args: argparse.Namespace) -> int:
    import asyncio

    import uvicorn

    from knowledge_weaver.api.app import install_windows_selector_policy

    install_windows_selector_policy()
    config = uvicorn.Config(
        "knowledge_weaver.api.app:app",
        host="127.0.0.1",
        port=args.port,
        workers=1,
        log_level="info",
    )
    server = uvicorn.Server(config)
    if os.name == "nt":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(server.serve())
    else:
        server.run()
    return 0


def _ui(args: argparse.Namespace) -> int:
    import subprocess
    from pathlib import Path

    app_path = Path(__file__).resolve().parent / "ui" / "app.py"
    env = dict(os.environ)
    if args.api_url:
        env["KW_API_URL"] = args.api_url
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(app_path),
            "--server.address",
            "127.0.0.1",
            "--server.port",
            str(args.port),
            "--server.headless",
            "true",
        ],
        env=env,
        check=False,
    )
    return completed.returncode


def _mq_bridge(args: argparse.Namespace) -> int:
    from knowledge_weaver.api.mq_bridge import MqBridgeConfig, run_mq_bridge

    cfg = MqBridgeConfig.from_environment()
    if args.api_url:
        cfg = MqBridgeConfig(amqp_url=args.amqp_url or cfg.amqp_url, api_url=args.api_url)
    elif args.amqp_url:
        cfg = MqBridgeConfig(amqp_url=args.amqp_url, api_url=cfg.api_url)
    run_mq_bridge(cfg)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kw", description="KnowledgeWeaver local product CLI")
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="check local runtime and API readiness")
    doctor.set_defaults(handler=_doctor)

    serve = commands.add_parser("serve", help="start the loopback-only API server")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(handler=_serve)

    ui = commands.add_parser("ui", help="start the local Streamlit product UI")
    ui.add_argument("--port", type=int, default=8501)
    ui.add_argument("--api-url")
    ui.set_defaults(handler=_ui)

    mq_bridge = commands.add_parser("mq-bridge", help="run the KnowSphere RabbitMQ kw-external-v1 bridge")
    mq_bridge.add_argument("--amqp-url")
    mq_bridge.add_argument("--api-url")
    mq_bridge.set_defaults(handler=_mq_bridge)

    ingest = commands.add_parser("ingest", help="register and index an approved local folder")
    ingest.add_argument("--path", required=True)
    ingest.add_argument("--kind", choices=("python_repo", "pdf", "docx", "markdown"), required=True)
    ingest.add_argument("--display-name")
    ingest.add_argument("--license")
    ingest.add_argument("--idempotency-key")
    ingest.add_argument("--allow-rejected-files", action="store_true")
    ingest.set_defaults(handler=_ingest)

    search = commands.add_parser("search", help="retrieve generation-scoped evidence")
    search.add_argument("query")
    search.add_argument(
        "--channel",
        choices=("hybrid", "bm25", "dense", "symbol", "entity", "graph"),
        default="hybrid",
    )
    search.add_argument("--top-k", type=int, default=10)
    search.add_argument("--source-id", action="append")
    search.set_defaults(handler=_search)

    investigate = commands.add_parser("investigate", help="run bounded evidence-grounded investigation")
    investigate.add_argument("query", nargs="?")
    investigate.add_argument("--run-id")
    investigate.add_argument("--idempotency-key")
    investigate.add_argument("--source-id", action="append")
    investigate.add_argument("--policy", choices=("fixed", "rule"), default="fixed")
    investigate.add_argument("--max-steps", type=int, default=6)
    investigate.add_argument("--timeout", type=int, default=300)
    investigate.add_argument("--no-wait", dest="wait", action="store_false")
    investigate.set_defaults(handler=_investigate)

    poll = commands.add_parser("investigation-status", help="read a prior investigation run")
    poll.add_argument("run_id")
    poll.set_defaults(handler=_poll_run)

    evaluate = commands.add_parser("evaluate", help="show verified local internal evidence")
    evaluate.set_defaults(handler=_evaluate)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "investigate" and args.run_id:
        exit_code = _poll_run(argparse.Namespace(run_id=args.run_id))
    elif args.command == "investigate" and not args.query:
        parser.error("investigate requires a query unless --run-id is provided")
    else:
        handler = args.handler
        exit_code = handler(args)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
