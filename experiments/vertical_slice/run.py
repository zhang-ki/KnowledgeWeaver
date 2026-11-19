"""Run the reproducible offline P3 engineering slice; no external API calls."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from experiments.vertical_slice.agent import InvestigationBudget, make_agent
from experiments.vertical_slice.ingest import CORPUS_ROOT, build_corpus
from experiments.vertical_slice.offline_runtime import drive_ready_coroutine
from experiments.vertical_slice.providers import FakeGenerationProvider, RemoteUseApproval
from experiments.vertical_slice.retrieval import BM25Index, InMemoryDenseIndex
from experiments.vertical_slice.storage import write_snapshot


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "results"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def run_offline(output: Path) -> dict[str, Any]:
    corpus = build_corpus(write_manifest=True)
    output.mkdir(parents=True, exist_ok=True)
    snapshot_path = output / "corpus-snapshot.json"
    snapshot_hash = write_snapshot(snapshot_path, corpus)
    bm25 = BM25Index(corpus.chunks)
    dense = InMemoryDenseIndex(corpus.chunks, corpus.generation_id)
    bm25_path = output / "bm25-index.json"
    dense_path = output / "dense-index.json"
    bm25_path.write_text(json.dumps(bm25.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    dense_path.write_text(json.dumps(dense.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    agent = make_agent(corpus)

    cases: dict[str, Any] = {}
    cases["symbol_bm25"] = (await agent.investigate("What type annotates EvidenceRecord.span?")).to_dict()
    cases["cross_source_dense_two_hop"] = (
        await agent.investigate("How does the ingestion pipeline connect Evidence to source spans?")
    ).to_dict()
    cases["no_evidence"] = (await agent.investigate("What color is the admin console?")).to_dict()
    cases["conflict"] = (await agent.investigate("Does the demo lookup cache survive process restart?")).to_dict()
    timeout_agent = make_agent(corpus, FakeGenerationProvider(timeout=True))
    cases["timeout"] = (await timeout_agent.investigate("What type annotates EvidenceRecord.span?")).to_dict()
    cases["budget_stop"] = (
        await agent.investigate(
            "How does the ingestion pipeline connect Evidence to source spans?",
            InvestigationBudget(max_steps=1, max_tool_calls=4, max_llm_calls=8, max_total_tokens=12000),
        )
    ).to_dict()

    if cases["symbol_bm25"]["route_history"] != ["bm25"]:
        raise AssertionError("exact-symbol question did not use BM25 route")
    if cases["cross_source_dense_two_hop"]["route_history"] != ["dense", "graph"]:
        raise AssertionError("cross-source question did not use dense then graph route")
    paths = cases["cross_source_dense_two_hop"]["action_history"][-1].get("returned_path_count", 0)
    if paths < 1 or len(cases["cross_source_dense_two_hop"]["citations"]) < 2:
        raise AssertionError("cross-source question did not return a cited two-hop path")
    if {item["locator"]["kind"] for item in cases["cross_source_dense_two_hop"]["citations"]} != {"pdf", "python"}:
        raise AssertionError("two-hop citations do not include PDF page and Python line locators")
    if cases["no_evidence"]["status"] != "insufficient" or cases["no_evidence"]["claims"]:
        raise AssertionError("no-evidence question produced a claim")
    if cases["conflict"]["status"] != "conflicted" or len(cases["conflict"]["claims"]) < 2:
        raise AssertionError("conflict was not surfaced with both cited sides")
    if cases["timeout"]["stop_reason"] != "deadline_exceeded" or cases["timeout"]["claims"]:
        raise AssertionError("timeout did not stop claim generation")
    if cases["budget_stop"]["status"] != "budget_exhausted" or cases["budget_stop"]["claims"]:
        raise AssertionError("hard budget did not stop before an incomplete path answer")

    manifest_path = CORPUS_ROOT / "manifest.json"
    result = {
        "plan_version": "KW-P0-2026-11-18",
        "declared_plan_hash": "6ef838b43dc6a6a8d1680c8b9503e55fe7a63c6ca9c4238e4942acf8d495d1df",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "pytest": importlib.metadata.version("pytest"),
            "uv_available": shutil.which("uv") is not None,
            "project_python311_available": (Path("verification/environment/python/.venv/Scripts/python.exe").is_file()),
            "lockfile": {
                "path": "uv.lock",
                "declared_sha256": "4c41a9ab7ef2f51b0cf41c6fcfe6a3515fefd6e2a4e588415e7ad7ca605b9a5e",
                "applied": False,
            },
            "run_mode": "offline_fake_provider",
        },
        "corpus": {
            "manifest_path": "experiments/vertical_slice/corpus/manifest.json",
            "manifest_sha256": _sha256(manifest_path),
            "snapshot_path": "experiments/vertical_slice/results/corpus-snapshot.json",
            "snapshot_sha256": snapshot_hash,
            "source_count": len(corpus.documents),
            "chunk_count": len(corpus.chunks),
            "evidence_count": len(corpus.evidence),
            "relation_count": len(corpus.relations),
        },
        "indexes": {
            "bm25_path": "experiments/vertical_slice/results/bm25-index.json",
            "bm25_sha256": _sha256(bm25_path),
            "dense_path": "experiments/vertical_slice/results/dense-index.json",
            "dense_sha256": _sha256(dense_path),
            "dense_provider": "fake-hash-embedding-v1",
            "dense_is_model_evidence": False,
        },
        "live_gates": {
            "postgresql": "WAITING_EXTERNAL: localhost:55432 unreachable; DSN absent in this session",
            "qdrant": "WAITING_EXTERNAL: localhost:56333 unreachable; URL/key absent in this session",
            "qwen_embedding": "WAITING_EXTERNAL: no credential, exact endpoint, external-data approval, or cost cap",
            "deepseek_generation": "WAITING_EXTERNAL: no credential, exact endpoint, external-data approval, or cost cap",
            "remote_call_made": False,
            "remote_use_approval": RemoteUseApproval().__dict__,
        },
        "cases": cases,
        "acceptance": {
            "pdf_and_python_fixture_parsed": True,
            "local_json_evidence_snapshot": True,
            "bm25_dense_routes_differ": True,
            "cross_source_two_hop": True,
            "page_and_line_locators": True,
            "no_evidence_explicit": True,
            "conflict_explicit": True,
            "timeout_stops_claims": True,
            "hard_budget_stop": True,
            "postgres_qdrant_live_persistence": False,
            "real_qwen_embedding": False,
            "real_deepseek_generation": False,
            "currency_cost": None,
            "currency_budget_authorized": False,
        },
    }
    result_path = output / "offline-e2e.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["result_sha256"] = _sha256(result_path)
    print(f"offline_e2e=PASS cases={len(cases)} evidence={len(corpus.evidence)} relations={len(corpus.relations)}")
    print("live_postgres_qdrant=WAITING_EXTERNAL live_qwen_deepseek=WAITING_EXTERNAL")
    print(f"offline_result=experiments/vertical_slice/results/offline-e2e.json sha256={result['result_sha256']}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    drive_ready_coroutine(run_offline(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
