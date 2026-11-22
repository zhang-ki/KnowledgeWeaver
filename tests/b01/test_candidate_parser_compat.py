from __future__ import annotations

import hashlib
import html

import pytest

from knowledge_weaver.ingestion import ManifestBuilder, StructuralChunker, StructuralParser
from knowledge_weaver.models import ChunkConfig, MarkdownLocator, PythonLocator, SourceKind


def _compat_chunk_config() -> ChunkConfig:
    return ChunkConfig(
        target_chars=20,
        max_chars=30,
        overlap_chars=2,
        fingerprint="b01-candidate-compat-v1",
    )


_TEXT_HTML_CASES = (
    (
        "sources/specifications/rfc9114/rfc9114.txt",
        "first line\r\nGold quote stays exact\r\n",
        "Gold quote stays exact",
    ),
    ("sources/specifications/rfc7540/rfc7540.txt", "plain UTF-8 café\nsecond line\n", "café"),
    ("sources/specifications/rfc8725/rfc8725.txt", "third source\n", "third source"),
    ("sources/specifications/rfc6749/rfc6749.txt", "fourth source\n", "fourth source"),
    ("sources/specifications/rfc7519/rfc7519.txt", "fifth source\n", "fifth source"),
    (
        "sources/specifications/nist/sp800-63-3b.html",
        (
            "<!doctype html>\n<html>\n<head><title>hidden title</title></head>\n"
            "<body>\n<p>Gold quote stays exact</p>\n<script>secret()</script>\n</body>\n</html>"
        ),
        "Gold quote stays exact",
    ),
    (
        "sources/specifications/pep3156/pep-3156.html",
        "<html><body>Seven visible words.</body></html>",
        "Seven visible words.",
    ),
    (
        "sources/specifications/nist/sp800-63-4b.html",
        "<html><body>\n<h1>Eight heading</h1>\n<p>Eight body</p>\n</body></html>",
        "Eight body",
    ),
    (
        "sources/specifications/pep249/pep-0249.html",
        (
            "<html><body><p>Visible &amp; decoded text and &lt;angle&gt;</p>"
            "<span hidden>hidden attribute</span>"
            "<span style=\"display:none\">hidden style</span>"
            "<span aria-hidden=\"true\">hidden aria</span>"
            "<style>.x{display:none}</style></body></html>"
        ),
        "Visible & decoded text",
    ),
)


@pytest.mark.parametrize("relative_path,content,visible_quote", _TEXT_HTML_CASES)
def test_txt_html_file_formats_parse_with_source_line_locators(
    tmp_path, relative_path, content, visible_quote, request_factory, source_factory, run_immediate_async
) -> None:
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = content.encode("utf-8")
    target.write_bytes(raw)
    request = request_factory(tmp_path, SourceKind.markdown, source_factory(SourceKind.markdown))
    manifest_entry = request.source_version.files[0]

    parsed = run_immediate_async(StructuralParser().parse(request))

    assert not parsed.rejected_files
    assert len(parsed.documents) == 1
    document = parsed.documents[0]
    assert document.raw_hash == manifest_entry.raw_hash
    assert document.raw_hash == hashlib.sha256(raw).hexdigest()
    assert visible_quote in document.normalized_text
    assert document.blocks
    assert all(isinstance(block.locator, MarkdownLocator) for block in document.blocks)
    if relative_path.casefold().endswith(".txt"):
        assert document.normalized_text == content
        assert document.blocks[0].locator.line_start == 1
        assert document.blocks[0].locator.line_end == len(content.splitlines()) + int(
            content.endswith(("\n", "\r"))
        )
    else:
        assert "<html>" not in document.normalized_text
        assert "<body>" not in document.normalized_text
        assert "<p>" not in document.normalized_text
        if visible_quote.startswith("Visible & decoded"):
            assert "<angle>" in document.normalized_text
        assert "hidden title" not in document.normalized_text
        assert "secret()" not in document.normalized_text
        assert "hidden attribute" not in document.normalized_text
        assert "hidden style" not in document.normalized_text
        assert "hidden aria" not in document.normalized_text
        assert ".x{display:none}" not in document.normalized_text
        source_lines = content.replace("\r\n", "\n").replace("\r", "\n").splitlines()
        for block in document.blocks:
            location = block.locator
            assert location.line_start <= location.line_end <= len(source_lines)
            assert any(
                html.escape(text.strip(), quote=False) in "\n".join(
                    source_lines[location.line_start - 1 : location.line_end]
                )
                for text in block.text.splitlines()
                if text.strip()
            )


