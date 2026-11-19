from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_weaver.core.ids import document_id, sha256_bytes, source_version_id
from knowledge_weaver.ingestion import StructuralChunker, StructuralParser
from knowledge_weaver.ingestion.chunker import ChunkLimitExceeded
from knowledge_weaver.ingestion.fingerprints import parser_fingerprint
from knowledge_weaver.models import (
    ChunkConfig,
    FileManifestEntry,
    ParseLimits,
    ParseRequest,
    SourceKind,
    SourceVersion,
)
from knowledge_weaver.ports import Chunker, Parser


def _chunk_config(*, target: int = 80, maximum: int = 100, overlap: int = 10) -> ChunkConfig:
    return ChunkConfig(
        target_chars=target,
        max_chars=maximum,
        overlap_chars=overlap,
        fingerprint="b01-test-chunker-v1",
    )


def _assert_source_spans(document, chunks) -> None:
    for chunk in chunks:
        for span in chunk.spans:
            assert chunk.text[span.chunk_start : span.chunk_end] == document.normalized_text[
                span.document_start : span.document_end
            ]


def test_components_match_frozen_parser_and_chunker_ports() -> None:
    assert isinstance(StructuralParser(), Parser)
    assert isinstance(StructuralChunker(), Chunker)


def test_python_ast_unicode_columns_nested_symbols_and_reuse(
    tmp_path, fixture_copier, request_factory, source_factory, run_immediate_async
) -> None:
    fixture_copier("python_unicode.py", tmp_path)
    fixture_copier("python_nested.py", tmp_path)
    request = request_factory(tmp_path, SourceKind.python_repo, source_factory(SourceKind.python_repo))
    parser = StructuralParser()

    first = run_immediate_async(parser.parse_detailed(request))
    second = run_immediate_async(parser.parse_detailed(request))
    assert not first.parsed_source.rejected_files
    assert len(first.parsed_source.documents) == 2
    assert second.reused_document_ids == tuple(
        document.id for document in first.parsed_source.documents
    )

    unicode_document = next(
        document for document in first.parsed_source.documents if document.relative_path.endswith("python_unicode.py")
    )
    function = next(
        block
        for block in unicode_document.blocks
        if getattr(block.locator, "qualname", None) == "café"
    )
    return_statement = next(
        block
        for block in unicode_document.blocks
        if getattr(block.locator, "qualname", "").startswith("café::<stmt:")
    )
    assert function.text == unicode_document.normalized_text[function.start : function.end]
    assert "✓ {naïve}" in function.text
    assert return_statement.locator.col_start == 4
    assert "naïve" in return_statement.text

    nested_document = next(
        document for document in first.parsed_source.documents if document.relative_path.endswith("python_nested.py")
    )
    chunks = StructuralChunker().split(nested_document, _chunk_config())
    outer = next(chunk for chunk in chunks if chunk.qualname == "outer")
    inner = next(chunk for chunk in chunks if chunk.qualname == "outer.inner")
    large_function = next(chunk for chunk in chunks if chunk.qualname == "very_large_function")
    assert not outer.is_leaf
    assert inner.parent_id == outer.id
    assert not large_function.is_leaf
    assert all(len(chunk.text) <= 100 for chunk in chunks if chunk.is_leaf)
    _assert_source_spans(nested_document, chunks)


