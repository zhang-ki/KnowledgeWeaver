from __future__ import annotations

import hashlib

import pytest

from knowledge_weaver.core.errors import ErrorCode, KnowledgeWeaverError
from knowledge_weaver.models import (
    Block,
    BlockKind,
    Chunk,
    Document,
    MarkdownLocator,
    StructureType,
)
from knowledge_weaver.storage.pipeline import PipelineService
from knowledge_weaver.storage.schema import BlockRow, ChunkRow, DocumentRow


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