def test_eighteen_candidate_sources_manifest_and_parse_successfully(
    tmp_path, request_factory, source_factory, run_immediate_async
) -> None:
    markdown_root = tmp_path / "markdown-sources"
    for relative_path, content, _quote in _TEXT_HTML_CASES:
        target = markdown_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
    readme = markdown_root / "sources/repositories/rank_bm25/README.md"
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text("# Existing Markdown\n\nManifest remains valid.\n", encoding="utf-8")

    source = source_factory(SourceKind.markdown)
    manifest = ManifestBuilder().build(source, markdown_root, revision="fixture", dirty=False)
    assert manifest.source_version is not None
    assert len(manifest.source_version.files) == 10
    assert not manifest.rejected_files

    request = request_factory(markdown_root, SourceKind.markdown, source)
    parsed = run_immediate_async(StructuralParser().parse(request))
    assert len(parsed.documents) == 10
    assert not parsed.rejected_files

    python_paths = (
        "sources/repositories/pysyncobj/syncobj.py",
        "sources/repositories/pyjwt/api_jwt.py",
        "sources/repositories/fastapi/applications.py",
        "sources/repositories/rank_bm25/rank_bm25.py",
        "sources/repositories/uvicorn/config.py",
        "sources/repositories/pydantic/main.py",
        "sources/repositories/tiktoken/model.py",
        "sources/repositories/asgiref/compatibility.py",
    )
    python_root = tmp_path / "python-sources"
    for relative_path in python_paths:
        target = python_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def candidate():\n    return 'valid'\n", encoding="utf-8")
    python_source = source_factory(SourceKind.python_repo)
    python_manifest = ManifestBuilder().build(
        python_source, python_root, revision="fixture", dirty=False
    )
    assert python_manifest.source_version is not None
    assert len(python_manifest.source_version.files) == 8
    assert not python_manifest.rejected_files
    python_request = request_factory(python_root, SourceKind.python_repo, python_source)
    python_parsed = run_immediate_async(StructuralParser().parse(python_request))
    assert len(python_parsed.documents) == 8
    assert not python_parsed.rejected_files


@pytest.mark.parametrize(
    "relative_path,newline",
    (
        ("pysyncobj/syncobj.py", "\n"),
        ("pydantic/main.py", "\r\n"),
        ("fastapi/applications.py", "\n"),
    ),
)
def test_three_python_regression_files_parse_and_chunk_descending_multiline_columns(
    tmp_path, relative_path, newline, request_factory, source_factory, run_immediate_async
) -> None:
    source_text = newline.join(
        (
            "def parse_value():",
            "    result = (",
            '        "naïve ✓"',
            ")",
            "    return result",
            "",
        )
    )
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source_text.encode("utf-8"))
    request = request_factory(
        tmp_path, SourceKind.python_repo, source_factory(SourceKind.python_repo)
    )

    parsed = run_immediate_async(StructuralParser().parse(request))

    assert not parsed.rejected_files
    document = parsed.documents[0]
    assert document.raw_hash
    cross_line_locators = [
        block.locator
        for block in document.blocks
        if isinstance(block.locator, PythonLocator)
        and block.locator.line_start < block.locator.line_end
    ]
    assert cross_line_locators
    assert any(
        locator.col_start is not None
        and locator.col_end is not None
        and locator.col_end < locator.col_start
        for locator in cross_line_locators
    )

    chunks = StructuralChunker().split(
        document, _compat_chunk_config()
    )
    assert chunks
    span_locators = [
        span.locator
        for chunk in chunks
        for span in chunk.spans
        if isinstance(span.locator, PythonLocator)
    ]
    assert any(
        locator.line_start < locator.line_end
        and locator.col_start is not None
        and locator.col_end is not None
        and locator.col_end < locator.col_start
        for locator in span_locators
    )
    for chunk in chunks:
        for span in chunk.spans:
            assert chunk.text[span.chunk_start : span.chunk_end] == document.normalized_text[
                span.document_start : span.document_end
            ]


