from __future__ import annotations

import bisect
import unicodedata
from collections.abc import Iterable
from typing import Any

from knowledge_weaver.core.ids import (
    canonical_json,
    deterministic_id,
    entity_id,
    evidence_id,
    provenance_id,
    relation_id,
)
from knowledge_weaver.models import (
    Chunk,
    Document,
    Entity,
    EntityKind,
    Evidence,
    ExtractionRequest,
    ExtractionResult,
    Mention,
    Polarity,
    Provenance,
    ProvenanceMethod,
    RejectedFile,
    Relation,
    RelationPredicate,
)
from knowledge_weaver.models.contracts import (
    DocxLocator,
    MarkdownLocator,
    PdfLocator,
    PythonLocator,
)


def normalize_surface(value: str) -> str:
    """Normalize text identity without removing source or sense scope."""
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def scoped_identity_key(
    *, namespace: str, kind: EntityKind, normalized_name: str, scope: str
) -> str:
    return canonical_json(
        {
            "namespace": namespace,
            "kind": kind.value,
            "normalized_name": normalized_name,
            "scope": scope,
        }
    )


def scoped_alias_key(entity: Entity, alias: str) -> str:
    """Return an alias key that can never merge two entity scopes implicitly."""
    return canonical_json(
        {
            "entity_id": entity.id,
            "namespace": entity.namespace,
            "normalized_alias": normalize_surface(alias),
        }
    )


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    offsets.extend(index + 1 for index, character in enumerate(text) if character == "\n")
    return offsets


def _line_column(offsets: list[int], position: int) -> tuple[int, int]:
    line_index = bisect.bisect_right(offsets, position) - 1
    return line_index + 1, position - offsets[line_index]


def _locator_for_quote(
    document: Document, base: Any, document_start: int, document_end: int
) -> Any:
    offsets = _line_offsets(document.normalized_text)
    line_start, col_start = _line_column(offsets, document_start)
    last_position = max(document_start, document_end - 1)
    line_end, _ = _line_column(offsets, last_position)
    end_line, col_end = _line_column(offsets, document_end)

    if isinstance(base, PythonLocator) or document.relative_path.casefold().endswith(
        (".py", ".pyi")
    ):
        path = base.path if isinstance(base, PythonLocator) else document.relative_path
        same_line = line_start == end_line
        return PythonLocator(
            path=path,
            line_start=line_start,
            line_end=line_end,
            qualname=base.qualname if isinstance(base, PythonLocator) else None,
            col_start=col_start if same_line else None,
            col_end=col_end if same_line else None,
        )
    if isinstance(base, MarkdownLocator) or document.relative_path.casefold().endswith(".md"):
        path = base.path if isinstance(base, MarkdownLocator) else document.relative_path
        return MarkdownLocator(path=path, line_start=line_start, line_end=line_end)
    if isinstance(base, (PdfLocator, DocxLocator)):
        return base
    return base


