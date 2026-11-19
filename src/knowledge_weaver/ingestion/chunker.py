from __future__ import annotations

import ast
import bisect
from dataclasses import dataclass

from knowledge_weaver.core.ids import chunk_id, deterministic_id, sha256_text
from knowledge_weaver.models import (
    Block,
    BlockKind,
    Chunk,
    ChunkConfig,
    ChunkSpan,
    Document,
    DocxLocator,
    Locator,
    MarkdownLocator,
    PythonLocator,
    StructureType,
)

from .tables import CellRange, render_table_cells

DEFAULT_MAX_LEAF_CHUNKS = 10_000


class ChunkLimitExceeded(ValueError):
    """Raised instead of silently truncating a source that exceeds its leaf limit."""



@dataclass(frozen=True)
class _SpanSlice:
    text: str
    spans: tuple[ChunkSpan, ...]
    path_component: str


class StructuralChunker:
    """Create deterministic parent/leaf chunks while retaining source-backed spans."""

    def __init__(self, max_leaf_chunks: int = DEFAULT_MAX_LEAF_CHUNKS) -> None:
        if max_leaf_chunks < 1:
            raise ValueError("max_leaf_chunks must be positive")
        self.max_leaf_chunks = max_leaf_chunks

    def split(self, document: Document, config: ChunkConfig) -> list[Chunk]:
        return self.split_many([document], config)[0]

    def split_many(
        self, documents: list[Document], config: ChunkConfig
    ) -> list[list[Chunk]]:
        results: list[list[Chunk]] = []
        total_leaves = 0
        for document in documents:
            chunks = self._split_document(document, config)
            total_leaves += sum(1 for chunk in chunks if chunk.is_leaf)
            if total_leaves > self.max_leaf_chunks:
                raise ChunkLimitExceeded(
                    f"Chunking produced more than the {self.max_leaf_chunks}-leaf source limit."
                )
            results.append(chunks)
        leaf_groups: dict[str, list[tuple[int, int]]] = {}
        for document_index, chunks in enumerate(results):
            for chunk_index, chunk in enumerate(chunks):
                if chunk.is_leaf:
                    leaf_groups.setdefault(chunk.text_hash, []).append(
                        (document_index, chunk_index)
                    )

        for text_hash, positions in leaf_groups.items():
            if len(positions) < 2:
                continue
            group_id = deterministic_id("duplicate_group", {"text_hash": text_hash})
            for document_index, chunk_index in positions:
                results[document_index][chunk_index] = results[document_index][
                    chunk_index
                ].model_copy(update={"duplicate_group": group_id})
        leaf_count = sum(
            1 for chunks in results for chunk in chunks if chunk.is_leaf
        )
        if leaf_count > self.max_leaf_chunks:
            raise ChunkLimitExceeded(
                f"Chunking produced {leaf_count} leaf chunks; limit is {self.max_leaf_chunks}."
            )
        return results

    def _split_document(self, document: Document, config: ChunkConfig) -> list[Chunk]:
        blocks = sorted(
            document.blocks,
            key=lambda block: (block.start, -(block.end - block.start), block.id),
        )
        block_by_qualname = {
            locator.qualname: block
            for block in blocks
            if isinstance((locator := block.locator), PythonLocator)
            and locator.qualname is not None
            and "::<" not in locator.qualname
        }
        parent_block_ids: dict[str, str] = {}
        child_ids: dict[str, list[str]] = {}
        for block in blocks:
            parent_qualname = _python_parent_qualname(block)
            if parent_qualname is None:
                continue
            parent = block_by_qualname.get(parent_qualname)
            if parent is None or parent.id == block.id:
                continue
            parent_block_ids[block.id] = parent.id
            child_ids.setdefault(parent.id, []).append(block.id)

        is_parent = {
            block.id: bool(child_ids.get(block.id)) or len(block.text) > config.max_chars
            for block in blocks
        }
        block_chunk_ids: dict[str, str] = {}
        for block in blocks:
            path = ["block", block.id, "parent"] if is_parent[block.id] else ["block", block.id]
            block_chunk_ids[block.id] = chunk_id(
                document.id,
                path,
                config.fingerprint,
                sha256_text(block.text),
            )

        output: list[Chunk] = []
        leaf_count = 0

        def append_chunk(chunk: Chunk) -> None:
            nonlocal leaf_count
            if chunk.is_leaf:
                leaf_count += 1
                if leaf_count > self.max_leaf_chunks:
                    raise ChunkLimitExceeded(
                        f"Chunking produced more than the {self.max_leaf_chunks}-leaf document limit."
                    )
            output.append(chunk)

        for block in blocks:
            parent_block_id = parent_block_ids.get(block.id)
            parent_id = block_chunk_ids.get(parent_block_id) if parent_block_id else None
            block_has_children = bool(child_ids.get(block.id))
            block_is_parent = is_parent[block.id]

            if block_is_parent:
                append_chunk(
                    self._make_chunk(
                        document,
                        block,
                        text=block.text,
                        spans=self._block_spans(document, block),
                        parent_id=parent_id,
                        is_leaf=False,
                        path=["block", block.id, "parent"],
                        config=config,
                    )
                )
                if not block_has_children and len(block.text) > config.max_chars:
                    for leaf in self._split_oversized_block(
                        document, block, config, block_chunk_ids[block.id]
                    ):
                        append_chunk(leaf)
                continue

            append_chunk(
                self._make_chunk(
                    document,
                    block,
                    text=block.text,
                    spans=self._block_spans(document, block),
                    parent_id=parent_id,
                    is_leaf=True,
                    path=["block", block.id],
                    config=config,
                )
            )
        return output

    def _block_spans(self, document: Document, block: Block) -> list[ChunkSpan]:
        if block.kind is BlockKind.table and block.table_cells:
            rendered, cell_ranges = render_table_cells(block.table_cells)
            if rendered != block.text:
                raise ValueError("table cells do not reproduce the table block text")
            return [
                ChunkSpan(
                    chunk_start=cell.start,
                    chunk_end=cell.end,
                    document_start=block.start + cell.start,
                    document_end=block.start + cell.end,
                    locator=_cell_locator(block.locator, cell),
                )
                for cell in cell_ranges
            ]
        if not block.text:
            return []
        return [
            ChunkSpan(
                chunk_start=0,
                chunk_end=len(block.text),
                document_start=block.start,
                document_end=block.end,
                locator=block.locator,
            )
        ]

    def _split_oversized_block(
        self,
        document: Document,
        block: Block,
        config: ChunkConfig,
        parent_id: str,
    ) -> list[Chunk]:
        if block.kind is BlockKind.table and block.table_cells:
            slices = _table_slices(block, config, self.max_leaf_chunks)
        else:
            slices = _plain_slices(document, block, config, self.max_leaf_chunks)
        leaves: list[Chunk] = []
        for index, item in enumerate(slices):
            if not item.text:
                continue
            leaves.append(
                self._make_chunk(
                    document,
                    block,
                    text=item.text,
                    spans=list(item.spans),
                    parent_id=parent_id,
                    is_leaf=True,
                    path=["block", block.id, "leaf", item.path_component, str(index)],
                    config=config,
                )
            )
        return leaves

    def _make_chunk(
        self,
        document: Document,
        block: Block,
        *,
        text: str,
        spans: list[ChunkSpan],
        parent_id: str | None,
        is_leaf: bool,
        path: list[str],
        config: ChunkConfig,
    ) -> Chunk:
        text_hash = sha256_text(text)
        locator = block.locator
        qualname = locator.qualname if isinstance(locator, PythonLocator) else None
        if qualname and "::<" in qualname:
            qualname = qualname.split("::<", 1)[0]
        structure_type = {
            BlockKind.heading: StructureType.heading,
            BlockKind.paragraph: StructureType.paragraph,
            BlockKind.table: StructureType.table,
            BlockKind.code: StructureType.code,
        }[block.kind]
        return Chunk(
            id=chunk_id(document.id, path, config.fingerprint, text_hash),
            document_id=document.id,
            parent_id=parent_id,
            is_leaf=is_leaf,
            text=text,
            text_hash=text_hash,
            spans=spans,
            structure_type=structure_type,
            qualname=qualname,
            heading_path=list(block.heading_path),
            chunker_fingerprint=config.fingerprint,
        )


