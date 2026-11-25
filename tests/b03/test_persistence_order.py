from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from knowledge_weaver.core.errors import ErrorCode, KnowledgeWeaverError
from knowledge_weaver.core.ids import sha256_text
from knowledge_weaver.extraction import KnowledgeExtractor
from knowledge_weaver.models import (
    Block,
    BlockKind,
    Chunk,
    ChunkSpan,
    Document,
    EntityKind,
    ExtractionRequest,
    FileManifestEntry,
    Generation,
    GenerationState,
    MarkdownLocator,
    PythonLocator,
    RelationPredicate,
    Source,
    SourceKind,
    SourceVersion,
    StructureType,
)
from knowledge_weaver.storage.pipeline import (
    _EXTRACTION_ROUTING_PROFILE_VERSION,
    _PYTHON_SOURCE_INDEX_VERSION,
    PipelineService,
    _python_source_index_for_version,
    _source_extraction_profile_sha256,
)
from knowledge_weaver.storage.schema import (
    BlockRow,
    ChunkRow,
    DocumentRow,
    EntityRow,
    EvidenceRow,
    MentionRow,
    RelationEvidenceRow,
    RelationRow,
)

CORPUS_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_ID = "00000000-0000-4000-8000-000000000001"
SOURCE_VERSION_ID = "00000000-0000-4000-8000-000000000301"


class RecordingSession:
    def __init__(self) -> None:
        self.events: list[tuple[str, type[object] | None]] = []

    async def get(self, model: type[object], key: str) -> None:
        self.events.append(("get", model))

    def add(self, row: object) -> None:
        self.events.append(("add", type(row)))

    async def flush(self) -> None:
        self.events.append(("flush", None))


class RecordingChunkSession:
    def __init__(self, existing_ids: set[str] | None = None) -> None:
        self.rows = {chunk_id: object() for chunk_id in existing_ids or set()}
        self.pending: list[ChunkRow] = []
        self.added_ids: list[str] = []
        self.flushed_ids: list[list[str]] = []

    async def get(self, model: type[object], key: str) -> object | None:
        assert model is ChunkRow
        return self.rows.get(key)

    def add(self, row: object) -> None:
        assert isinstance(row, ChunkRow)
        self.pending.append(row)
        self.added_ids.append(row.id)

    async def flush(self) -> None:
        ids = [row.id for row in self.pending]
        for row in self.pending:
            assert row.parent_id is None or row.parent_id in self.rows
            self.rows[row.id] = row
        if ids:
            self.flushed_ids.append(ids)
        self.pending.clear()


class RecordingFactSession:
    def __init__(self) -> None:
        self.rows: dict[tuple[type[object], object], object] = {}

    @staticmethod
    def _key(model: type[object], key: object) -> tuple[type[object], object]:
        return model, key

    async def get(self, model: type[object], key: object) -> object | None:
        return self.rows.get(self._key(model, key))

    def add(self, row: object) -> None:
        if isinstance(row, RelationEvidenceRow):
            key: object = (row.relation_id, row.evidence_id)
        else:
            key = cast(Any, row).id
        self.rows[(type(row), key)] = row


def _python_request(
    text: str, *, path: str, document_id: str, chunk_id: str
) -> tuple[ExtractionRequest, Chunk]:
    line_count = text.count("\n") + 1
    document = Document(
        id=document_id,
        source_id=SOURCE_ID,
        relative_path=path,
        raw_hash=sha256_text(text),
        parser_fingerprint="synthetic-parser-v1",
        normalized_text=text,
    )
    chunk = Chunk(
        id=chunk_id,
        document_id=document_id,
        is_leaf=True,
        text=text,
        text_hash=sha256_text(text),
        spans=[
            ChunkSpan(
                chunk_start=0,
                chunk_end=len(text),
                document_start=0,
                document_end=len(text),
                locator=PythonLocator(
                    path=path,
                    line_start=1,
                    line_end=line_count,
                    qualname=None,
                    col_start=0,
                    col_end=0,
                ),
            )
        ],
        structure_type=StructureType.code,
        chunker_fingerprint="synthetic-chunker-v1",
    )
    return (
        ExtractionRequest(
            document=document,
            chunks=[chunk],
            producer_fingerprint="synthetic-extractor-v1",
        ),
        chunk,
    )


def _run_immediate(awaitable: Any) -> Any:
    try:
        awaitable.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("expected fake-session persistence to complete immediately")


def _chunk(chunk_id: str, *, parent_id: str | None = None, is_leaf: bool = True) -> Chunk:
    text = f"chunk {chunk_id}"
    return Chunk(
        id=chunk_id,
        document_id="00000000-0000-0000-0000-000000000001",
        parent_id=parent_id,
        is_leaf=is_leaf,
        text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest(),
        spans=[],
        structure_type=StructureType.code,
        chunker_fingerprint="test-chunker-v1",
    )


