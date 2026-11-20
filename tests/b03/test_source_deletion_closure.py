from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from knowledge_weaver.core.errors import ErrorCode, KnowledgeWeaverError
from knowledge_weaver.models import (
    Chunk,
    ChunkSpan,
    DeleteSourceRequest,
    Evidence,
    MarkdownLocator,
    StructureType,
)
from knowledge_weaver.storage.pipeline import PipelineService
from knowledge_weaver.storage.schema import (
    ChunkRow,
    CorpusRow,
    EvidenceRow,
    GenerationRow,
    JobRow,
    MentionRow,
    ProvenanceRow,
    RelationEvidenceRow,
    RelationRow,
    SourceRow,
    SourceVersionDocumentRow,
    SourceVersionRow,
)

CORPUS = "00000000-0000-0000-0000-000000000001"
SOURCE_A = "00000000-0000-0000-0000-000000000002"
SOURCE_B = "00000000-0000-0000-0000-000000000003"
VERSION_A = "00000000-0000-0000-0000-000000000004"
VERSION_B = "00000000-0000-0000-0000-000000000005"
DOCUMENT_A = "00000000-0000-0000-0000-000000000006"
DOCUMENT_B = "00000000-0000-0000-0000-000000000007"
CHUNK_A = "00000000-0000-0000-0000-000000000008"
CHUNK_B = "00000000-0000-0000-0000-000000000009"
EVIDENCE_A = "00000000-0000-0000-0000-000000000010"
EVIDENCE_B = "00000000-0000-0000-0000-000000000011"
PROVENANCE_A = "00000000-0000-0000-0000-000000000012"
PROVENANCE_B = "00000000-0000-0000-0000-000000000013"
SHARED_ENTITY = "00000000-0000-0000-0000-000000000014"
SOURCE_A_ONLY_ENTITY = "00000000-0000-0000-0000-000000000020"
MENTION_A = "00000000-0000-0000-0000-000000000015"
MENTION_B = "00000000-0000-0000-0000-000000000016"
RELATION_A = "00000000-0000-0000-0000-000000000017"
RELATION_B = "00000000-0000-0000-0000-000000000021"
GENERATION = "00000000-0000-0000-0000-000000000018"


class ScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def all(self) -> list[object]:
        return self.rows


class RemovalSession:
    def __init__(
        self,
        mention_a: object,
        mention_b: object,
        *,
        relation_evidence_rows: list[object] | None = None,
        relation_rows: list[object] | None = None,
    ) -> None:
        self.versions = [
            SimpleNamespace(id=VERSION_A, source_id=SOURCE_A),
            SimpleNamespace(id=VERSION_B, source_id=SOURCE_B),
        ]
        self.mention_a = mention_a
        self.mention_b = mention_b
        self.relation_evidence_rows = relation_evidence_rows or [
            SimpleNamespace(relation_id=RELATION_A, evidence_id=EVIDENCE_A)
        ]
        self.removed_evidence_ids = {EVIDENCE_A}
        self.deleted_evidence_relation_ids: list[str] | None = None
        self.relation_rows = relation_rows or []
        self.mention_entity_calls = 0
        self.relation_rows_queried = False
        text = "remaining source supports shared entity"
        locator = MarkdownLocator(path="b.md", line_start=1, line_end=1)
        chunk = Chunk(
            id=CHUNK_B,
            document_id=DOCUMENT_B,
            is_leaf=True,
            text=text,
            text_hash=hashlib.sha256(text.encode()).hexdigest(),
            spans=[ChunkSpan(chunk_start=0, chunk_end=len(text), document_start=0, document_end=len(text), locator=locator)],
            structure_type=StructureType.paragraph,
            chunker_fingerprint="test-chunker-v1",
        )
        evidence = Evidence(
            id=EVIDENCE_B,
            chunk_id=CHUNK_B,
            start=0,
            end=9,
            quote="remaining",
            locator=locator,
            provenance_id=PROVENANCE_B,
        )
        self.chunk_row = SimpleNamespace(id=CHUNK_B, document_id=DOCUMENT_B, payload=chunk.model_dump(mode="json"))
        self.evidence_row = SimpleNamespace(
            id=EVIDENCE_B,
            chunk_id=CHUNK_B,
            provenance_id=PROVENANCE_B,
            payload=evidence.model_dump(mode="json"),
        )

    async def scalars(self, statement) -> ScalarResult:
        description = statement.column_descriptions[0]
        selected = description["entity"]
        model = selected if isinstance(selected, type) else selected.class_
        column = description["name"] if description["name"] != model.__name__ else None
        if model is SourceVersionRow:
            rows = self.versions
        elif model is SourceVersionDocumentRow:
            rows = [DOCUMENT_A]
        elif model is ChunkRow and column == "id":
            rows = [CHUNK_A]
        elif model is ChunkRow:
            rows = [self.chunk_row]
        elif model is EvidenceRow and column == "id":
            rows = [EVIDENCE_A]
        elif model is EvidenceRow:
            rows = [self.evidence_row]
        elif model is ProvenanceRow:
            rows = [PROVENANCE_A]
        elif model is MentionRow and column == "id":
            rows = [MENTION_A]
        elif model is MentionRow and column == "entity_id":
            self.mention_entity_calls += 1
            if self.mention_entity_calls == 1:
                rows = [self.mention_a.entity_id]
            else:
                rows = [self.mention_b.entity_id] if self.mention_b.entity_id == self.mention_a.entity_id else []
        elif model is MentionRow:
            rows = [self.mention_b]
        elif model is RelationEvidenceRow:
            if column == "relation_id":
                rows = [
                    relation_evidence.relation_id
                    for relation_evidence in self.relation_evidence_rows
                    if relation_evidence.evidence_id in self.removed_evidence_ids
                ]
                self.deleted_evidence_relation_ids = rows
            else:
                rows = self.relation_evidence_rows
        elif model is RelationRow:
            self.relation_rows_queried = True
            rows = self.relation_rows
        else:
            raise AssertionError(f"Unexpected query: {model.__name__}.{column}")
        return ScalarResult(rows)