def _python_parent_qualname(block: Block) -> str | None:
    locator = block.locator
    if not isinstance(locator, PythonLocator) or not locator.qualname:
        return None
    qualname = locator.qualname
    marker = qualname.find("::<")
    if marker >= 0:
        return qualname[:marker]
    if qualname == "<module>":
        return None
    if "." in qualname:
        return qualname.rsplit(".", 1)[0]
    return "<module>"


def _cell_locator(locator: Locator, cell: CellRange) -> Locator:
    if isinstance(locator, DocxLocator):
        return DocxLocator(
            block=locator.block,
            table=locator.table,
            row=cell.row,
            cell=cell.column,
        )
    return locator


def _table_slices(
    block: Block, config: ChunkConfig, max_leaf_chunks: int
) -> list[_SpanSlice]:
    rendered, cell_ranges = render_table_cells(block.table_cells)
    if rendered != block.text:
        raise ValueError("table cells do not reproduce the table block text")
    row_parts: dict[int, list[tuple[CellRange, Locator, str]]] = {}
    for cell in cell_ranges:
        cell_text = block.text[cell.start : cell.end]
        for start, end in _natural_ranges(cell_text, config, max_leaf_chunks):
            clipped = CellRange(
                row=cell.row,
                column=cell.column,
                start=cell.start + start,
                end=cell.start + end,
            )
            row_parts.setdefault(cell.row, []).append(
                (clipped, _cell_locator(block.locator, cell), cell_text[start:end])
            )

    result: list[_SpanSlice] = []
    for row in sorted(row_parts):
        buffer: list[str] = []
        spans: list[ChunkSpan] = []
        cursor = 0
        group_index = 0
        previous_column: int | None = None

        def flush(current_row: int = row) -> None:
            nonlocal buffer, spans, cursor, group_index, previous_column
            if buffer:
                result.append(
                    _SpanSlice(
                        text="".join(buffer),
                        spans=tuple(spans),
                        path_component=f"row:{current_row}:group:{group_index}",
                    )
                )
                group_index += 1
                if len(result) > max_leaf_chunks:
                    raise ChunkLimitExceeded(
                        f"Table chunking exceeded the {max_leaf_chunks}-leaf limit."
                    )
            buffer = []
            spans = []
            cursor = 0
            previous_column = None

        for cell, locator, text in row_parts[row]:
            prefix = "\t" if buffer and cell.column != previous_column else ""
            if buffer and cursor + len(prefix) + len(text) > config.max_chars:
                flush()
                prefix = ""
            buffer.append(prefix)
            cursor += len(prefix)
            chunk_start = cursor
            buffer.append(text)
            cursor += len(text)
            spans.append(
                ChunkSpan(
                    chunk_start=chunk_start,
                    chunk_end=cursor,
                    document_start=block.start + cell.start,
                    document_end=block.start + cell.end,
                    locator=locator,
                )
            )
            previous_column = cell.column
        flush()
    return result


