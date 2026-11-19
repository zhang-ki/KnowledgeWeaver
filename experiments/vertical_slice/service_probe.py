"""Optional local PG/Qdrant persistence probe for a P3-owned schema/collection."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from experiments.vertical_slice.ingest import build_corpus
from experiments.vertical_slice.providers import FakeEmbeddingProvider, ProviderUnavailable
from experiments.vertical_slice.retrieval import QdrantCandidateIndex
from experiments.vertical_slice.storage import (
    PostgresEvidenceStore,
    QdrantDenseIndex,
    QdrantRestClient,
)

ROOT = Path(__file__).resolve().parent


async def run_probe(schema: str, collection: str, output: Path) -> dict[str, Any]:
    corpus = build_corpus(write_manifest=True)
    pg_store = PostgresEvidenceStore()
    pg_count = await pg_store.persist(schema, corpus)
    qdrant = QdrantCandidateIndex(QdrantDenseIndex(QdrantRestClient(), collection, FakeEmbeddingProvider()))
    vector_count = await qdrant.build(corpus.chunks, corpus.generation_id, corpus.corpus_id)
    hits = await qdrant.search("ingestion pipeline evidence", top_k=5, generation_id=corpus.generation_id)
    if pg_count != len(corpus.evidence) or vector_count != len(corpus.chunks) or not hits:
        raise RuntimeError("local service probe returned incomplete task-owned resources")
    result = {
        "plan_version": "KW-P0-2026-11-18",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "resources": {"schema": schema, "collection": collection, "corpus_id": corpus.corpus_id, "generation_id": corpus.generation_id},
        "postgresql": {"status": "PASS", "evidence_rows": pg_count},
        "qdrant": {
            "status": "PASS",
            "vectors": vector_count,
            "query_hits": len(hits),
            "embedding_provider": "fake-hash-embedding-v1",
            "real_qwen_gate_passed": False,
        },
        "external_model_calls": False,
        "cleanup_scope": "Only the named P3 schema and collection may be deleted by a later cleanup action.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "service-probe.json")
    args = parser.parse_args()
    PostgresEvidenceStore.validate_schema(args.schema)
    if not args.collection.startswith("p3_"):
        parser.error("collection must start with p3_")
    try:
        PostgresEvidenceStore()
    except ProviderUnavailable as exc:
        print("service_probe=WAITING_EXTERNAL")
        print(f"last_error={exc}")
        return 2
    asyncio.run(run_probe(args.schema, args.collection, args.output))
    print(f"service_probe=PASS schema={args.schema} collection={args.collection}")
    print("dense_provider=fake-hash-embedding-v1; real_qwen_gate=WAITING_EXTERNAL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