@pytest.mark.asyncio
async def test_remove_source_keeps_shared_entity_and_validates_remaining_generation() -> None:
    mention_a = SimpleNamespace(id=MENTION_A, entity_id=SHARED_ENTITY, evidence_id=EVIDENCE_A)
    mention_b = SimpleNamespace(id=MENTION_B, entity_id=SHARED_ENTITY, evidence_id=EVIDENCE_B)
    session = RemovalSession(mention_a, mention_b)
    pipeline = object.__new__(PipelineService)
    old_members = {
        "source_version": {VERSION_A, VERSION_B},
        "document": {DOCUMENT_A, DOCUMENT_B},
        "chunk": {CHUNK_A, CHUNK_B},
        "evidence": {EVIDENCE_A, EVIDENCE_B},
        "provenance": {PROVENANCE_A, PROVENANCE_B},
        "entity": {SHARED_ENTITY},
        "mention": {MENTION_A, MENTION_B},
        "relation": {RELATION_A},
    }

    members = await PipelineService._replace_source_members(pipeline, session, old_members, SOURCE_A, GENERATION)
    await PipelineService._validate_generation(pipeline, session, members)

    assert members["source_version"] == {VERSION_B}
    assert members["document"] == {DOCUMENT_B}
    assert members["chunk"] == {CHUNK_B}
    assert members["evidence"] == {EVIDENCE_B}
    assert members["provenance"] == {PROVENANCE_B}
    assert members["entity"] == {SHARED_ENTITY}
    assert members["mention"] == {MENTION_B}
    assert members["relation"] == set()