def _plain_slices(
    document: Document,
    block: Block,
    config: ChunkConfig,
    max_leaf_chunks: int,
) -> list[_SpanSlice]:
    if isinstance(block.locator, PythonLocator):
        statement_ranges = _python_statement_ranges(document, block)
        if statement_ranges:
            result: list[_SpanSlice] = []
            for start, end in statement_ranges:
                text = document.normalized_text[start:end]
                if len(text) <= config.max_chars:
                    ranges = [(start, end)]
                else:
                    ranges = [
                        (start + left, start + right)
                        for left, right in _natural_ranges(text, config, max_leaf_chunks)
                    ]
                for left, right in ranges:
                    slice_text = document.normalized_text[left:right]
                    locator = _python_locator_for_range(document, block, left, right)
                    result.append(
                        _SpanSlice(
                            text=slice_text,
                            spans=(
                                ChunkSpan(
                                    chunk_start=0,
                                    chunk_end=len(slice_text),
                                    document_start=left,
                                    document_end=right,
                                    locator=locator,
                                ),
                            ),
                            path_component=f"source:{left}",
                        )
                    )
                    if len(result) > max_leaf_chunks:
                        raise ChunkLimitExceeded(
                            f"Code chunking exceeded the {max_leaf_chunks}-leaf limit."
                        )
            if result:
                return result

    result = []
    for left, right in _natural_ranges(block.text, config, max_leaf_chunks):
        slice_text = block.text[left:right]
        document_start = block.start + left
        document_end = block.start + right
        result.append(
            _SpanSlice(
                text=slice_text,
                spans=(
                    ChunkSpan(
                        chunk_start=0,
                        chunk_end=len(slice_text),
                        document_start=document_start,
                        document_end=document_end,
                        locator=_slice_locator(document, block, document_start, document_end),
                    ),
                ),
                path_component=f"source:{document_start}",
            )
        )
        if len(result) > max_leaf_chunks:
            raise ChunkLimitExceeded(
                f"Block chunking exceeded the {max_leaf_chunks}-leaf limit."
            )
    return result


def _python_statement_ranges(document: Document, block: Block) -> list[tuple[int, int]]:
    try:
        tree = ast.parse(document.normalized_text, type_comments=True)
    except SyntaxError:
        return []
    line_starts = _line_starts(document.normalized_text)
    matching: ast.AST | None = None
    for node in ast.walk(tree):
        start, end = _ast_node_range(
            node,
            document.normalized_text,
            line_starts,
            include_decorators=isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)),
        )
        if start == block.start and end == block.end:
            matching = node
            break
    if matching is None:
        return []
    children = sorted(
        _ast_child_statements(matching),
        key=lambda item: _ast_node_range(item, document.normalized_text, line_starts)[0],
    )
    if not children:
        return []
    ranges: list[tuple[int, int]] = []
    cursor = block.start
    for child in children:
        child_start, child_end = _ast_node_range(
            child, document.normalized_text, line_starts, include_decorators=True
        )
        if child_start > cursor and document.normalized_text[cursor:child_start].strip():
            ranges.append((cursor, child_start))
        if child_end > child_start:
            ranges.append((child_start, child_end))
        cursor = max(cursor, child_end)
    if cursor < block.end and document.normalized_text[cursor : block.end].strip():
        ranges.append((cursor, block.end))
    return [(start, end) for start, end in ranges if end > start]


