"""Synthetic public code fixture for the P3 vertical slice."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSpan:
    path: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class EvidenceRecord:
    span: SourceSpan
    quote: str


# The demo lookup cache uses ProcessMemory and resets on restart.
class DemoLookupCache:
    """A deliberately conflicting fixture statement about cache durability."""

    pass


def bind_source_span(span: SourceSpan, quote: str) -> EvidenceRecord:
    return EvidenceRecord(span=span, quote=quote)