@pytest.mark.asyncio
async def test_remove_source_prunes_relation_with_deleted_only_entity_endpoint() -> None:
    mention_a = SimpleNamespace(id=MENTION_A, entity_id=SOURCE_A_ONLY_ENTITY, evidence_id=EVIDENCE_A)
    mention_b = SimpleNamespace(id=MENTION_B, entity_id=SHARED_ENTITY, evidence_id=EVIDENCE_B)
    relation_b = SimpleNamespace(
        id=RELATION_B,
        subject_id=SOURCE_A_ONLY_ENTITY,
        object_id=SHARED_ENTITY,
        provenance_id=PROVENANCE_B,
    )
    relation_evidence_b = SimpleNamespace(relation_id=RELATION_B, evidence_id=EVIDENCE_B)
    session = RemovalSession(
        mention_a,
        mention_b,
        relation_evidence_rows=[relation_evidence_b],
        relation_rows=[relation_b],
    )
    pipeline = object.__new__(PipelineService)
    old_members = {
        "source_version": {VERSION_A, VERSION_B},
        "document": {DOCUMENT_A, DOCUMENT_B},
        "chunk": {CHUNK_A, CHUNK_B},
        "evidence": {EVIDENCE_A, EVIDENCE_B},
        "provenance": {PROVENANCE_A, PROVENANCE_B},
        "entity": {SOURCE_A_ONLY_ENTITY, SHARED_ENTITY},
        "mention": {MENTION_A, MENTION_B},
        "relation": {RELATION_B},
    }

    members = await PipelineService._replace_source_members(pipeline, session, old_members, SOURCE_A, GENERATION)
    await PipelineService._validate_generation(pipeline, session, members)

    assert session.deleted_evidence_relation_ids == []
    assert relation_evidence_b.evidence_id == EVIDENCE_B
    assert session.relation_rows_queried
    assert members["entity"] == {SHARED_ENTITY}
    assert members["mention"] == {MENTION_B}
    assert members["evidence"] == {EVIDENCE_B}
    assert members["relation"] == set()


class DeleteSession:
    def __init__(self, corpus: CorpusRow, source: SourceRow, active: GenerationRow, version: SourceVersionRow) -> None:
        self.corpus = corpus
        self.source = source
        self.active = active
        self.version = version
        self.added: list[object] = []
        self.executed: list[object] = []
        self.committed = False

    async def get(self, model, key):
        if model is JobRow:
            return None
        if model is CorpusRow:
            return self.corpus
        if model is SourceRow:
            return self.source
        if model is GenerationRow:
            return self.active
        if model is SourceVersionRow:
            return self.version
        raise AssertionError(f"Unexpected get: {model.__name__}")

    async def scalars(self, statement) -> ScalarResult:
        raise AssertionError("Member queries should be supplied by the test double.")

    def add(self, row: object) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        pass

    async def execute(self, statement):
        self.executed.append(statement)
        raise AssertionError("CAS must not run after closure validation fails.")

    async def commit(self) -> None:
        self.committed = True


class DeleteDatabase:
    def __init__(self, session: DeleteSession) -> None:
        self._session = session

    @asynccontextmanager
    async def session(self):
        yield self._session


@pytest.mark.asyncio
async def test_delete_closure_failure_never_reaches_cas_or_retires_active_generation() -> None:
    corpus = CorpusRow(id=CORPUS, active_generation_id=GENERATION, created_at=datetime.now(UTC))
    source = SourceRow(id=SOURCE_A, corpus_id=CORPUS, kind="markdown", logical_uri="a", display_name="a", created_at=corpus.created_at)
    active = GenerationRow(
        id=GENERATION,
        corpus_id=CORPUS,
        state="READY",
        source_version_ids=[VERSION_A],
        index_manifest={},
        embedding_fingerprint="test-embedding-v1",
        generation_hash="0" * 64,
        created_at=corpus.created_at,
    )
    version = SourceVersionRow(id=VERSION_A, source_id=SOURCE_A, manifest_hash="0" * 64, dirty=False, files=[])
    session = DeleteSession(corpus, source, active, version)
    pipeline = object.__new__(PipelineService)
    pipeline.database = DeleteDatabase(session)

    async def member_rows(_session, _generation_id):
        return {"source_version": {VERSION_A}, "document": set(), "chunk": set(), "evidence": set(), "provenance": set(), "entity": set(), "mention": set(), "relation": set()}

    async def replace_members(_session, old, _source_id, _generation_id):
        return old

    async def fail_validation(_session, _members):
        raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "synthetic unclosed generation")

    pipeline._member_rows = member_rows
    pipeline._replace_source_members = replace_members
    pipeline._validate_generation = fail_validation
    request = DeleteSourceRequest(corpus_id=CORPUS, source_id=SOURCE_A, idempotency_key="delete-closure-failure")

    with pytest.raises(KnowledgeWeaverError, match="synthetic unclosed generation"):
        await PipelineService._delete_source_transaction(pipeline, request, "00000000-0000-0000-0000-000000000019")

    assert not session.executed
    assert not session.committed
    assert corpus.active_generation_id == GENERATION
    assert active.state == "READY"
