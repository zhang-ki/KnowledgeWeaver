from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

SCHEMA_VERSION = 1
ID_NAMESPACE = uuid.UUID("256bf6b7-a9db-5eac-944e-985ef155c9fe")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def deterministic_id(kind: str, value: Any) -> str:
    if not kind or not kind.strip():
        raise ValueError("kind must be non-empty")
    return str(uuid.uuid5(ID_NAMESPACE, f"{kind}:{canonical_json(value)}"))


def new_id() -> str:
    return str(uuid.uuid4())


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def source_version_id(source_id: str, manifest_hash: str) -> str:
    return deterministic_id("source_version", {"source_id": source_id, "manifest_hash": manifest_hash})


def document_id(source_id: str, relative_path: str, raw_hash: str, parser_fingerprint: str) -> str:
    return deterministic_id(
        "document",
        {
            "source_id": source_id,
            "relative_path": relative_path,
            "raw_hash": raw_hash,
            "parser_fingerprint": parser_fingerprint,
        },
    )


def block_id(document_id_value: str, locator: dict[str, Any], text_hash: str) -> str:
    return deterministic_id(
        "block",
        {"document_id": document_id_value, "locator": locator, "text_hash": text_hash},
    )


def chunk_id(
    document_id_value: str,
    structural_path: list[str],
    chunker_fingerprint: str,
    text_hash: str,
) -> str:
    return deterministic_id(
        "chunk",
        {
            "document_id": document_id_value,
            "structural_path": structural_path,
            "chunker_fingerprint": chunker_fingerprint,
            "text_hash": text_hash,
        },
    )


def evidence_id(
    chunk_id_value: str, start: int, end: int, quote: str, provenance_id_value: str
) -> str:
    return deterministic_id(
        "evidence",
        {
            "chunk_id": chunk_id_value,
            "start": start,
            "end": end,
            "quote_hash": sha256_text(quote),
            "provenance_id": provenance_id_value,
        },
    )


def entity_id(corpus_id: str, kind: str, identity_key: str) -> str:
    return deterministic_id(
        "entity", {"corpus_id": corpus_id, "kind": kind, "identity_key": identity_key}
    )


def provenance_id(
    method: str,
    producer_fingerprint: str,
    source_id: str,
    document_id_value: str,
    premise_relation_ids: list[str] | None = None,
    rule_id: str | None = None,
) -> str:
    return deterministic_id(
        "provenance",
        {
            "method": method,
            "producer_fingerprint": producer_fingerprint,
            "source_id": source_id,
            "document_id": document_id_value,
            "premise_relation_ids": sorted(premise_relation_ids or []),
            "rule_id": rule_id,
        },
    )


def relation_id(
    subject_id: str,
    predicate: str,
    object_id: str,
    polarity: str,
    qualifiers: dict[str, Any],
    provenance_id_value: str,
    evidence_ids: list[str],
) -> str:
    return deterministic_id(
        "relation",
        {
            "subject_id": subject_id,
            "predicate": predicate,
            "object_id": object_id,
            "polarity": polarity,
            "qualifiers": qualifiers,
            "provenance_id": provenance_id_value,
            "evidence_ids": sorted(evidence_ids),
        },
    )
