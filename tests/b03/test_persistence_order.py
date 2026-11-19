from __future__ import annotations

import pytest

from knowledge_weaver.models import Block, BlockKind, Document, MarkdownLocator
from knowledge_weaver.storage.pipeline import PipelineService
from knowledge_weaver.storage.schema import BlockRow, DocumentRow


class RecordingSession:
    def __init__(self) -> None:
        self.events: list[tuple[str, type[object] | None]] = []

    async def get(self, model: type[object], key: str) -> None:
        self.events.append(("get", model))

    def add(self, row: object) -> None:
        self.events.append(("add", type(row)))

    async def flush(self) -> None:
        self.events.append(("flush", None))


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
