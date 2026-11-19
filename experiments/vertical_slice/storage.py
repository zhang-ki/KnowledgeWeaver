"""Isolated P3 evidence persistence adapters; no production module imports."""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from experiments.vertical_slice.models import Corpus, canonical_json, derived_id, sha256_bytes
from experiments.vertical_slice.providers import ProviderUnavailable


def write_snapshot(path: Path, corpus: Corpus) -> str:
    """Atomically persist a safe JSON representation of the isolated corpus."""
    payload = json.dumps(corpus.to_dict(), ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


class PostgresEvidenceStore:
    """PostgreSQL adapter with a caller-supplied task-only schema name."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.environ.get("KW_DATABASE_URL") or os.environ.get("KW_POSTGRES_DSN") or ""
        if not self.dsn:
            raise ProviderUnavailable("PostgreSQL DSN is not configured")
        parsed = urllib.parse.urlparse(self.dsn)
        if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ProviderUnavailable("P3 PostgreSQL writes are restricted to loopback URLs")

    @staticmethod
    def validate_schema(schema: str) -> str:
        if not re.fullmatch(r"p3_[a-z0-9_]{1,56}", schema):
            raise ValueError("schema must be a P3-prefixed lowercase identifier")
        return schema

    async def persist(self, schema: str, corpus: Corpus) -> int:
        return await asyncio.to_thread(self._persist_sync, schema, corpus)

    def _persist_sync(self, schema: str, corpus: Corpus) -> int:
        schema = self.validate_schema(schema)
        try:
            import psycopg
            from psycopg import sql
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise ProviderUnavailable("psycopg is unavailable in this interpreter") from exc
        with psycopg.connect(self.dsn, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {}.generations (
                            id TEXT PRIMARY KEY, corpus_id TEXT NOT NULL, state TEXT NOT NULL,
                            source_version_ids JSONB NOT NULL, index_manifest JSONB NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS {}.documents (
                            id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_kind TEXT NOT NULL,
                            relative_path TEXT NOT NULL, raw_hash TEXT NOT NULL,
                            parser_fingerprint TEXT NOT NULL, normalized_text TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS {}.chunks (
                            id TEXT PRIMARY KEY, document_id TEXT NOT NULL, source_id TEXT NOT NULL,
                            source_kind TEXT NOT NULL, relative_path TEXT NOT NULL,
                            text TEXT NOT NULL, locator JSONB NOT NULL, text_hash TEXT NOT NULL,
                            structure_type TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS {}.provenance (
                            id TEXT PRIMARY KEY, method TEXT NOT NULL, producer_fingerprint TEXT NOT NULL,
                            source_id TEXT NOT NULL, document_id TEXT NOT NULL, confidence DOUBLE PRECISION,
                            premise_relation_ids JSONB NOT NULL, rule_id TEXT
                        );
                        CREATE TABLE IF NOT EXISTS {}.evidence (
                            id TEXT PRIMARY KEY, chunk_id TEXT NOT NULL, document_id TEXT NOT NULL,
                            source_id TEXT NOT NULL, chunk_start INTEGER NOT NULL, chunk_end INTEGER NOT NULL,
                            quote TEXT NOT NULL, locator JSONB NOT NULL,
                            provenance_id TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS {}.entities (
                            id TEXT PRIMARY KEY, corpus_id TEXT NOT NULL, canonical_name TEXT NOT NULL,
                            identity_key TEXT NOT NULL, aliases JSONB NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS {}.relations (
                            id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, predicate TEXT NOT NULL,
                            object_id TEXT NOT NULL, polarity TEXT NOT NULL, evidence_ids JSONB NOT NULL,
                            provenance_id TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS {}.generation_members (
                            generation_id TEXT NOT NULL, member_kind TEXT NOT NULL, member_id TEXT NOT NULL,
                            PRIMARY KEY (generation_id, member_kind, member_id)
                        );
                        CREATE TABLE IF NOT EXISTS {}.sources (
                            id TEXT PRIMARY KEY, corpus_id TEXT NOT NULL, kind TEXT NOT NULL,
                            logical_uri TEXT NOT NULL, display_name TEXT NOT NULL, license TEXT
                        );
                        CREATE TABLE IF NOT EXISTS {}.source_versions (
                            id TEXT PRIMARY KEY, source_id TEXT NOT NULL, manifest_hash TEXT NOT NULL,
                            git_revision TEXT NOT NULL, dirty BOOLEAN NOT NULL, files JSONB NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS {}.source_version_documents (
                            source_version_id TEXT NOT NULL, document_id TEXT NOT NULL,
                            PRIMARY KEY (source_version_id, document_id)
                        );
                        """
                    ).format(*[sql.Identifier(schema)] * 11)
                )
                source_versions: dict[str, str] = {}
                for doc in corpus.documents:
                    file_membership = [{"relative_path": doc.relative_path, "raw_hash": doc.raw_hash, "document_id": doc.document_id}]
                    version_body = {
                        "source_id": doc.source_id,
                        "files": file_membership,
                        "git_revision": corpus.git_revision,
                        "dirty": corpus.git_dirty,
                        "parser_fingerprint": "p3-fixture-v1",
                    }
                    version_hash = sha256_bytes(canonical_json(version_body).encode("utf-8"))
                    version_id = derived_id("source_version", {"source_id": doc.source_id, "manifest_hash": version_hash})
                    source_versions[doc.source_id] = version_id
                    cursor.execute(
                        sql.SQL("INSERT INTO {}.sources VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING").format(sql.Identifier(schema)),
                        (doc.source_id, corpus.corpus_id, doc.source_kind, doc.relative_path, Path(doc.relative_path).name, "synthetic-public-fixture"),
                    )
                    cursor.execute(
                        sql.SQL("INSERT INTO {}.source_versions VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING").format(sql.Identifier(schema)),
                        (version_id, doc.source_id, version_hash, corpus.git_revision, corpus.git_dirty, Jsonb(file_membership)),
                    )
                    cursor.execute(
                        sql.SQL("INSERT INTO {}.source_version_documents VALUES (%s,%s) ON CONFLICT DO NOTHING").format(sql.Identifier(schema)),
                        (version_id, doc.document_id),
                    )
                source_version_ids = list(source_versions.values())
                cursor.execute(
                    sql.SQL("INSERT INTO {}.generations VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING").format(
                        sql.Identifier(schema)
                    ),
                    (
                        corpus.generation_id,
                        corpus.corpus_id,
                        "READY",
                        Jsonb(source_version_ids),
                        Jsonb({"embedding_fingerprint": "fake-hash-embedding-v1:32:camelcase-stopword-sha256-sign-v2", "real_model": False}),
                    ),
                )
                for doc in corpus.documents:
                    cursor.execute(
                        sql.SQL("INSERT INTO {}.documents VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET normalized_text=EXCLUDED.normalized_text").format(sql.Identifier(schema)),
                        (doc.document_id, doc.source_id, doc.source_kind, doc.relative_path, doc.raw_hash, "p3-fixture-v1", doc.text),
                    )
                for chunk in corpus.chunks:
                    cursor.execute(
                        sql.SQL("INSERT INTO {}.chunks VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING").format(sql.Identifier(schema)),
                        (chunk.id, chunk.document_id, chunk.source_id, chunk.source_kind, chunk.relative_path, chunk.text, Jsonb(chunk.locator), chunk.text_hash, chunk.structure_type),
                    )
                for provenance_id, provenance in corpus.provenance.items():
                    cursor.execute(
                        sql.SQL("INSERT INTO {}.provenance VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING").format(sql.Identifier(schema)),
                        (provenance_id, provenance.method, provenance.producer_fingerprint, provenance.source_id, provenance.document_id, provenance.confidence, Jsonb(list(provenance.premise_relation_ids)), provenance.rule_id),
                    )
                for evidence in corpus.evidence:
                    cursor.execute(
                        sql.SQL("INSERT INTO {}.evidence VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING").format(sql.Identifier(schema)),
                        (evidence.id, evidence.chunk_id, evidence.document_id, evidence.source_id, evidence.start, evidence.end, evidence.quote, Jsonb(evidence.locator), evidence.provenance_id),
                    )
                for entity in corpus.entities.values():
                    cursor.execute(
                        sql.SQL("INSERT INTO {}.entities VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING").format(sql.Identifier(schema)),
                        (entity.id, corpus.corpus_id, entity.canonical_name, entity.identity_key, Jsonb(list(entity.aliases))),
                    )
                for relation in corpus.relations:
                    cursor.execute(
                        sql.SQL("INSERT INTO {}.relations VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING").format(sql.Identifier(schema)),
                        (relation.id, relation.subject_id, relation.predicate, relation.object_id, relation.polarity, Jsonb(list(relation.evidence_ids)), relation.provenance_id),
                    )
                members = (
                    [("source_version", item) for item in source_version_ids]
                    + [("document", item.document_id) for item in corpus.documents]
                    + [("chunk", item.id) for item in corpus.chunks]
                    + [("evidence", item.id) for item in corpus.evidence]
                    + [("entity", item.id) for item in corpus.entities.values()]
                    + [("relation", item.id) for item in corpus.relations]
                )
                for kind, member_id in members:
                    cursor.execute(
                        sql.SQL("INSERT INTO {}.generation_members VALUES (%s,%s,%s) ON CONFLICT DO NOTHING").format(sql.Identifier(schema)),
                        (corpus.generation_id, kind, member_id),
                    )
            return len(corpus.evidence)