@pytest.mark.asyncio
async def test_document_parent_is_flushed_before_block_children() -> None:
    session = RecordingSession()
    document = Document(
        id="00000000-0000-0000-0000-000000000001",
        source_id="00000000-0000-0000-0000-000000000002",
        relative_path="note.md",
        raw_hash="0" * 64,
        parser_fingerprint="test-parser-v1",
        normalized_text="body",
        blocks=[
            Block(
                id="00000000-0000-0000-0000-000000000003",
                kind=BlockKind.paragraph,
                text="body",
                start=0,
                end=4,
                locator=MarkdownLocator(path="note.md", line_start=1, line_end=1),
            )
        ],
    )

    await PipelineService._persist_document(object.__new__(PipelineService), session, document)

    document_add = session.events.index(("add", DocumentRow))
    parent_flush = session.events.index(("flush", None))
    block_add = session.events.index(("add", BlockRow))
    assert document_add < parent_flush < block_add


@pytest.mark.asyncio
async def test_child_first_multi_level_chunks_are_flushed_parent_first() -> None:
    root = _chunk("00000000-0000-0000-0000-000000000002", is_leaf=False)
    middle = _chunk("00000000-0000-0000-0000-000000000003", parent_id=root.id, is_leaf=False)
    leaf = _chunk("00000000-0000-0000-0000-000000000004", parent_id=middle.id)
    session = RecordingChunkSession()

    await PipelineService._persist_chunks(object.__new__(PipelineService), session, [leaf, middle, root])

    assert session.added_ids == [root.id, middle.id, leaf.id]
    assert session.flushed_ids == [[root.id], [middle.id], [leaf.id]]


@pytest.mark.asyncio
async def test_chunk_can_use_parent_already_in_database() -> None:
    parent = _chunk("00000000-0000-0000-0000-000000000005", is_leaf=False)
    child = _chunk("00000000-0000-0000-0000-000000000006", parent_id=parent.id)
    session = RecordingChunkSession({parent.id})

    await PipelineService._persist_chunks(object.__new__(PipelineService), session, [child])

    assert session.added_ids == [child.id]
    assert session.flushed_ids == [[child.id]]


@pytest.mark.asyncio
async def test_chunk_with_missing_parent_is_rejected_before_persistence() -> None:
    child = _chunk(
        "00000000-0000-0000-0000-000000000007",
        parent_id="00000000-0000-0000-0000-000000000008",
    )
    session = RecordingChunkSession()

    with pytest.raises(KnowledgeWeaverError) as error:
        await PipelineService._persist_chunks(object.__new__(PipelineService), session, [child])

    assert error.value.code is ErrorCode.MISSING_REFERENCE
    assert session.added_ids == []
    assert session.flushed_ids == []


@pytest.mark.asyncio
async def test_cyclic_chunk_parents_are_rejected_before_persistence() -> None:
    first = _chunk(
        "00000000-0000-0000-0000-000000000009",
        parent_id="00000000-0000-0000-0000-000000000010",
        is_leaf=False,
    )
    second = _chunk(
        "00000000-0000-0000-0000-000000000010",
        parent_id=first.id,
        is_leaf=False,
    )
    session = RecordingChunkSession()

    with pytest.raises(KnowledgeWeaverError) as error:
        await PipelineService._persist_chunks(object.__new__(PipelineService), session, [first, second])

    assert error.value.code is ErrorCode.MISSING_REFERENCE
    assert session.added_ids == []
    assert session.flushed_ids == []


