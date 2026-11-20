from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from knowledge_weaver.core.ids import deterministic_id, sha256_text
from knowledge_weaver.models import (
    Chunk,
    Entity,
    Evidence,
    GraphSnapshot,
    IndexCandidate,
    Mention,
    Provenance,
    ProvenanceMethod,
    QueryContext,
    Relation,
)


def make_context(
    *,
    generation_id: str | None = None,
    request_id: str | None = None,
    source_ids: list[str] | None = None,
    include_inferred: bool = False,
) -> QueryContext:
    return QueryContext(
        corpus_id=deterministic_id("b05_corpus", "fixture"),
        generation_id=generation_id or deterministic_id("b05_generation", "one"),
        request_id=request_id or deterministic_id("b05_request", datetime.now(UTC).isoformat()),
        deadline_utc=datetime.now(UTC) + timedelta(minutes=5),
        source_ids=source_ids,
        include_inferred=include_inferred,
    )


def _source_id(name: str) -> str:
    return deterministic_id("b05_source", name)


def _entity_id(name: str) -> str:
    return deterministic_id("b05_entity", name)


def _provenance(
    source_id: str,
    method: ProvenanceMethod = ProvenanceMethod.PARSER_EXCERPT,
    *,
    rule_id: str | None = None,
    premises: list[str] | None = None,
    label: str = "evidence",
) -> Provenance:
    document_id = deterministic_id("b05_document", {"source": source_id, "label": label})
    return Provenance(
        id=deterministic_id(
            "b05_provenance",
            {
                "source": source_id,
                "method": method.value,
                "label": label,
                "rule_id": rule_id,
                "premises": premises or [],
            },
        ),
        method=method,
        producer_fingerprint="b05-fixture-v1",
        source_id=source_id,
        document_id=document_id,
        premise_relation_ids=premises or [],
        rule_id=rule_id,
    )