class ExtractionBuilder:
    """Build a closed, deterministic ExtractionResult from source-backed spans."""

    def __init__(self, request: ExtractionRequest, *, corpus_id: str | None) -> None:
        self.request = request
        self.document = request.document
        self.corpus_id = corpus_id
        self.chunks = [
            chunk
            for chunk in request.chunks
            if chunk.document_id == request.document.id and chunk.is_leaf
        ]
        self._provenance: dict[str, Provenance] = {}
        self._entities: dict[str, Entity] = {}
        self._mentions: dict[str, Mention] = {}
        self._evidence: dict[str, Evidence] = {}
        self._relations: dict[str, Relation] = {}
        self._rejected: dict[tuple[str, str, str], RejectedFile] = {}
        self._warnings: list[str] = []
        for chunk in request.chunks:
            if chunk.document_id != request.document.id:
                self.reject(
                    "CHUNK_DOCUMENT_MISMATCH",
                    "Input chunk belongs to a different document and was excluded.",
                    chunk=chunk,
                )

    def warn(self, message: str) -> None:
        if message not in self._warnings:
            self._warnings.append(message)

    def reject(self, code: str, message: str, *, chunk: Chunk | None = None) -> None:
        path = self.document.relative_path
        if chunk is not None:
            path = f"{path}#chunk:{chunk.id}"
        item = RejectedFile(path=path, code=code, message=message)
        self._rejected[(item.path, item.code, item.message)] = item

    def add_provenance(
        self,
        method: ProvenanceMethod,
        *,
        premise_relation_ids: Iterable[str] = (),
        rule_id: str | None = None,
        confidence: float | None = None,
    ) -> Provenance:
        premises = sorted(set(premise_relation_ids))
        item_id = provenance_id(
            method.value,
            self.request.producer_fingerprint,
            self.document.source_id,
            self.document.id,
            premises,
            rule_id,
        )
        item = Provenance(
            id=item_id,
            method=method,
            producer_fingerprint=self.request.producer_fingerprint,
            source_id=self.document.source_id,
            document_id=self.document.id,
            confidence=confidence,
            premise_relation_ids=premises,
            rule_id=rule_id,
        )
        previous = self._provenance.get(item.id)
        if previous is not None and previous.confidence != item.confidence:
            # Confidence does not participate in identity; keep it out of shared provenance.
            item = previous.model_copy(update={"confidence": None})
        self._provenance[item.id] = item
        return item

    def add_entity(
        self,
        kind: EntityKind,
        canonical_name: str,
        namespace: str,
        identity_key: str,
        aliases: Iterable[str] = (),
    ) -> Entity:
        clean_name = canonical_name.strip()
        clean_aliases = sorted(
            {
                alias.strip()
                for alias in aliases
                if alias.strip() and normalize_surface(alias) != normalize_surface(clean_name)
            }
        )
        if self.corpus_id is None:
            raise ValueError("corpus_id is required to create deterministic entity identities")
        item_id = entity_id(self.corpus_id, kind.value, identity_key)
        item = Entity(
            id=item_id,
            corpus_id=self.corpus_id,
            kind=kind,
            canonical_name=clean_name,
            namespace=namespace,
            identity_key=identity_key,
            aliases=clean_aliases,
        )
        existing = self._entities.get(item.id)
        if existing is not None:
            merged_aliases = sorted(set(existing.aliases).union(item.aliases))
            if (
                existing.kind != item.kind
                or existing.canonical_name != item.canonical_name
                or existing.namespace != item.namespace
                or existing.identity_key != item.identity_key
            ):
                raise ValueError("deterministic entity identity collision")
            item = existing.model_copy(update={"aliases": merged_aliases})
        self._entities[item.id] = item
        return item

    def add_mention(self, entity: Entity, evidence: Evidence, surface_form: str) -> Mention:
        mention_id = deterministic_id(
            "mention",
            {
                "entity_id": entity.id,
                "evidence_id": evidence.id,
                "surface_form": surface_form,
            },
        )
        mention = Mention(
            id=mention_id,
            entity_id=entity.id,
            evidence_id=evidence.id,
            surface_form=surface_form,
        )
        self._mentions[mention.id] = mention
        return mention

    def evidence_at_doc_range(
        self,
        start: int,
        end: int,
        provenance: Provenance,
        *,
        required_chunk: Chunk | None = None,
    ) -> Evidence | None:
        if start < 0 or end <= start or end > len(self.document.normalized_text):
            return None
        quote = self.document.normalized_text[start:end]
        matches: list[tuple[Chunk, int, Any]] = []
        chunks = [required_chunk] if required_chunk is not None else self.chunks
        for chunk in chunks:
            if chunk is None:
                continue
            for span in chunk.spans:
                if span.document_start <= start and end <= span.document_end:
                    chunk_start = span.chunk_start + start - span.document_start
                    chunk_end = chunk_start + len(quote)
                    if chunk.text[chunk_start:chunk_end] == quote:
                        matches.append((chunk, chunk_start, span))
        if not matches:
            return None
        chunk, chunk_start, span = min(
            matches,
            key=lambda match: (match[0].id, match[1], match[2].document_start),
        )
        return self._make_evidence(
            chunk,
            chunk_start,
            chunk_start + len(quote),
            start,
            end,
            span,
            provenance,
        )

    def evidence_for_quote(
        self, chunk: Chunk, quote: str, provenance: Provenance
    ) -> tuple[Evidence | None, str | None]:
        if not quote:
            return None, "empty_quote"
        offsets: dict[int, tuple[int, Any]] = {}
        search_from = 0
        while True:
            position = chunk.text.find(quote, search_from)
            if position < 0:
                break
            end = position + len(quote)
            for span in chunk.spans:
                if span.chunk_start <= position and end <= span.chunk_end:
                    document_start = span.document_start + position - span.chunk_start
                    document_end = document_start + len(quote)
                    if (
                        document_end <= span.document_end
                        and self.document.normalized_text[document_start:document_end] == quote
                    ):
                        offsets.setdefault(document_start, (position, span))
            search_from = position + 1
        if not offsets:
            return None, "quote_not_in_source_span"
        if len(offsets) > 1:
            return None, "ambiguous_quote"
        document_start, (position, span) = next(iter(offsets.items()))
        evidence = self._make_evidence(
            chunk,
            position,
            position + len(quote),
            document_start,
            document_start + len(quote),
            span,
            provenance,
        )
        return evidence, None

    def _make_evidence(
        self,
        chunk: Chunk,
        chunk_start: int,
        chunk_end: int,
        document_start: int,
        document_end: int,
        span: Any,
        provenance: Provenance,
    ) -> Evidence:
        quote = chunk.text[chunk_start:chunk_end]
        item = Evidence(
            id=evidence_id(chunk.id, chunk_start, chunk_end, quote, provenance.id),
            chunk_id=chunk.id,
            start=chunk_start,
            end=chunk_end,
            quote=quote,
            locator=_locator_for_quote(
                self.document, span.locator, document_start, document_end
            ),
            provenance_id=provenance.id,
        )
        item.assert_matches_chunk(chunk)
        self._evidence[item.id] = item
        return item

    def add_relation(
        self,
        subject: Entity,
        predicate: RelationPredicate,
        object_: Entity,
        polarity: Polarity,
        qualifiers: dict[str, Any],
        provenance: Provenance,
        evidence: Iterable[Evidence],
    ) -> Relation:
        evidence_ids = sorted({item.id for item in evidence})
        item = Relation(
            id=relation_id(
                subject.id,
                predicate.value,
                object_.id,
                polarity.value,
                qualifiers,
                provenance.id,
                evidence_ids,
            ),
            subject_id=subject.id,
            predicate=predicate,
            object_id=object_.id,
            polarity=polarity,
            qualifiers=qualifiers,
            provenance_id=provenance.id,
            evidence_ids=evidence_ids,
        )
        self._relations[item.id] = item
        return item

    def result(self) -> ExtractionResult:
        referenced_evidence_ids = {item.evidence_id for item in self._mentions.values()}
        referenced_evidence_ids.update(
            evidence_id for item in self._relations.values() for evidence_id in item.evidence_ids
        )
        evidence = [
            item for item_id, item in self._evidence.items() if item_id in referenced_evidence_ids
        ]
        referenced_provenance_ids = {item.provenance_id for item in evidence}
        referenced_provenance_ids.update(item.provenance_id for item in self._relations.values())
        return ExtractionResult(
            document_id=self.document.id,
            chunk_ids=[chunk.id for chunk in self.chunks],
            entities=sorted(self._entities.values(), key=lambda item: item.id),
            mentions=sorted(self._mentions.values(), key=lambda item: item.id),
            relations=sorted(self._relations.values(), key=lambda item: item.id),
            evidence=sorted(evidence, key=lambda item: item.id),
            provenance=sorted(
                (
                    item
                    for item_id, item in self._provenance.items()
                    if item_id in referenced_provenance_ids
                ),
                key=lambda item: item.id,
            ),
            rejected_items=sorted(
                self._rejected.values(), key=lambda item: (item.path, item.code, item.message)
            ),
            warnings=list(self._warnings),
        )