def test_python_locator_validates_columns_by_line_semantics() -> None:
    assert PythonLocator(path="x.py", line_start=1, line_end=2, col_start=8, col_end=1)
    assert PythonLocator(path="x.py", line_start=1, line_end=1, col_start=3, col_end=3)
    with pytest.raises(ValueError, match="col_end"):
        PythonLocator(path="x.py", line_start=2, line_end=2, col_start=8, col_end=3)
    with pytest.raises(ValueError):
        PythonLocator(path="x.py", line_start=3, line_end=2, col_start=0, col_end=0)
    with pytest.raises(ValueError):
        PythonLocator(path="x.py", line_start=1, line_end=2, col_start=-1, col_end=0)
    with pytest.raises(ValueError):
        PythonLocator(path="x.py", line_start=1, line_end=2, col_start=0, col_end=-1)


def test_text_inputs_keep_strict_utf8_decoding(
    tmp_path, request_factory, source_factory, run_immediate_async
) -> None:
    (tmp_path / "invalid.txt").write_bytes(b"valid prefix\xff")
    request = request_factory(tmp_path, SourceKind.markdown, source_factory(SourceKind.markdown))
    parsed = run_immediate_async(StructuralParser().parse(request))
    assert not parsed.documents
    assert len(parsed.rejected_files) == 1
    assert parsed.rejected_files[0].code == "PARSE_FAILED"


def test_txt_windows_preserve_page_coordinates_and_contiguous_source(
    tmp_path, request_factory, source_factory, run_immediate_async
) -> None:
    from knowledge_weaver.ingestion.chunker import StructuralChunker
    from knowledge_weaver.ingestion.text_coordinates import TextCoordinates
    from knowledge_weaver.models import ChunkConfig

    content = "A\r\nB\f\nC" + "x" * 64
    (tmp_path / "rfc.txt").write_bytes(content.encode())
    request = request_factory(tmp_path, SourceKind.markdown, source_factory(SourceKind.markdown))
    document, = run_immediate_async(StructuralParser().parse(request)).documents
    coordinates = TextCoordinates.from_text(document.normalized_text)
    assert coordinates.at(content.index("\f")) == (2, 1, 2)
    assert coordinates.at(content.index("C")) == (4, 2, 2)
    assert document.normalized_text == content
    config = ChunkConfig(fingerprint="test", max_chars=16, target_chars=9, overlap_chars=2)
    chunker = StructuralChunker()
    chunks = chunker.split(document, config)
    leaves = [chunk for chunk in chunks if chunk.is_leaf]
    assert chunks == chunker.split(document, config)
    assert [chunk.spans[0].document_start for chunk in leaves[:3]] == [0, 8, 16]
    quote_start, quote_end = 13, 20
    assert any(
        chunk.spans[0].document_start <= quote_start
        and quote_end <= chunk.spans[0].document_end
        for chunk in leaves
    )
    for chunk in leaves:
        span, = chunk.spans
        assert len(chunk.text) <= config.max_chars
        assert chunk.text == content[span.document_start:span.document_end]
        assert span.locator.line_start == coordinates.at(span.document_start)[0]
        assert span.locator.line_end == coordinates.at(span.document_end - 1)[0]
        assert chunk.chunker_fingerprint != config.fingerprint