def test_python_syntax_error_is_reported_without_importing_source(
    tmp_path, request_factory, source_factory, run_immediate_async
) -> None:
    (tmp_path / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    request = request_factory(tmp_path, SourceKind.python_repo, source_factory(SourceKind.python_repo))
    result = run_immediate_async(StructuralParser().parse(request))
    assert result.documents == []
    assert result.rejected_files[0].code == "PARSE_FAILED"
    assert "line 1" in result.rejected_files[0].message


def test_markdown_headings_fences_and_line_locators(
    tmp_path, fixture_copier, request_factory, source_factory, run_immediate_async
) -> None:
    fixture_copier("markdown_sections.md", tmp_path)
    fixture_copier("markdown_fence.md", tmp_path)
    request = request_factory(tmp_path, SourceKind.markdown, source_factory(SourceKind.markdown))
    result = run_immediate_async(StructuralParser().parse(request))
    assert not result.rejected_files
    assert len(result.documents) == 2

    sections = next(item for item in result.documents if item.relative_path.endswith("markdown_sections.md"))
    headings = [block for block in sections.blocks if block.kind.value == "heading"]
    assert [block.heading_path for block in headings] == [["Root heading"], ["Root heading", "Nested heading"]]
    nested_paragraph = next(block for block in sections.blocks if "nested paragraph" in block.text.casefold())
    assert nested_paragraph.locator.line_start == 7
    assert nested_paragraph.locator.line_end == 7
    _assert_source_spans(sections, StructuralChunker().split(sections, _chunk_config()))

    fenced = next(item for item in result.documents if item.relative_path.endswith("markdown_fence.md"))
    code = next(block for block in fenced.blocks if block.kind.value == "code")
    assert code.locator.line_start == 3
    assert code.text.startswith("```python")
    assert "def greeting" in code.text


def test_docx_body_order_table_cells_and_synthetic_separators(
    tmp_path, request_factory, source_factory, run_immediate_async
) -> None:
    pytest.importorskip("docx", reason="python-docx is absent from the alternate Python runtime")
    from docx import Document as create_docx

    document = create_docx()
    document.add_paragraph("Before table")
    document.add_heading("A section", level=1)
    document.add_paragraph("Between heading and table")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Name"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "counter"
    table.cell(1, 1).text = "42"
    document.add_paragraph("After table")

    import io

    buffer = io.BytesIO()
    document.save(buffer)
    root = tmp_path / "word"
    root.mkdir()
    (root / "sample.docx").write_bytes(buffer.getvalue())

    second_document = create_docx()
    second_document.add_heading("Second document", level=1)
    second_document.add_paragraph("A separate source remains addressable.")
    second_buffer = io.BytesIO()
    second_document.save(second_buffer)
    (root / "second.docx").write_bytes(second_buffer.getvalue())

    request = request_factory(root, SourceKind.docx, source_factory(SourceKind.docx))
    parsed = run_immediate_async(StructuralParser().parse(request))
    assert not parsed.rejected_files
    assert len(parsed.documents) == 2
    result = next(item for item in parsed.documents if item.relative_path == "sample.docx")
    assert [block.kind.value for block in result.blocks] == [
        "paragraph",
        "heading",
        "paragraph",
        "table",
        "paragraph",
    ]
    table_block = next(block for block in result.blocks if block.kind.value == "table")
    assert table_block.table_cells[0].text == "Name"
    assert table_block.table_cells[-1].text == "42"
    second_result = next(item for item in parsed.documents if item.relative_path == "second.docx")
    assert second_result.blocks[0].locator.block == 0
    assert second_result.blocks[0].heading_path == ["Second document"]

    chunks = StructuralChunker().split(
        result,
        _chunk_config(target=4, maximum=6, overlap=1),
    )
    table_parent = next(
        chunk for chunk in chunks if not chunk.is_leaf and chunk.structure_type.value == "table"
    )
    table_leaves = [chunk for chunk in chunks if chunk.parent_id == table_parent.id]
    assert table_leaves
    assert all(span.locator.kind == "docx" for chunk in table_leaves for span in chunk.spans)
    _assert_source_spans(result, chunks)
    for chunk in table_leaves:
        for position, character in enumerate(chunk.text):
            if character == "\t":
                assert not any(span.chunk_start <= position < span.chunk_end for span in chunk.spans)


def test_pdf_pages_headings_tables_and_bbox_are_preserved(
    tmp_path, monkeypatch, request_factory, source_factory, run_immediate_async
) -> None:
    import knowledge_weaver.ingestion.parser as parser_module

    (tmp_path / "pages.pdf").write_bytes(b"synthetic pdf bytes")
    request = request_factory(tmp_path, SourceKind.pdf, source_factory(SourceKind.pdf))

    class FakeBBox:
        def __init__(self, left: float, top: float, right: float, bottom: float) -> None:
            self.l = left
            self.t = top
            self.r = right
            self.b = bottom

        def to_top_left_origin(self, *, page_height: float):
            return FakeBBox(self.l, page_height - self.b, self.r, page_height - self.t)

    class FakeDoclingDocument:
        def __init__(self) -> None:
            self.pages = {
                1: SimpleNamespace(size=SimpleNamespace(height=100.0)),
                2: SimpleNamespace(size=SimpleNamespace(height=100.0)),
            }
            heading = SimpleNamespace(
                label=SimpleNamespace(value="section_header"),
                text="Methods",
                level=1,
                prov=[SimpleNamespace(page_no=1, bbox=FakeBBox(1, 10, 40, 20))],
            )
            paragraph = SimpleNamespace(
                label=SimpleNamespace(value="text"),
                text="A paragraph on page one.",
                prov=[SimpleNamespace(page_no=1, bbox=FakeBBox(2, 22, 80, 32))],
            )
            table = SimpleNamespace(
                label=SimpleNamespace(value="table"),
                text="",
                data=SimpleNamespace(
                    table_cells=[
                        SimpleNamespace(start_row_offset_idx=0, start_col_offset_idx=0, text="Year"),
                        SimpleNamespace(start_row_offset_idx=0, start_col_offset_idx=1, text="Value"),
                        SimpleNamespace(start_row_offset_idx=1, start_col_offset_idx=0, text="2026"),
                        SimpleNamespace(start_row_offset_idx=1, start_col_offset_idx=1, text="8"),
                    ]
                ),
                prov=[SimpleNamespace(page_no=2, bbox=FakeBBox(5, 60, 90, 90))],
            )
            self._items = [(heading, 0), (paragraph, 0), (table, 0)]

        def iterate_items(self):
            return iter(self._items)

    fake_result = SimpleNamespace(
        document=FakeDoclingDocument(),
        status=SimpleNamespace(value="success"),
        errors=[],
    )
    calls: list[bool] = []

    def fake_run(_path: Path, *, table_structure: bool):
        calls.append(table_structure)
        return fake_result

    monkeypatch.setattr(parser_module, "_run_docling", fake_run)
    parsed = run_immediate_async(StructuralParser().parse(request))
    assert not parsed.rejected_files
    assert calls == [True]
    result = parsed.documents[0]
    heading, paragraph, table_block = result.blocks
    assert heading.locator.page == 1
    assert paragraph.heading_path == ["Methods"]
    assert table_block.locator.page == 2
    assert table_block.locator.bbox == (5.0, 10.0, 90.0, 40.0)
    assert len(table_block.table_cells) == 4
    chunks = StructuralChunker().split(result, _chunk_config(target=4, maximum=6, overlap=1))
    _assert_source_spans(result, chunks)


def test_pdf_table_model_failure_falls_back_with_warning(
    tmp_path, monkeypatch, request_factory, source_factory, run_immediate_async
) -> None:
    import knowledge_weaver.ingestion.parser as parser_module

    (tmp_path / "plain.pdf").write_bytes(b"synthetic pdf bytes")
    request = request_factory(tmp_path, SourceKind.pdf, source_factory(SourceKind.pdf))
    fake_result = SimpleNamespace(
        document=SimpleNamespace(
            pages={},
            iterate_items=lambda: iter(
                [
                    (
                        SimpleNamespace(
                            label=SimpleNamespace(value="text"),
                            text="Offline fallback text",
                            prov=[SimpleNamespace(page_no=1, bbox=None)],
                        ),
                        0,
                    )
                ]
            ),
        ),
        status=SimpleNamespace(value="success"),
        errors=[],
    )
    calls: list[bool] = []

    def fake_run(_path: Path, *, table_structure: bool):
        calls.append(table_structure)
        if table_structure:
            raise RuntimeError("local table weights unavailable")
        return fake_result

    monkeypatch.setattr(parser_module, "_run_docling", fake_run)
    result = run_immediate_async(StructuralParser().parse(request))
    assert not result.rejected_files
    assert calls == [True, False]
    assert any("PDF_TABLE_STRUCTURE_UNAVAILABLE" in warning for warning in result.documents[0].warnings)


def test_parser_rejects_manifest_path_escape_and_changed_content(
    tmp_path, source_factory, run_immediate_async
) -> None:
    source = source_factory(SourceKind.markdown)
    raw_hash = sha256_bytes(b"outside")
    fingerprint = parser_fingerprint(SourceKind.markdown)
    entry = FileManifestEntry(
        relative_path="../outside.md",
        raw_hash=raw_hash,
        document_id=document_id(source.id, "../outside.md", raw_hash, fingerprint),
    )
    version = SourceVersion(
        id=source_version_id(source.id, "b" * 64),
        source_id=source.id,
        manifest_hash="b" * 64,
        revision="synthetic-revision",
        dirty=False,
        files=[entry],
    )
    request = ParseRequest(
        source=source,
        source_version=version,
        allowed_root=str(tmp_path.resolve()),
        limits=ParseLimits(),
    )
    result = run_immediate_async(StructuralParser().parse(request))
    assert result.documents == []
    assert result.rejected_files[0].code == "PATH_DENIED"

    (tmp_path / "changed.md").write_text("current", encoding="utf-8")
    actual_hash = sha256_bytes(b"old")
    actual_entry = FileManifestEntry(
        relative_path="changed.md",
        raw_hash=actual_hash,
        document_id=document_id(source.id, "changed.md", actual_hash, fingerprint),
    )
    changed_version = SourceVersion(
        id=source_version_id(source.id, "c" * 64),
        source_id=source.id,
        manifest_hash="c" * 64,
        revision="synthetic-revision",
        dirty=False,
        files=[actual_entry],
    )
    changed_request = request.model_copy(update={"source_version": changed_version})
    changed_result = run_immediate_async(StructuralParser().parse(changed_request))
    assert changed_result.documents == []
    assert changed_result.rejected_files[0].code == "MANIFEST_MISMATCH"


def test_docx_bad_archive_is_rejected(
    tmp_path, request_factory, source_factory, run_immediate_async
) -> None:
    root = tmp_path / "bad-word"
    root.mkdir()
    (root / "broken.docx").write_bytes(b"not a zip archive")
    request = request_factory(root, SourceKind.docx, source_factory(SourceKind.docx))
    result = run_immediate_async(StructuralParser().parse(request))
    assert result.documents == []
    assert result.rejected_files[0].code == "PARSE_FAILED"


def test_duplicate_leaf_text_is_grouped_without_collapsing_documents(
    tmp_path, request_factory, source_factory, run_immediate_async
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "one.md").write_text("same evidence phrase\n", encoding="utf-8")
    (second_root / "two.md").write_text("same evidence phrase\n", encoding="utf-8")
    source = source_factory(SourceKind.markdown)
    first = run_immediate_async(
        StructuralParser().parse(request_factory(first_root, SourceKind.markdown, source))
    )
    second = run_immediate_async(
        StructuralParser().parse(request_factory(second_root, SourceKind.markdown, source))
    )
    documents = first.documents + second.documents
    assert len(documents) == 2
    assert documents[0].id != documents[1].id

    groups = StructuralChunker().split_many(documents, _chunk_config())
    first_leaf = next(chunk for chunk in groups[0] if chunk.is_leaf)
    second_leaf = next(chunk for chunk in groups[1] if chunk.is_leaf)
    assert first_leaf.duplicate_group is not None
    assert first_leaf.duplicate_group == second_leaf.duplicate_group


def test_chunker_reports_leaf_limit_without_truncating(
    tmp_path, request_factory, source_factory, run_immediate_async
) -> None:
    (tmp_path / "long.md").write_text("one two three four five six seven eight", encoding="utf-8")
    request = request_factory(tmp_path, SourceKind.markdown, source_factory(SourceKind.markdown))
    document = run_immediate_async(StructuralParser().parse(request)).documents[0]

    try:
        StructuralChunker(max_leaf_chunks=1).split(
            document, _chunk_config(target=5, maximum=8, overlap=0)
        )
    except ChunkLimitExceeded:
        pass
    else:
        raise AssertionError("The configured leaf limit must fail explicitly instead of truncating.")
