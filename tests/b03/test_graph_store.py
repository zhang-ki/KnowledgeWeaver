from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Self
from uuid import uuid4

import pytest

from knowledge_weaver.indexing.graph_store import PostgresGraphStore
from knowledge_weaver.models import (
    Entity,
    EntityKind,
    Evidence,
    MarkdownLocator,
    Mention,
    Polarity,
    Provenance,
    ProvenanceMethod,
    QueryContext,
    Relation,
    RelationPredicate,
)
from knowledge_weaver.storage.schema import (
    ChunkRow,
    DocumentRow,
    EntityRow,
    EvidenceRow,
    GenerationEntityRow,
    GenerationEvidenceRow,
    GenerationMentionRow,
    GenerationRelationRow,
    GenerationRow,
    MentionRow,
    ProvenanceRow,
    RelationEvidenceRow,
    RelationRow,
)


class GraphSession:
    def __init__(self) -> None:
        self.corpus_id, self.generation_id = str(uuid4()), str(uuid4())
        self.source_a, self.source_b = str(uuid4()), str(uuid4())
        self.document_a, self.document_b = str(uuid4()), str(uuid4())
        self.chunk_a, self.chunk_b = str(uuid4()), str(uuid4())
        self.provenance_a, self.provenance_b = str(uuid4()), str(uuid4())
        self.entity_a, self.entity_b = str(uuid4()), str(uuid4())
        self.evidence_a, self.evidence_b, self.evidence_outside = (str(uuid4()) for _ in range(3))
        self.mention_a, self.mention_b, self.old_mention = (str(uuid4()) for _ in range(3))
        self.relation_cross_source, self.relation_outside_generation = str(uuid4()), str(uuid4())
        self.generation = GenerationRow(
            id=self.generation_id,
            corpus_id=self.corpus_id,
            state="READY",
            source_version_ids=[],
            index_manifest={},
            embedding_fingerprint="test-embedding-v1",
            generation_hash="0" * 64,
            created_at=datetime.now(UTC),
            published_at=datetime.now(UTC),
        )
        entity_a = Entity(
            id=self.entity_a,
            corpus_id=self.corpus_id,
            kind=EntityKind.concept,
            canonical_name="Entity A",
            namespace="source-a",
            identity_key="entity-a",
        )
        entity_b = Entity(
            id=self.entity_b,
            corpus_id=self.corpus_id,
            kind=EntityKind.concept,
            canonical_name="Entity B",
            namespace="source-b",
            identity_key="entity-b",
        )
        provenance_a = Provenance(
            id=self.provenance_a,
            method=ProvenanceMethod.STATIC_ANALYSIS,
            producer_fingerprint="test-provenance-v1",
            source_id=self.source_a,
            document_id=self.document_a,
        )
        provenance_b = Provenance(
            id=self.provenance_b,
            method=ProvenanceMethod.STATIC_ANALYSIS,
            producer_fingerprint="test-provenance-v1",
            source_id=self.source_b,
            document_id=self.document_b,
        )
        locator_a = MarkdownLocator(path="a.md", line_start=1, line_end=1)
        locator_b = MarkdownLocator(path="b.md", line_start=1, line_end=1)
        evidence_a = Evidence(
            id=self.evidence_a,
            chunk_id=self.chunk_a,
            start=0,
            end=1,
            quote="A",
            locator=locator_a,
            provenance_id=self.provenance_a,
        )
        evidence_b = Evidence(
            id=self.evidence_b,
            chunk_id=self.chunk_b,
            start=0,
            end=1,
            quote="B",
            locator=locator_b,
            provenance_id=self.provenance_b,
        )
        mention_a = Mention(id=self.mention_a, entity_id=self.entity_a, evidence_id=self.evidence_a, surface_form="A")
        mention_b = Mention(id=self.mention_b, entity_id=self.entity_b, evidence_id=self.evidence_b, surface_form="B")
        old_mention = Mention(id=self.old_mention, entity_id=self.entity_a, evidence_id=self.evidence_a, surface_form="A (old generation)")
        relation_cross_source = Relation(
            id=self.relation_cross_source,
            subject_id=self.entity_a,
            predicate=RelationPredicate.RELATED_TO,
            object_id=self.entity_b,
            polarity=Polarity.positive,
            provenance_id=self.provenance_b,
            evidence_ids=[self.evidence_b],
        )
        relation_outside_generation = Relation(
            id=self.relation_outside_generation,
            subject_id=self.entity_a,
            predicate=RelationPredicate.RELATED_TO,
            object_id=self.entity_a,
            polarity=Polarity.positive,
            provenance_id=self.provenance_a,
            evidence_ids=[self.evidence_outside],
        )
        self.rows = {
            EntityRow: [
                SimpleNamespace(id=self.entity_a, payload=entity_a.model_dump(mode="json")),
                SimpleNamespace(id=self.entity_b, payload=entity_b.model_dump(mode="json")),
            ],
            MentionRow: [
                SimpleNamespace(id=self.mention_a, entity_id=self.entity_a, evidence_id=self.evidence_a, payload=mention_a.model_dump(mode="json")),
                SimpleNamespace(id=self.mention_b, entity_id=self.entity_b, evidence_id=self.evidence_b, payload=mention_b.model_dump(mode="json")),
                SimpleNamespace(id=self.old_mention, entity_id=self.entity_a, evidence_id=self.evidence_a, payload=old_mention.model_dump(mode="json")),
            ],
            RelationRow: [
                SimpleNamespace(id=self.relation_cross_source, payload=relation_cross_source.model_dump(mode="json")),
                SimpleNamespace(id=self.relation_outside_generation, payload=relation_outside_generation.model_dump(mode="json")),
            ],
            RelationEvidenceRow: [
                SimpleNamespace(relation_id=self.relation_cross_source, evidence_id=self.evidence_b),
                SimpleNamespace(relation_id=self.relation_outside_generation, evidence_id=self.evidence_outside),
            ],
            EvidenceRow: [
                SimpleNamespace(id=self.evidence_a, chunk_id=self.chunk_a, payload=evidence_a.model_dump(mode="json")),
                SimpleNamespace(id=self.evidence_b, chunk_id=self.chunk_b, payload=evidence_b.model_dump(mode="json")),
            ],
            ChunkRow: [
                SimpleNamespace(id=self.chunk_a, document_id=self.document_a),
                SimpleNamespace(id=self.chunk_b, document_id=self.document_b),
            ],
            DocumentRow: [
                SimpleNamespace(id=self.document_a, source_id=self.source_a),
                SimpleNamespace(id=self.document_b, source_id=self.source_b),
            ],
            ProvenanceRow: [
                SimpleNamespace(id=self.provenance_a, payload=provenance_a.model_dump(mode="json")),
                SimpleNamespace(id=self.provenance_b, payload=provenance_b.model_dump(mode="json")),
            ],
        }
        self.members = {
            "entity": {self.entity_a, self.entity_b},
            "mention": {self.mention_a, self.mention_b},
            "relation": {self.relation_cross_source, self.relation_outside_generation},
            "evidence": {self.evidence_a, self.evidence_b},
        }

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def get(self, model, _key):
        assert model is GenerationRow
        return self.generation

    async def scalars(self, statement):
        description = statement.column_descriptions[0]
        selected = description["entity"]
        model = selected if isinstance(selected, type) else selected.class_
        column = description["name"] if description["name"] != model.__name__ else None
        if model is GenerationEntityRow and column == "entity_id":
            rows = self.members["entity"]
        elif model is GenerationMentionRow and column == "mention_id":
            rows = self.members["mention"]
        elif model is GenerationRelationRow and column == "relation_id":
            rows = self.members["relation"]
        elif model is GenerationEvidenceRow and column == "evidence_id":
            rows = self.members["evidence"]
        elif model is RelationEvidenceRow and column == "evidence_id":
            rows = [row.evidence_id for row in self.rows[RelationEvidenceRow]]
        elif model is MentionRow:
            member_filters = [
                set(value)
                for value in statement.compile().params.values()
                if isinstance(value, (list, tuple, set))
            ]
            if self.members["mention"] in member_filters:
                rows = [row for row in self.rows[MentionRow] if row.id in self.members["mention"]]
            else:
                rows = self.rows[MentionRow]
        else:
            rows = self.rows.get(model, [])
        return SimpleNamespace(all=lambda: rows)


