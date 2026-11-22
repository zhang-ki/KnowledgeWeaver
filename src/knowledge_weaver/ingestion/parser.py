from __future__ import annotations

import ast
import asyncio
import contextlib
import html
import io
import os
import re
import threading
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, ClassVar

from pydantic import ValidationError

from knowledge_weaver.core.ids import block_id, document_id, sha256_bytes, sha256_text
from knowledge_weaver.models import (
    Block,
    BlockKind,
    DocxLocator,
    Locator,
    MarkdownLocator,
    ParsedSource,
    ParseRequest,
    PdfLocator,
    PythonLocator,
    RejectedFile,
    SourceKind,
    TableCell,
)
from knowledge_weaver.models import Document as DocumentDto

from .fingerprints import parser_fingerprint
from .paths import (
    ArchiveSafetyError,
    PathSafetyError,
    is_supported_path,
    normalize_root,
    resolve_relative_file,
    validate_docx_archive,
)
from .tables import render_table_cells
from .text_coordinates import TextCoordinates


@dataclass(frozen=True)
class ParseExecution:
    parsed_source: ParsedSource
    reused_document_ids: tuple[str, ...]


@dataclass(frozen=True)
class _BlockSpec:
    kind: BlockKind
    text: str
    locator: Locator
    heading_path: tuple[str, ...] = ()
    table_cells: tuple[TableCell, ...] = ()