class QdrantRestClient:
    """Small authenticated REST client; error messages omit endpoint and body."""

    def __init__(self, url: str | None = None, api_key: str | None = None, timeout_seconds: float = 5.0) -> None:
        self.url = (url or os.environ.get("KW_QDRANT_URL", "http://127.0.0.1:56333")).strip().rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("KW_QDRANT_API_KEY", "")
        self.timeout_seconds = timeout_seconds
        if not self.url:
            raise ProviderUnavailable("Qdrant URL is not configured")
        parsed = urllib.parse.urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Qdrant URL must be an HTTP URL")
        if parsed.hostname.casefold() not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("P3 Qdrant data writes are restricted to loopback services")

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["api-key"] = self.api_key
        request = urllib.request.Request(self.url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise ProviderUnavailable(f"Qdrant request failed with HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderUnavailable(f"Qdrant request failed: {type(exc).__name__}") from None
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ProviderUnavailable("Qdrant returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise ProviderUnavailable("Qdrant returned an invalid response object")
        return parsed


def qdrant_point_id(generation_id: str, chunk_id: str, embedding_fingerprint: str) -> str:
    namespace = uuid.UUID(generation_id)
    name = json.dumps([generation_id, chunk_id, embedding_fingerprint], separators=(",", ":"))
    return str(uuid.uuid5(namespace, name))


class QdrantDenseIndex:
    """Generation-filtered Qdrant index using the frozen embedding port shape."""

    def __init__(self, client: QdrantRestClient, collection: str, embedder: Any) -> None:
        if not re.fullmatch(r"p3_[a-z0-9_]{1,56}", collection):
            raise ValueError("collection must be a P3-prefixed lowercase identifier")
        self.client = client
        self.collection = collection
        self.embedder = embedder
        self.generation_id = ""
        self.corpus_id = ""
        self.embedding_fingerprint = ""

    async def build(self, chunks: list[Any], generation_id: str, corpus_id: str) -> int:
        batch = await self.embedder.embed([chunk.text for chunk in chunks], mode="document")
        self.generation_id = generation_id
        self.corpus_id = corpus_id
        self.embedding_fingerprint = batch.fingerprint
        try:
            await asyncio.to_thread(self.client.request, "GET", f"/collections/{self.collection}")
        except ProviderUnavailable as exc:
            if "HTTP 404" not in str(exc):
                raise
            await asyncio.to_thread(
                self.client.request,
                "PUT",
                f"/collections/{self.collection}",
                {"vectors": {"size": batch.dimension, "distance": "Cosine"}},
            )
        points = [
            {
                "id": qdrant_point_id(generation_id, chunk.id, batch.fingerprint),
                "vector": vector,
                "payload": {
                    "corpus_id": corpus_id,
                    "generation_id": generation_id,
                    "source_id": chunk.source_id,
                    "source_kind": chunk.source_kind,
                    "chunk_id": chunk.id,
                    "relative_path": chunk.relative_path,
                    "embedding_fingerprint": batch.fingerprint,
                },
            }
            for chunk, vector in zip(chunks, batch.vectors, strict=True)
        ]
        if points:
            await asyncio.to_thread(self.client.request, "PUT", f"/collections/{self.collection}/points?wait=true", {"points": points})
        return len(points)

    async def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        batch = await self.embedder.embed([query], mode="query")
        if batch.fingerprint != self.embedding_fingerprint:
            raise ProviderUnavailable("query embedding fingerprint differs from indexed vectors")
        result = await asyncio.to_thread(
            self.client.request,
            "POST",
            f"/collections/{self.collection}/points/query",
            {
                "query": batch.vectors[0],
                "limit": top_k,
                "with_payload": True,
                "filter": {"must": [{"key": "generation_id", "match": {"value": self.generation_id}}]},
            },
        )
        data = result.get("result", [])
        if isinstance(data, dict):
            data = data.get("points", [])
        return [
            {"chunk_id": item.get("payload", {}).get("chunk_id"), "score": item.get("score", 0.0), "payload": item.get("payload", {})}
            for item in data
            if item.get("payload", {}).get("generation_id") == self.generation_id
        ]

    async def delete(self) -> None:
        await asyncio.to_thread(self.client.request, "DELETE", f"/collections/{self.collection}")