class GraphDatabase:
    def __init__(self, session: GraphSession) -> None:
        self.graph_session = session

    def session(self) -> GraphSession:
        return self.graph_session


class GraphKnowledgeStore:
    async def _members(self, session: GraphSession, _generation_id: str, kind: str) -> set[str]:
        return session.members[kind]


@pytest.mark.asyncio
async def test_graph_snapshot_closes_generation_and_filters_cross_source_facts() -> None:
    session = GraphSession()
    store = PostgresGraphStore(GraphDatabase(session), GraphKnowledgeStore())

    def context(source_ids: list[str] | None) -> QueryContext:
        return QueryContext(
            corpus_id=session.corpus_id,
            generation_id=session.generation_id,
            request_id=str(uuid4()),
            deadline_utc=datetime.now(UTC),
            source_ids=source_ids,
        )

    complete = await store.snapshot(context(None))
    assert {item.id for item in complete.evidence} == {session.evidence_a, session.evidence_b}
    assert {item.id for item in complete.mentions} == {session.mention_a, session.mention_b}
    assert session.old_mention not in {item.id for item in complete.mentions}
    assert {item.id for item in complete.relations} == {session.relation_cross_source}
    assert session.relation_outside_generation not in {item.id for item in complete.relations}

    filtered = await store.snapshot(context([session.source_a]))
    assert {item.id for item in filtered.evidence} == {session.evidence_a}
    assert {item.id for item in filtered.mentions} == {session.mention_a}
    assert {item.id for item in filtered.entities} == {session.entity_a}
    assert filtered.relations == []