class _FileParseProblem(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


class _VisibleHTMLTextParser(HTMLParser):
    """Collect deterministic visible text blocks with original HTML line ranges."""

    _HIDDEN_TAGS: ClassVar[frozenset[str]] = frozenset(
        {"head", "script", "style", "template", "noscript"}
    )
    _BLOCK_TAGS: ClassVar[frozenset[str]] = frozenset({
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
        "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
        "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
        "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.blocks: list[tuple[BlockKind, str, int, int]] = []
        self._parts: list[str] = []
        self._start_line: int | None = None
        self._end_line: int | None = None
        self._kind = BlockKind.paragraph
        self._hidden_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        hidden_attr = any(
            name.casefold() == "hidden"
            or (name.casefold() == "aria-hidden" and (value or "").casefold() == "true")
            or (
                name.casefold() == "style"
                and bool(
                    re.search(
                        r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden|content-visibility\s*:\s*hidden)\s*(?:;|$)",
                        value or "",
                        re.IGNORECASE,
                    )
                )
            )
            for name, value in attrs
        )
        if self._hidden_stack:
            if tag in self._HIDDEN_TAGS or hidden_attr:
                self._hidden_stack.append(tag)
            return
        if tag in self._HIDDEN_TAGS or hidden_attr:
            self._flush()
            self._hidden_stack.append(tag)
            return
        if tag in self._BLOCK_TAGS:
            self._flush()
        if tag in {f"h{level}" for level in range(1, 7)}:
            self._kind = BlockKind.heading
        elif tag == "pre":
            self._kind = BlockKind.code

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if self._hidden_stack:
            if tag in self._hidden_stack:
                index = len(self._hidden_stack) - 1 - self._hidden_stack[::-1].index(tag)
                del self._hidden_stack[index:]
            return
        if tag in self._BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not data or self._hidden_stack:
            return
        line, _column = self.getpos()
        self._append(data, line, line + data.count("\n"))

    def handle_entityref(self, name: str) -> None:
        self._append(html.unescape(f"&{name};"), self.getpos()[0], self.getpos()[0])

    def handle_charref(self, name: str) -> None:
        self._append(html.unescape(f"&#{name};"), self.getpos()[0], self.getpos()[0])

    def _append(self, value: str, start_line: int, end_line: int) -> None:
        if not value or self._hidden_stack:
            return
        if self._start_line is None:
            self._start_line = start_line
        self._end_line = max(self._end_line or end_line, end_line)
        self._parts.append(value)

    def _flush(self) -> None:
        if self._parts and self._start_line is not None and self._end_line is not None:
            value = "".join(self._parts)
            if value.strip():
                self.blocks.append((self._kind, value, self._start_line, self._end_line))
        self._parts = []
        self._start_line = None
        self._end_line = None
        self._kind = BlockKind.paragraph


def _plain_text_specs(text: str, relative_path: str) -> list[tuple[_BlockSpec, int]]:
    if not text:
        return []
    line_end, _, _ = TextCoordinates.from_text(text).at(len(text))
    return [
        (
            _BlockSpec(
                kind=BlockKind.paragraph,
                text=text,
                locator=MarkdownLocator(path=relative_path, line_start=1, line_end=line_end),
            ),
            0,
        )
    ]


def _html_specs(text: str, relative_path: str) -> list[_BlockSpec]:
    # HTMLParser counts LF; normalizing line endings preserves source line numbers.
    parser = _VisibleHTMLTextParser()
    parser.feed(text.replace("\r\n", "\n").replace("\r", "\n"))
    parser.close()
    parser._flush()
    specs: list[_BlockSpec] = []
    for kind, value, line_start, line_end in parser.blocks:
        locator = MarkdownLocator(
            path=relative_path,
            line_start=line_start,
            line_end=max(line_start, line_end),
        )
        specs.append(_BlockSpec(kind=kind, text=value, locator=locator))
    return specs


class StructuralParser:
    """Four-source parser that consumes a prevalidated SourceVersion manifest."""

    def __init__(self, cache_max_bytes: int = 128 * 1024 * 1024) -> None:
        if cache_max_bytes < 1:
            raise ValueError("cache_max_bytes must be positive")
        self._cache: OrderedDict[str, DocumentDto] = OrderedDict()
        self._cache_sizes: dict[str, int] = {}
        self._cache_bytes = 0
        self._cache_max_bytes = cache_max_bytes
        self._cache_lock = threading.RLock()

    async def parse(self, request: ParseRequest) -> ParsedSource:
        return (await self.parse_detailed(request)).parsed_source

    async def parse_detailed(self, request: ParseRequest) -> ParseExecution:
        return await asyncio.to_thread(self._parse_sync, request)

    def _parse_sync(self, request: ParseRequest) -> ParseExecution:
        if request.source_version.source_id != request.source.id:
            rejected = RejectedFile(
                path=".",
                code="MANIFEST_MISMATCH",
                message="SourceVersion does not belong to the requested source.",
            )
            return ParseExecution(ParsedSource(documents=[], rejected_files=[rejected]), ())
        try:
            root = normalize_root(request.allowed_root)
        except PathSafetyError as exc:
            rejected = RejectedFile(path=".", code="PATH_DENIED", message=str(exc))
            return ParseExecution(ParsedSource(documents=[], rejected_files=[rejected]), ())

        if len(request.source_version.files) > request.limits.max_files:
            rejected = RejectedFile(
                path=".",
                code="LIMIT_EXCEEDED",
                message=f"SourceVersion exceeds the configured {request.limits.max_files}-file limit.",
            )
            return ParseExecution(ParsedSource(documents=[], rejected_files=[rejected]), ())

        fingerprint = parser_fingerprint(request.source.kind)
        documents: list[DocumentDto] = []
        rejected_files: list[RejectedFile] = []
        global_warnings: list[str] = []
        reused_ids: list[str] = []
        seen_paths: set[str] = set()

        ordered_files = sorted(
            request.source_version.files,
            key=lambda item: (item.relative_path.casefold(), item.relative_path),
        )
        for entry in ordered_files:
            path_key = entry.relative_path.casefold()
            if path_key in seen_paths:
                rejected_files.append(
                    RejectedFile(
                        path=entry.relative_path,
                        code="PATH_COLLISION",
                        message="Path collides with another source path under case-insensitive normalization.",
                    )
                )
                continue
            seen_paths.add(path_key)

            if not is_supported_path(request.source.kind, entry.relative_path):
                rejected_files.append(
                    RejectedFile(
                        path=entry.relative_path,
                        code="UNSUPPORTED_FORMAT",
                        message=f"File does not match source kind {request.source.kind.value}.",
                    )
                )
                continue

            try:
                normalized, path = resolve_relative_file(root, entry.relative_path)
            except PathSafetyError as exc:
                rejected_files.append(
                    RejectedFile(path=entry.relative_path, code="PATH_DENIED", message=str(exc))
                )
                continue

            try:
                size = path.stat().st_size
                max_bytes = request.limits.max_file_bytes
                if request.source.kind is SourceKind.python_repo:
                    max_bytes = min(max_bytes, request.limits.max_python_file_bytes)
                if size > max_bytes:
                    rejected_files.append(
                        RejectedFile(
                            path=normalized,
                            code="LIMIT_EXCEEDED",
                            message=f"File exceeds the configured {max_bytes}-byte limit.",
                        )
                    )
                    continue
                raw = path.read_bytes()
            except OSError:
                rejected_files.append(
                    RejectedFile(
                        path=normalized,
                        code="PARSE_FAILED",
                        message="File could not be read.",
                    )
                )
                continue

            raw_hash = sha256_bytes(raw)
            if raw_hash != entry.raw_hash:
                rejected_files.append(
                    RejectedFile(
                        path=normalized,
                        code="MANIFEST_MISMATCH",
                        message="File content changed after its SourceVersion was created.",
                    )
                )
                continue
            expected_id = document_id(request.source.id, normalized, raw_hash, fingerprint)
            if entry.document_id != expected_id:
                rejected_files.append(
                    RejectedFile(
                        path=normalized,
                        code="MANIFEST_MISMATCH",
                        message="Document id does not match the active parser fingerprint.",
                    )
                )
                continue

            with self._cache_lock:
                cached = self._cache.get(expected_id)
                if cached is not None:
                    self._cache.move_to_end(expected_id)
            if cached is not None:
                documents.append(cached)
                global_warnings.extend(cached.warnings)
                reused_ids.append(expected_id)
                continue

            try:
                parsed = self._parse_file(
                    request.source.kind,
                    request.source.id,
                    normalized,
                    raw_hash,
                    fingerprint,
                    expected_id,
                    raw,
                    path,
                )
            except _FileParseProblem as exc:
                rejected_files.append(
                    RejectedFile(path=normalized, code=exc.code, message=exc.safe_message)
                )
                continue
            except ArchiveSafetyError as exc:
                rejected_files.append(
                    RejectedFile(path=normalized, code=exc.code, message=str(exc))
                )
                continue
            except ImportError:
                rejected_files.append(
                    RejectedFile(
                        path=normalized,
                        code="PARSER_UNAVAILABLE",
                        message=f"Required parser dependency for {request.source.kind.value} is unavailable.",
                    )
                )
                continue
            except (
                AttributeError,
                IndexError,
                KeyError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                ValidationError,
            ):
                rejected_files.append(
                    RejectedFile(
                        path=normalized,
                        code="PARSE_FAILED",
                        message=f"{request.source.kind.value} parsing failed without returning a partial document.",
                    )
                )
                continue

            with self._cache_lock:
                self._remember(expected_id, parsed)
            documents.append(parsed)
            global_warnings.extend(parsed.warnings)

        parsed_source = ParsedSource(
            documents=documents,
            rejected_files=rejected_files,
            warnings=sorted(set(global_warnings)),
        )
        return ParseExecution(parsed_source, tuple(reused_ids))

    def _remember(self, key: str, document: DocumentDto) -> None:
        if key in self._cache:
            self._cache_bytes -= self._cache_sizes.pop(key)
            del self._cache[key]
        size = len(document.normalized_text.encode("utf-8"))
        if size > self._cache_max_bytes:
            return
        while self._cache and self._cache_bytes + size > self._cache_max_bytes:
            oldest_key, _oldest = self._cache.popitem(last=False)
            self._cache_bytes -= self._cache_sizes.pop(oldest_key)
        self._cache[key] = document
        self._cache_sizes[key] = size
        self._cache_bytes += size

    def _parse_file(
        self,
        source_kind: SourceKind,
        source_id: str,
        relative_path: str,
        raw_hash: str,
        fingerprint: str,
        expected_id: str,
        raw: bytes,
        path: Path,
    ) -> DocumentDto:
        if source_kind is SourceKind.python_repo:
            text = _decode_text(raw)
            located_specs = _python_specs(text, relative_path)
            document_text = text
            warnings: list[str] = []
        elif source_kind is SourceKind.markdown:
            text = _decode_text(raw)
            suffix = Path(relative_path).suffix.casefold()
            if suffix in {".txt"}:
                located_specs = _plain_text_specs(text, relative_path)
                document_text = text
            elif suffix == ".html":
                document_text, located_specs = _compose_specs(_html_specs(text, relative_path))
            else:
                located_specs = _markdown_specs(text, relative_path)
                document_text = text
            warnings = []
        elif source_kind is SourceKind.docx:
            validate_docx_archive(path)
            block_specs, warnings = _docx_specs(raw, relative_path)
            document_text, located_specs = _compose_specs(block_specs)
        else:
            block_specs, warnings = _pdf_specs(path, relative_path)
            document_text, located_specs = _compose_specs(block_specs)
            if os.environ.get("KW_DOCLING_MODEL_FINGERPRINT") is None:
                warnings.append("PDF_MODEL_FINGERPRINT_UNPINNED: local Docling model revision is not configured.")

        blocks = [
            _block_for_spec(expected_id, spec, start, document_text)
            for spec, start in located_specs
            if spec.text
        ]
        return DocumentDto(
            id=expected_id,
            source_id=source_id,
            relative_path=relative_path,
            raw_hash=raw_hash,
            parser_fingerprint=fingerprint,
            normalized_text=document_text,
            blocks=blocks,
            warnings=warnings,
        )


def _decode_text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _FileParseProblem("PARSE_FAILED", "Text file is not valid UTF-8.") from exc


def _python_specs(text: str, relative_path: str) -> list[tuple[_BlockSpec, int]]:
    try:
        tree = ast.parse(text, filename=relative_path, type_comments=True)
    except SyntaxError as exc:
        line = exc.lineno or 1
        raise _FileParseProblem(
            "PARSE_FAILED", f"Python syntax is invalid near line {line}."
        ) from exc

    line_starts = _line_starts(text)
    specs: list[tuple[_BlockSpec, int]] = []

    def add_range(start: int, end: int, qualname: str) -> None:
        if start < 0 or end > len(text) or end <= start:
            return
        value = text[start:end]
        if not value.strip():
            return
        start_line, start_column = _line_and_column(text, line_starts, start)
        end_line, end_column = _line_and_column(text, line_starts, end - 1)
        locator = PythonLocator(
            path=relative_path,
            line_start=start_line,
            line_end=end_line,
            qualname=qualname,
            col_start=start_column,
            col_end=end_column + 1,
        )
        specs.append(
            (
                _BlockSpec(
                    kind=BlockKind.code,
                    text=value,
                    locator=locator,
                    heading_path=tuple(qualname.split("::<", 1)[0].split(".")),
                ),
                start,
            )
        )

    if text:
        add_range(0, len(text), "<module>")

    def walk_symbol(node: ast.AST, parent_name: str) -> None:
        name = getattr(node, "name", None)
        if not isinstance(name, str):
            return
        qualname = name if parent_name == "<module>" else f"{parent_name}.{name}"
        start, end = _node_range(node, text, line_starts, include_decorators=True)
        add_range(start, end, qualname)
        body = getattr(node, "body", [])
        if not isinstance(body, list) or not body:
            return
        first_body_start, _ = _node_range(body[0], text, line_starts)
        if first_body_start > start:
            add_range(start, first_body_start, f"{qualname}::<header>")
        for index, statement in enumerate(body):
            walk_statement(statement, qualname, str(index))

    def walk_statement(node: ast.stmt, owner_name: str, path_key: str) -> None:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            walk_symbol(node, owner_name)
            return
        start, end = _node_range(node, text, line_starts)
        children = _child_statements(node)
        if children and _contains_symbol(node):
            cursor = start
            for index, child in enumerate(sorted(children, key=lambda item: _node_range(item, text, line_starts)[0])):
                child_start, child_end = _node_range(child, text, line_starts, include_decorators=True)
                if child_start > cursor:
                    add_range(cursor, child_start, f"{owner_name}::<stmt:{path_key}.h{index}>")
                walk_statement(child, owner_name, f"{path_key}.{index}")
                cursor = max(cursor, child_end)
            if end > cursor:
                add_range(cursor, end, f"{owner_name}::<stmt:{path_key}.t>")
            return
        add_range(start, end, f"{owner_name}::<stmt:{path_key}>")

    for index, statement in enumerate(tree.body):
        walk_statement(statement, "<module>", str(index))

    specs.sort(
        key=lambda item: (
            item[1],
            item[1] + len(item[0].text),
            str(item[0].locator.qualname or "")
            if isinstance(item[0].locator, PythonLocator)
            else "",
        )
    )
    return specs


def _child_statements(node: ast.AST) -> list[ast.stmt]:
    children: list[ast.stmt] = []
    for field in ("body", "orelse", "finalbody"):
        value = getattr(node, field, None)
        if isinstance(value, list):
            children.extend(item for item in value if isinstance(item, ast.stmt))
    handlers = getattr(node, "handlers", None)
    if isinstance(handlers, list):
        for handler in handlers:
            body = getattr(handler, "body", None)
            if isinstance(body, list):
                children.extend(item for item in body if isinstance(item, ast.stmt))
    cases = getattr(node, "cases", None)
    if isinstance(cases, list):
        for case in cases:
            body = getattr(case, "body", None)
            if isinstance(body, list):
                children.extend(item for item in body if isinstance(item, ast.stmt))
    return children


def _contains_symbol(node: ast.AST) -> bool:
    return any(
        item is not node and isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        for item in ast.walk(node)
    )


def _node_range(
    node: ast.AST,
    text: str,
    line_starts: list[int],
    *,
    include_decorators: bool = False,
) -> tuple[int, int]:
    start_line = getattr(node, "lineno", None)
    start_column = getattr(node, "col_offset", None)
    if start_line is None or start_column is None:
        return 0, 0
    if include_decorators:
        decorators = getattr(node, "decorator_list", [])
        if decorators:
            first = min(decorators, key=lambda item: getattr(item, "lineno", start_line))
            start_line = getattr(first, "lineno", start_line)
            start_column = getattr(first, "col_offset", start_column)
    end_line = getattr(node, "end_lineno", None)
    end_column = getattr(node, "end_col_offset", None)
    if end_line is None or end_column is None:
        return 0, 0
    start = _offset_from_ast(text, line_starts, int(start_line), int(start_column))
    end = _offset_from_ast(text, line_starts, int(end_line), int(end_column))
    return start, end


def _offset_from_ast(text: str, line_starts: list[int], line_number: int, byte_column: int) -> int:
    if line_number < 1 or line_number > len(line_starts):
        raise _FileParseProblem("PARSE_FAILED", "Python source position is outside the file.")
    start = line_starts[line_number - 1]
    end = line_starts[line_number] if line_number < len(line_starts) else len(text)
    line = text[start:end].rstrip("\r\n")
    encoded = line.encode("utf-8")
    if byte_column > len(encoded):
        raise _FileParseProblem("PARSE_FAILED", "Python column is outside its source line.")
    try:
        char_column = len(encoded[:byte_column].decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise _FileParseProblem("PARSE_FAILED", "Python column is not a UTF-8 boundary.") from exc
    return start + char_column


def _line_starts(text: str) -> list[int]:
    starts = [0]
    index = 0
    while index < len(text):
        if text[index] == "\r":
            if index + 1 < len(text) and text[index + 1] == "\n":
                index += 1
            starts.append(index + 1)
        elif text[index] == "\n":
            starts.append(index + 1)
        index += 1
    return starts


def _line_and_column(text: str, line_starts: list[int], offset: int) -> tuple[int, int]:
    import bisect

    index = bisect.bisect_right(line_starts, offset) - 1
    return index + 1, offset - line_starts[index]


def _markdown_specs(text: str, relative_path: str) -> list[tuple[_BlockSpec, int]]:
    from markdown_it import MarkdownIt

    tokens = MarkdownIt("commonmark").parse(text)
    line_starts = _line_starts(text)
    specs: list[tuple[_BlockSpec, int]] = []
    heading_stack: list[str] = []
    seen: set[tuple[str, int, int]] = set()

    for index, token in enumerate(tokens):
        if token.map is None:
            continue
        token_type = str(token.type)
        if token_type == "heading_open":
            level = int(str(token.tag)[1:]) if str(token.tag).startswith("h") else 1
            inline = next(
                (item for item in tokens[index + 1 :] if item.type == "inline"),
                None,
            )
            title = str(getattr(inline, "content", "")).strip()
            while len(heading_stack) >= level:
                heading_stack.pop()
            heading_stack.append(title)
            kind = BlockKind.heading
        elif token_type in {"fence", "code_block"}:
            kind = BlockKind.code
        elif token_type in {"paragraph_open", "html_block", "hr"}:
            kind = BlockKind.paragraph
        else:
            continue

        line_start, line_end_exclusive = token.map
        if line_start < 0 or line_end_exclusive > len(line_starts):
            continue
        start = line_starts[line_start]
        end = (
            line_starts[line_end_exclusive]
            if line_end_exclusive < len(line_starts)
            else len(text)
        )
        if end <= start:
            continue
        signature = (kind.value, start, end)
        if signature in seen:
            continue
        seen.add(signature)
        block_text = text[start:end]
        locator = MarkdownLocator(
            path=relative_path,
            line_start=line_start + 1,
            line_end=max(line_start + 1, line_end_exclusive),
        )
        specs.append(
            (
                _BlockSpec(
                    kind=kind,
                    text=block_text,
                    locator=locator,
                    heading_path=tuple(heading_stack),
                ),
                start,
            )
        )

    specs.sort(key=lambda item: (item[1], item[1] + len(item[0].text)))
    return specs


def _docx_specs(raw: bytes, relative_path: str) -> tuple[list[_BlockSpec], list[str]]:
    from docx import Document as load_docx
    from docx.opc.exceptions import PackageNotFoundError
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from lxml.etree import XMLSyntaxError

    try:
        document = load_docx(io.BytesIO(raw))
    except (PackageNotFoundError, XMLSyntaxError, OSError, ValueError, KeyError) as exc:
        raise _FileParseProblem("PARSE_FAILED", "DOCX document content could not be parsed.") from exc
    specs: list[_BlockSpec] = []
    heading_stack: list[str] = []
    block_index = 0
    table_index = 0
    for element in _docx_body_items(document, CT_P, CT_Tbl, Paragraph, Table):
        if isinstance(element, Paragraph):
            value = element.text
            style_name = str(getattr(getattr(element, "style", None), "name", ""))
            heading_match = re.match(r"Heading\s+(\d+)\b", style_name, flags=re.IGNORECASE)
            if heading_match:
                level = max(1, int(heading_match.group(1)))
                while len(heading_stack) >= level:
                    heading_stack.pop()
                heading_stack.append(value.strip())
                kind = BlockKind.heading
            else:
                kind = BlockKind.paragraph
            if value:
                specs.append(
                    _BlockSpec(
                        kind=kind,
                        text=value,
                        locator=DocxLocator(block=block_index),
                        heading_path=tuple(heading_stack),
                    )
                )
            block_index += 1
            continue

        rows = [
            [cell.text for cell in row.cells]
            for row in element.rows
        ]
        cells = [
            TableCell(row=row_index, column=column_index, text=value)
            for row_index, row in enumerate(rows)
            for column_index, value in enumerate(row)
        ]
        text, _ranges = render_table_cells(cells)
        if text:
            specs.append(
                _BlockSpec(
                    kind=BlockKind.table,
                    text=text,
                    locator=DocxLocator(block=block_index, table=table_index),
                    heading_path=tuple(heading_stack),
                    table_cells=tuple(cells),
                )
            )
        block_index += 1
        table_index += 1
    return specs, []


def _docx_body_items(
    document: Any,
    paragraph_xml_type: type,
    table_xml_type: type,
    paragraph_type: type,
    table_type: type,
) -> Iterator[Any]:
    iterator = getattr(document, "iter_inner_content", None)
    if callable(iterator):
        yield from iterator()
        return
    body = document._element.body
    for child in body.iterchildren():
        if isinstance(child, paragraph_xml_type):
            yield paragraph_type(child, document)
        elif isinstance(child, table_xml_type):
            yield table_type(child, document)


_DOCLING_ENV_LOCK = threading.RLock()


@contextlib.contextmanager
def _docling_offline_environment() -> Iterator[None]:
    names = ("HF_HUB_OFFLINE", "HF_HUB_DISABLE_TELEMETRY")
    with _DOCLING_ENV_LOCK:
        previous = {name: os.environ.get(name) for name in names}
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        try:
            yield
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def _pdf_specs(path: Path, relative_path: str) -> tuple[list[_BlockSpec], list[str]]:
    warnings: list[str] = []
    try:
        with _docling_offline_environment():
            try:
                result = _run_docling(path, table_structure=True)
            except (OSError, RuntimeError, TypeError, ValueError, ValidationError):
                warnings.append(
                    "PDF_TABLE_STRUCTURE_UNAVAILABLE: table model failed offline; retried with table extraction disabled."
                )
                result = _run_docling(path, table_structure=False)
    except (
        AttributeError,
        IndexError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        ValidationError,
    ) as exc:
        raise _FileParseProblem(
            "PARSE_FAILED", "PDF conversion failed with the offline Docling configuration."
        ) from exc

    document = getattr(result, "document", None)
    if document is None:
        raise _FileParseProblem("PARSE_FAILED", "Docling returned no PDF document.")
    status = str(getattr(getattr(result, "status", None), "value", getattr(result, "status", "")))
    if status and "success" not in status.casefold():
        warnings.append(f"PDF_CONVERSION_STATUS: Docling returned {status}.")
    errors = getattr(result, "errors", None)
    if errors:
        warnings.append(f"PDF_CONVERSION_ISSUES: Docling reported {len(errors)} issue(s).")

    specs: list[_BlockSpec] = []
    heading_stack: list[str] = []
    block_number = 0
    iterator = getattr(document, "iterate_items", None)
    if not callable(iterator):
        raise _FileParseProblem("PARSE_FAILED", "Docling document does not expose ordered items.")
    for item, _level in iterator():
        label = _label_text(getattr(item, "label", ""))
        provenance = list(getattr(item, "prov", []) or [])
        first_prov = provenance[0] if provenance else None
        page = max(1, int(getattr(first_prov, "page_no", 1)))
        bbox = _pdf_bbox(document, page, getattr(first_prov, "bbox", None), warnings)
        locator = PdfLocator(page=page, block=block_number, bbox=bbox)

        raw_cells = _pdf_table_cells(item)
        if "table" in label and raw_cells:
            text, _ranges = render_table_cells(raw_cells)
            kind = BlockKind.table
            cells = tuple(raw_cells)
        else:
            text = _docling_item_text(item, document)
            kind = _pdf_block_kind(label)
            cells = ()
            if "table" in label:
                warnings.append(
                    f"PDF_TABLE_CELLS_UNAVAILABLE: table at page {page}, block {block_number} is retained as text."
                )
        if not text.strip():
            block_number += 1
            continue
        if kind is BlockKind.heading:
            level = max(1, int(getattr(item, "level", 1) or 1))
            while len(heading_stack) >= level:
                heading_stack.pop()
            heading_stack.append(text.strip())
        specs.append(
            _BlockSpec(
                kind=kind,
                text=text,
                locator=locator,
                heading_path=tuple(heading_stack),
                table_cells=cells,
            )
        )
        block_number += 1
    return specs, warnings


def _run_docling(path: Path, *, table_structure: bool) -> Any:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = False
    options.do_table_structure = table_structure
    options.enable_remote_services = False
    options.allow_external_plugins = False
    options.generate_page_images = False
    options.generate_picture_images = False
    artifacts_path = os.environ.get("KW_DOCLING_ARTIFACTS")
    if artifacts_path:
        options.artifacts_path = artifacts_path
    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
    )
    return converter.convert(str(path))


def _label_text(value: Any) -> str:
    return str(getattr(value, "value", value)).casefold()


def _pdf_block_kind(label: str) -> BlockKind:
    if "heading" in label or "section_header" in label or "title" in label:
        return BlockKind.heading
    if "code" in label:
        return BlockKind.code
    return BlockKind.paragraph


def _docling_item_text(item: Any, document: Any) -> str:
    value = getattr(item, "text", None)
    if isinstance(value, str) and value:
        return value
    exporter = getattr(item, "export_to_markdown", None)
    if callable(exporter):
        try:
            exported = exporter(doc=document)
        except TypeError:
            exported = exporter()
        if isinstance(exported, str):
            return exported
    return ""


def _pdf_table_cells(item: Any) -> list[TableCell]:
    data = getattr(item, "data", None)
    raw_cells = getattr(data, "table_cells", None)
    if not raw_cells:
        return []
    cells: list[TableCell] = []
    for raw_cell in raw_cells:
        row = int(getattr(raw_cell, "start_row_offset_idx", 0))
        column = int(getattr(raw_cell, "start_col_offset_idx", 0))
        value = getattr(raw_cell, "text", "")
        if not isinstance(value, str):
            value = str(value)
        cells.append(TableCell(row=row, column=column, text=value))
    return cells


def _pdf_bbox(
    document: Any,
    page_number: int,
    bbox: Any,
    warnings: list[str],
) -> tuple[float, float, float, float] | None:
    if bbox is None:
        return None
    try:
        normalized = bbox
        page_map = getattr(document, "pages", {})
        page = page_map.get(page_number) if hasattr(page_map, "get") else None
        page_size = getattr(page, "size", None)
        page_height = getattr(page_size, "height", None)
        convert = getattr(bbox, "to_top_left_origin", None)
        if callable(convert) and page_height is not None:
            normalized = convert(page_height=float(page_height))
        if hasattr(normalized, "l"):
            return (
                float(normalized.l),
                float(normalized.t),
                float(normalized.r),
                float(normalized.b),
            )
        values = tuple(float(item) for item in normalized.as_tuple())
        if len(values) != 4:
            raise ValueError("unexpected bbox shape")
        return (values[0], values[1], values[2], values[3])
    except (AttributeError, TypeError, ValueError):
        warnings.append(f"PDF_BBOX_UNAVAILABLE: page {page_number} bbox could not be normalized.")
        return None


def _compose_specs(specs: list[_BlockSpec]) -> tuple[str, list[tuple[_BlockSpec, int]]]:
    parts: list[str] = []
    located: list[tuple[_BlockSpec, int]] = []
    cursor = 0
    for spec in specs:
        if not spec.text:
            continue
        if parts:
            parts.append("\n\n")
            cursor += 2
        located.append((spec, cursor))
        parts.append(spec.text)
        cursor += len(spec.text)
    return "".join(parts), located


def _block_for_spec(
    document_id_value: str,
    spec: _BlockSpec,
    start: int,
    document_text: str,
) -> Block:
    end = start + len(spec.text)
    if document_text[start:end] != spec.text:
        raise ValueError("parser block does not match the document text range")
    locator_dump = spec.locator.model_dump(mode="json")
    return Block(
        id=block_id(document_id_value, locator_dump, sha256_text(spec.text)),
        kind=spec.kind,
        text=spec.text,
        start=start,
        end=end,
        locator=spec.locator,
        heading_path=list(spec.heading_path),
        table_cells=list(spec.table_cells),
    )
