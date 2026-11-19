from __future__ import annotations

from dataclasses import dataclass

from knowledge_weaver.models import TableCell


@dataclass(frozen=True)
class CellRange:
    row: int
    column: int
    start: int
    end: int


def render_table_cells(cells: list[TableCell]) -> tuple[str, list[CellRange]]:
    if not cells:
        return "", []

    grid: dict[tuple[int, int], str] = {}
    for cell in sorted(cells, key=lambda item: (item.row, item.column)):
        grid.setdefault((cell.row, cell.column), cell.text)

    max_row = max(row for row, _column in grid)
    max_column = max(column for _row, column in grid)
    parts: list[str] = []
    ranges: list[CellRange] = []
    cursor = 0
    for row in range(max_row + 1):
        for column in range(max_column + 1):
            value = grid.get((row, column), "")
            start = cursor
            parts.append(value)
            cursor += len(value)
            if value:
                ranges.append(CellRange(row=row, column=column, start=start, end=cursor))
            if column < max_column:
                parts.append("\t")
                cursor += 1
        if row < max_row:
            parts.append("\n")
            cursor += 1
    return "".join(parts), ranges