def _ast_child_statements(node: ast.AST) -> list[ast.stmt]:
    children: list[ast.stmt] = []
    for field in ("body", "orelse", "finalbody"):
        value = getattr(node, field, None)
        if isinstance(value, list):
            children.extend(item for item in value if isinstance(item, ast.stmt))
    for field in ("handlers", "cases"):
        value = getattr(node, field, None)
        if isinstance(value, list):
            for child in value:
                body = getattr(child, "body", None)
                if isinstance(body, list):
                    children.extend(item for item in body if isinstance(item, ast.stmt))
    return children


def _ast_node_range(
    node: ast.AST,
    text: str,
    line_starts: list[int],
    *,
    include_decorators: bool = False,
) -> tuple[int, int]:
    start_line = getattr(node, "lineno", None)
    start_col = getattr(node, "col_offset", None)
    end_line = getattr(node, "end_lineno", None)
    end_col = getattr(node, "end_col_offset", None)
    if (
        start_line is None
        or start_col is None
        or end_line is None
        or end_col is None
    ):
        return -1, -1
    if include_decorators:
        decorators = getattr(node, "decorator_list", [])
        if decorators:
            first = min(decorators, key=lambda item: getattr(item, "lineno", start_line))
            start_line = getattr(first, "lineno", start_line)
            start_col = getattr(first, "col_offset", start_col)
    return (
        _ast_offset(text, line_starts, int(start_line), int(start_col)),
        _ast_offset(text, line_starts, int(end_line), int(end_col)),
    )


def _ast_offset(text: str, line_starts: list[int], line: int, byte_column: int) -> int:
    if line < 1 or line > len(line_starts):
        return -1
    start = line_starts[line - 1]
    end = line_starts[line] if line < len(line_starts) else len(text)
    line_text = text[start:end].rstrip("\r\n")
    encoded = line_text.encode("utf-8")
    if byte_column > len(encoded):
        return -1
    try:
        return start + len(encoded[:byte_column].decode("utf-8"))
    except UnicodeDecodeError:
        return -1


def _python_locator_for_range(
    document: Document, block: Block, start: int, end: int
) -> PythonLocator:
    locator = block.locator
    if not isinstance(locator, PythonLocator):
        raise TypeError("Python locator required")
    starts = _line_starts(document.normalized_text)
    start_line, start_col = _line_and_column(document.normalized_text, starts, start)
    end_line, end_col = _line_and_column(document.normalized_text, starts, end - 1)
    return PythonLocator(
        path=locator.path,
        line_start=start_line,
        line_end=end_line,
        qualname=locator.qualname,
        col_start=start_col,
        col_end=end_col + 1,
    )


def _slice_locator(document: Document, block: Block, start: int, end: int) -> Locator:
    locator = block.locator
    if isinstance(locator, MarkdownLocator):
        starts = _line_starts(document.normalized_text)
        start_line, _ = _line_and_column(document.normalized_text, starts, start)
        end_line, _ = _line_and_column(document.normalized_text, starts, end - 1)
        return MarkdownLocator(
            path=locator.path,
            line_start=start_line,
            line_end=max(start_line, end_line),
        )
    if isinstance(locator, PythonLocator):
        return _python_locator_for_range(document, block, start, end)
    return locator


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
    index = bisect.bisect_right(line_starts, offset) - 1
    return index + 1, offset - line_starts[index]


def _natural_ranges(
    text: str,
    config: ChunkConfig,
    max_ranges: int = DEFAULT_MAX_LEAF_CHUNKS,
) -> list[tuple[int, int]]:
    if not text:
        return []
    if len(text) <= config.max_chars:
        return [(0, len(text))]
    result: list[tuple[int, int]] = []
    start = 0
    target = min(config.target_chars, config.max_chars)
    while start < len(text):
        hard_end = min(len(text), start + config.max_chars)
        if hard_end == len(text):
            end = hard_end
        else:
            preferred = min(hard_end, start + target)
            search_start = min(hard_end, start + max(1, target // 2))
            candidates = [
                text.rfind(separator, search_start, preferred)
                for separator in ("\n", " ", "\t")
            ]
            boundary = max(candidates)
            end = boundary if boundary > start else hard_end
        if end <= start:
            end = hard_end
        if len(result) >= max_ranges:
            raise ChunkLimitExceeded(f"Text splitting exceeded the {max_ranges}-chunk limit.")
        result.append((start, end))
        if end >= len(text):
            break
        next_start = max(start + 1, end - config.overlap_chars)
        start = next_start
    return result
