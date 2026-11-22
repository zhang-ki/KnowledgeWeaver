"""Source coordinates for verbatim TXT documents."""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

_BOUNDARY = re.compile(r"\r\n|[\r\n\f]")


@dataclass(frozen=True)
class TextCoordinates:
    """One-based global, page and page-relative lines for a decoded character offset.

    CRLF is one line break. CR, LF and FF are individual breaks. FF additionally
    starts a new page, so an LF immediately after FF advances that page to line 2.
    A boundary character belongs to the line it terminates.
    """

    line_starts: tuple[int, ...]
    page_first_lines: tuple[int, ...]

    @classmethod
    def from_text(cls, text: str) -> TextCoordinates:
        line_starts = [0]
        page_first_lines = [1]
        for boundary in _BOUNDARY.finditer(text):
            line_starts.append(boundary.end())
            if boundary.group() == "\f":
                page_first_lines.append(len(line_starts))
        return cls(tuple(line_starts), tuple(page_first_lines))

    def at(self, offset: int) -> tuple[int, int, int]:
        line = bisect_right(self.line_starts, offset)
        page = bisect_right(self.page_first_lines, line)
        return line, page, line - self.page_first_lines[page - 1] + 1
