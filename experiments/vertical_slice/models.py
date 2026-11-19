"""Small private DTOs for the P3 experiment, separate from frozen public ports."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


SCHEMA_VERSION = 1
ID_NAMESPACE = uuid.UUID("256bf6b7-a9db-5eac-944e-985ef155c9fe")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def derived_id(kind: str, value: Any) -> str:
    return str(uuid.uuid5(ID_NAMESPACE, f"{kind}:{canonical_json(value)}"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class SourceDocument:
    source_id: str
    document_id: str
    source_kind: str
    relative_path: str
    raw_hash: str
    text: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class Chunk:
    id: str
    document_id: str
    source_id: str
    source_kind: str
    relative_path: str
    text: str
    locator: dict[str, Any]
    text_hash: str
    structure_type: str


@dataclass(frozen=True)
class Evidence:
    id: str
    chunk_id: str
    document_id: str
    source_id: str
    source_kind: str
    relative_path: str
    start: int
    end: int
    quote: str
    locator: dict[str, Any]
    provenance_id: str


@dataclass(frozen=True)
class Provenance:
    id: str
    method: str
    producer_fingerprint: str
    source_id: str
    document_id: str
    confidence: float | None = None
    premise_relation_ids: tuple[str, ...] = ()
    rule_id: str | None = None


@dataclass(frozen=True)
class Entity:
    id: str
    canonical_name: str
    aliases: tuple[str, ...] = ()
    identity_key: str = ""


@dataclass(frozen=True)
class Relation:
    id: str
    subject_id: str
    subject: str
    predicate: str
    object_id: str
    object: str
    polarity: str
    evidence_ids: tuple[str, ...]
    source_kind: str
    provenance_id: str
    provenance_method: str


@dataclass
class Corpus:
    corpus_id: str
    generation_id: str
    git_revision: str
    git_dirty: bool
    documents: list[SourceDocument] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    provenance: dict[str, Provenance] = field(default_factory=dict)
    entities: dict[str, Entity] = field(default_factory=dict)
    relations: list[Relation] = field(default_factory=list)
    entity_mappings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "corpus_id": self.corpus_id,
            "generation_id": self.generation_id,
            "git_revision": self.git_revision,
            "git_dirty": self.git_dirty,
            "documents": [asdict(item) for item in self.documents],
            "chunks": [asdict(item) for item in self.chunks],
            "evidence": [asdict(item) for item in self.evidence],
            "provenance": [asdict(item) for item in self.provenance.values()],
            "entities": [asdict(item) for item in self.entities.values()],
            "relations": [asdict(item) for item in self.relations],
            "entity_mappings": self.entity_mappings,
        }


@dataclass(frozen=True)
class Citation:
    evidence_id: str
    relative_path: str
    quote: str
    locator: dict[str, Any]


@dataclass(frozen=True)
class Claim:
    text: str
    evidence_ids: tuple[str, ...]
    relation_ids: tuple[str, ...] = ()


@dataclass
class InvestigationResult:
    status: str
    answer: str
    claims: list[Claim]
    citations: list[Citation]
    unresolved: list[str]
    conflicts: list[dict[str, Any]]
    route_history: list[str]
    action_history: list[dict[str, Any]]
    budget_used: dict[str, int]
    stop_reason: str
    policy_profile: str = "prior"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