def build_case(
    context: QueryContext,
    *,
    include_conflict: bool = False,
    include_cycle: bool = False,
    alternate_route: bool = False,
    relation_method_overrides: dict[str, ProvenanceMethod] | None = None,
    evidence_method_overrides: dict[str, ProvenanceMethod] | None = None,
    extra_relations: list[Relation] | None = None,
    extra_evidence: list[Evidence] | None = None,
    extra_provenance: list[Provenance] | None = None,
    extra_mentions: list[Mention] | None = None,
    fan_out: int = 0,
) -> dict[str, Any]:
    source_a = _source_id("manual")
    source_b = _source_id("repository")
    entities = {
        name: {
            "schema_version": 1,
            "id": _entity_id(name),
            "corpus_id": context.corpus_id,
            "kind": "concept",
            "canonical_name": name,
            "namespace": "fixture",
            "identity_key": name.casefold(),
            "aliases": [],
        }
        for name in ("Pipeline", "Evidence", "Repository", "Graph")
    }

    evidence_specs = {
        "ab": (source_a, "Pipeline uses evidence-backed chunks."),
        "bc": (source_b, "Evidence links the repository source."),
        "cd": (source_b, "The repository source defines the graph path."),
        "ad": (source_a, "An alternate route reaches the same entity."),
        "negative": (source_a, "Pipeline does not use evidence-backed chunks."),
        "cycle": (source_b, "The graph path links back to Pipeline."),
    }
    provenance: dict[str, Provenance] = {}
    evidence: dict[str, Evidence] = {}
    chunks: dict[str, Chunk] = {}
    for label, (source_id, text) in evidence_specs.items():
        evidence_method = (evidence_method_overrides or {}).get(
            label, ProvenanceMethod.PARSER_EXCERPT
        )
        evidence_provenance = _provenance(
            source_id,
            evidence_method,
            rule_id="fixture_rule" if evidence_method == ProvenanceMethod.RULE_INFERRED else None,
            premises=[deterministic_id("b05_relation", "evidence-premise")]
            if evidence_method == ProvenanceMethod.RULE_INFERRED
            else None,
            label=label,
        )
        provenance[evidence_provenance.id] = evidence_provenance
        chunk_id = deterministic_id("b05_chunk", label)
        chunk = Chunk.model_validate(
            {
                "schema_version": 1,
                "id": chunk_id,
                "document_id": evidence_provenance.document_id,
                "parent_id": None,
                "is_leaf": True,
                "text": text,
                "text_hash": sha256_text(text),
                "spans": [
                    {
                        "schema_version": 1,
                        "chunk_start": 0,
                        "chunk_end": len(text),
                        "document_start": 0,
                        "document_end": len(text),
                        "locator": {
                            "kind": "markdown",
                            "path": f"{label}.md",
                            "line_start": 1,
                            "line_end": 1,
                        },
                    }
                ],
                "structure_type": "paragraph",
                "qualname": None,
                "heading_path": [],
                "duplicate_group": None,
                "chunker_fingerprint": "b05-fixture-v1",
            }
        )
        evidence_item = Evidence.model_validate(
            {
                "schema_version": 1,
                "id": deterministic_id("b05_evidence", label),
                "chunk_id": chunk.id,
                "start": 0,
                "end": len(text),
                "quote": text,
                "locator": {
                    "kind": "markdown",
                    "path": f"{label}.md",
                    "line_start": 1,
                    "line_end": 1,
                },
                "provenance_id": evidence_provenance.id,
            }
        )
        chunks[chunk.id] = chunk
        evidence[evidence_item.id] = evidence_item

    def relation(
        name: str,
        subject: str,
        predicate: str,
        object_: str,
        evidence_label: str,
        *,
        polarity: str = "positive",
        qualifiers: dict[str, Any] | None = None,
        source_id: str | None = None,
        method: ProvenanceMethod | None = None,
        rule_id: str | None = None,
    ) -> Relation:
        evidence_item = evidence[deterministic_id("b05_evidence", evidence_label)]
        evidence_source = evidence_specs[evidence_label][0]
        relation_source = source_id or evidence_source
        method = method or (relation_method_overrides or {}).get(
            name, ProvenanceMethod.LLM_EXTRACTED
        )
        premises = [deterministic_id("b05_relation", "ab")] if method == ProvenanceMethod.RULE_INFERRED else []
        if method == ProvenanceMethod.RULE_INFERRED and rule_id is None:
            rule_id = "fixture_rule"
        relation_provenance = _provenance(
            relation_source,
            method,
            rule_id=rule_id,
            premises=premises,
            label=f"relation-{name}",
        )
        provenance[relation_provenance.id] = relation_provenance
        return Relation(
            id=deterministic_id("b05_relation", name),
            subject_id=entities[subject]["id"],
            predicate=predicate,
            object_id=entities[object_]["id"],
            polarity=polarity,
            qualifiers=qualifiers or {},
            provenance_id=relation_provenance.id,
            evidence_ids=[evidence_item.id],
        )

    relations = [
        relation("ab", "Pipeline", "USES", "Evidence", "ab", qualifiers={"scope": "runtime"}),
        relation("bc", "Evidence", "DEFINES", "Repository", "bc"),
        relation("cd", "Repository", "USES", "Graph", "cd"),
    ]
    if include_conflict:
        relations.append(
            relation(
                "ab-negative",
                "Pipeline",
                "USES",
                "Evidence",
                "negative",
                polarity="negative",
                qualifiers={"scope": "runtime"},
            )
        )
    if include_cycle:
        relations.append(relation("cycle-da", "Graph", "USES", "Pipeline", "cycle"))
    if alternate_route:
        relations.extend(
            [
                relation("ad", "Pipeline", "RELATED_TO", "Graph", "ad"),
                relation("db", "Graph", "RELATED_TO", "Evidence", "ab"),
            ]
        )
    mentions = [
        Mention(id=deterministic_id("b05_mention", "a-ab"), entity_id=entities["Pipeline"]["id"], evidence_id=deterministic_id("b05_evidence", "ab"), surface_form="Pipeline"),
        Mention(id=deterministic_id("b05_mention", "b-ab"), entity_id=entities["Evidence"]["id"], evidence_id=deterministic_id("b05_evidence", "ab"), surface_form="Evidence"),
        Mention(id=deterministic_id("b05_mention", "b-bc"), entity_id=entities["Evidence"]["id"], evidence_id=deterministic_id("b05_evidence", "bc"), surface_form="Evidence"),
        Mention(id=deterministic_id("b05_mention", "c-bc"), entity_id=entities["Repository"]["id"], evidence_id=deterministic_id("b05_evidence", "bc"), surface_form="repository source"),
        Mention(id=deterministic_id("b05_mention", "c-cd"), entity_id=entities["Repository"]["id"], evidence_id=deterministic_id("b05_evidence", "cd"), surface_form="repository source"),
        Mention(id=deterministic_id("b05_mention", "d-cd"), entity_id=entities["Graph"]["id"], evidence_id=deterministic_id("b05_evidence", "cd"), surface_form="graph path"),
    ]

    for index in range(fan_out):
        name = f"Fanout {index:03}"
        entity_id_value = _entity_id(name)
        entities[name] = {
            "schema_version": 1,
            "id": entity_id_value,
            "corpus_id": context.corpus_id,
            "kind": "concept",
            "canonical_name": name,
            "namespace": "fixture",
            "identity_key": name.casefold(),
            "aliases": [],
        }
        relations.append(
            relation(
                f"fanout-{index}",
                "Pipeline",
                "USES",
                name,
                "ab",
                qualifiers={"fanout": index},
            )
        )
        mentions.append(
            Mention(
                id=deterministic_id("b05_mention", f"fanout-{index}"),
                entity_id=entity_id_value,
                evidence_id=deterministic_id("b05_evidence", "ab"),
                surface_form=name,
            )
        )

    relations.extend(extra_relations or [])
    evidence.update({item.id: item for item in (extra_evidence or [])})
    provenance.update({item.id: item for item in (extra_provenance or [])})
    mentions.extend(extra_mentions or [])

    all_entities = [Entity.model_validate(item) for item in entities.values()]

    allowed_sources = None if context.source_ids is None else set(context.source_ids)
    allowed_evidence = {
        evidence_id
        for evidence_id, item in evidence.items()
        if allowed_sources is None
        or provenance[item.provenance_id].source_id in allowed_sources
    }
    filtered_mentions = [item for item in mentions if item.evidence_id in allowed_evidence]
    visible_entities = {item.entity_id for item in filtered_mentions}
    filtered_relations = [
        item
        for item in relations
        if item.subject_id in visible_entities
        and item.object_id in visible_entities
        and set(item.evidence_ids).issubset(allowed_evidence)
        and (
            context.include_inferred
            or provenance[item.provenance_id].method != ProvenanceMethod.RULE_INFERRED
        )
    ]
    used_provenance_ids = {
        evidence[item_id].provenance_id for item_id in allowed_evidence
    } | {item.provenance_id for item in filtered_relations}
    filtered_snapshot = GraphSnapshot(
        context=context,
        entities=[item for item in all_entities if item.id in visible_entities],
        mentions=filtered_mentions,
        relations=filtered_relations,
        evidence=[item for item in evidence.values() if item.id in allowed_evidence],
        provenance=[item for item in provenance.values() if item.id in used_provenance_ids],
        snapshot_hash=sha256_text(
            f"{context.generation_id}:{context.source_ids}:{context.include_inferred}"
        ),
    )
    return {
        "snapshot": filtered_snapshot,
        "entities": entities,
        "evidence": evidence,
        "chunks": chunks,
        "relations": relations,
        "provenance": provenance,
        "source_a": source_a,
        "source_b": source_b,
    }