def test_python_extraction_profile_binds_source_index_version() -> None:
    source = Source(
        id=SOURCE_ID,
        corpus_id=CORPUS_ID,
        kind=SourceKind.python_repo,
        logical_uri="synthetic://python",
        display_name="synthetic Python source",
        created_at=datetime.now(UTC),
    )
    expected = sha256_text(
        json.dumps(
            {
                "profile_version": _EXTRACTION_ROUTING_PROFILE_VERSION,
                "source_kind": SourceKind.python_repo.value,
                "normative_source": False,
                "normative_prompt_version": None,
                "provider_configured": None,
                "python_source_index_version": _PYTHON_SOURCE_INDEX_VERSION,
                "python_ast_extraction_version": "kw-python-ast-extraction-v3",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )

    actual = _source_extraction_profile_sha256(
        source,
        normative_source=False,
        provider_configured=None,
    )

    assert actual == expected


def test_current_source_version_ast_edges_persist_in_generation_shape() -> None:
    caller_request, caller_chunk = _python_request(
        "from pkg.target import transform as tx\n\ndef caller():\n    result = tx()\n",
        path="pkg/caller.py",
        document_id="00000000-0000-4000-8000-000000000141",
        chunk_id="00000000-0000-4000-8000-000000000241",
    )
    target_request, target_chunk = _python_request(
        "def transform():\n    return None\n",
        path="pkg/target.py",
        document_id="00000000-0000-4000-8000-000000000142",
        chunk_id="00000000-0000-4000-8000-000000000242",
    )
    stale_request, _stale_chunk = _python_request(
        "def outside_version():\n    return None\n",
        path="pkg/stale.py",
        document_id="00000000-0000-4000-8000-000000000143",
        chunk_id="00000000-0000-4000-8000-000000000243",
    )
    source = Source(
        id=SOURCE_ID,
        corpus_id=CORPUS_ID,
        kind=SourceKind.python_repo,
        logical_uri="synthetic://python",
        display_name="synthetic Python source",
        created_at=datetime.now(UTC),
    )
    source_version = SourceVersion(
        id=SOURCE_VERSION_ID,
        source_id=SOURCE_ID,
        manifest_hash=sha256_text("synthetic-current-manifest"),
        dirty=False,
        files=[
            FileManifestEntry(
                relative_path="pkg/caller.py",
                raw_hash=caller_request.document.raw_hash,
                document_id=caller_request.document.id,
            ),
            FileManifestEntry(
                relative_path="pkg/target.py",
                raw_hash=target_request.document.raw_hash,
                document_id=target_request.document.id,
            ),
        ],
    )
    documents_by_id = {
        item.id: item
        for item in (
            caller_request.document,
            target_request.document,
            stale_request.document,
        )
    }
    index = _python_source_index_for_version(
        source,
        source_version,
        documents_by_id=documents_by_id,
        corpus_id=CORPUS_ID,
    )
    assert index is not None
    assert index.resolve(
        "pkg.target.transform",
        expected=EntityKind.function,
        source_id=SOURCE_ID,
        source_version_id=SOURCE_VERSION_ID,
        corpus_id=CORPUS_ID,
    ) is not None
    assert index.resolve(
        "pkg.stale.outside_version",
        expected=EntityKind.function,
        source_id=SOURCE_ID,
        source_version_id=SOURCE_VERSION_ID,
        corpus_id=CORPUS_ID,
    ) is None
    incomplete_index = _python_source_index_for_version(
        source,
        source_version,
        documents_by_id={
            caller_request.document.id: caller_request.document,
            stale_request.document.id: stale_request.document,
        },
        corpus_id=CORPUS_ID,
    )
    assert incomplete_index is not None
    assert incomplete_index.resolve(
        "pkg.target.transform",
        expected=EntityKind.function,
        source_id=SOURCE_ID,
        source_version_id=SOURCE_VERSION_ID,
        corpus_id=CORPUS_ID,
    ) is None
    extractor = KnowledgeExtractor(
        corpus_id=CORPUS_ID,
        source_kinds_by_id={SOURCE_ID: SourceKind.python_repo},
        _python_source_index=index,
        _python_source_version_id=source_version.id,
    )

    caller_result = _run_immediate(extractor.extract(caller_request))
    target_result = _run_immediate(extractor.extract(target_request))
    session = RecordingFactSession()
    members: dict[str, set[str]] = {}
    for result in (caller_result, target_result):
        _run_immediate(
            PipelineService._persist_extraction(
                object.__new__(PipelineService),
                session,
                result,
                {item.id: item for item in result.evidence},
                {item.id: item for item in result.provenance},
                members,
            )
        )

    target = next(
        entity
        for entity in target_result.entities
        if entity.canonical_name == "pkg.target.transform"
        and entity.kind is EntityKind.function
    )
    generation = Generation(
        id="00000000-0000-4000-8000-000000000401",
        corpus_id=CORPUS_ID,
        state=GenerationState.BUILDING,
        source_version_ids=[source_version.id],
        chunk_ids=sorted({caller_chunk.id, target_chunk.id}),
        entity_ids=sorted(members["entity"]),
        relation_ids=sorted(members["relation"]),
        index_manifest={},
        embedding_fingerprint="synthetic-none",
    )
    assert target.id in generation.entity_ids
    target_entity_rows = [
        row
        for (model, _key), row in session.rows.items()
        if model is EntityRow and isinstance(row, EntityRow) and row.id == target.id
    ]
    assert len(target_entity_rows) == 1
    call_relation = next(
        row
        for (model, _key), row in session.rows.items()
        if model is RelationRow and row.predicate == RelationPredicate.CALLS.value
    )
    assert call_relation.object_id == target.id
    persisted_call_evidence_ids = {
        row.evidence_id
        for (model, _key), row in session.rows.items()
        if model is RelationEvidenceRow and row.relation_id == call_relation.id
    }
    assert persisted_call_evidence_ids
    target_mentions = [
        row
        for (model, _key), row in session.rows.items()
        if model is MentionRow and row.entity_id == target.id
    ]
    assert len(target_mentions) == 2
    evidence_by_id = {
        row.id: row
        for (model, _key), row in session.rows.items()
        if model is EvidenceRow
    }
    assert any(
        evidence_by_id[evidence_id].payload["quote"] == "result = tx()"
        for evidence_id in persisted_call_evidence_ids
    )
    assert {
        evidence_by_id[row.evidence_id].chunk_id for row in target_mentions
    } == {caller_chunk.id, target_chunk.id}
    assert call_relation.id in generation.relation_ids