class FakeGraphStore:
    def __init__(self, factory: Callable[[QueryContext], GraphSnapshot]) -> None:
        self.factory = factory
        self.calls: list[QueryContext] = []

    async def snapshot(self, context: QueryContext) -> GraphSnapshot:
        self.calls.append(context)
        return self.factory(context)


class FakeEntityIndex:
    def __init__(self, candidates: list[IndexCandidate] | None = None) -> None:
        self.candidates = candidates or []
        self.queries: list[Any] = []

    async def search(self, query: Any) -> list[IndexCandidate]:
        self.queries.append(query)
        return self.candidates[: query.top_k]


class FakeKnowledgeStore:
    def __init__(self, evidence: dict[str, Evidence], chunks: dict[str, Chunk]) -> None:
        self.evidence = evidence
        self.chunks = chunks
        self.evidence_requests: list[list[str]] = []
        self.chunk_requests: list[list[str]] = []

    async def get_evidence(self, ids: list[str], context: QueryContext) -> list[Evidence]:
        self.evidence_requests.append(ids)
        return [self.evidence[item] for item in ids if item in self.evidence]

    async def get_chunks(self, ids: list[str], context: QueryContext) -> list[Chunk]:
        self.chunk_requests.append(ids)
        return [self.chunks[item] for item in ids if item in self.chunks]


@pytest.fixture
def case_factory() -> Callable[..., dict[str, Any]]:
    return build_case


@pytest.fixture
def context_factory() -> Callable[..., QueryContext]:
    return make_context
