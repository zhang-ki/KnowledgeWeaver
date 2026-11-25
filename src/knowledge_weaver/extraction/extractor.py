from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import PurePosixPath

from knowledge_weaver.models import ExtractionRequest, ExtractionResult, SourceKind
from knowledge_weaver.ports import Extractor as ExtractorPort
from knowledge_weaver.ports import GenerationProvider

from .ast_extractor import _PythonSourceIndex, extract_static_python
from .text_extractor import StructuredTextExtractor

EXTRACTION_OBSERVATION_PREFIX = "EXTRACTION_OBSERVATION_V2:"

_PROVIDER_FAILURE_CODES = {
    "PROVIDER_TIMEOUT",
    "PROVIDER_ERROR",
    "SCHEMA_REPAIR_TIMEOUT",
    "SCHEMA_REPAIR_FAILED",
}
_STRUCTURE_REJECTION_CODES = {"LLM_SCHEMA_INVALID", "AST_PARSE_FAILED"}


def _is_python_source(
    source_id: str, relative_path: str, source_kinds_by_id: Mapping[str, SourceKind]
) -> bool:
    source_kind = source_kinds_by_id.get(source_id)
    if source_kind is not None:
        return source_kind is SourceKind.python_repo
    # Direct ExtractionRequest callers predate source-kind routing. Retain their
    # source-format fallback while ingested documents use the authoritative kind.
    return PurePosixPath(relative_path.replace("\\", "/")).suffix.casefold() in {
        ".py",
        ".pyi",
    }


def _observation(
    request: ExtractionRequest,
    result: ExtractionResult,
    *,
    lane: str,
    source_kind: SourceKind | None,
    provider_configured: bool | None,
    corpus_id: str | None,
    provider_required: bool,
) -> str:
    valid_leaf_chunks = sum(
        chunk.document_id == request.document.id and chunk.is_leaf
        for chunk in request.chunks
    )
    accepted_entities = len(result.entities)
    accepted_mentions = len(result.mentions)
    accepted_relations = len(result.relations)
    accepted_item_count = accepted_entities + accepted_mentions + accepted_relations
    rejection_codes = {item.code for item in result.rejected_items}
    if corpus_id is None:
        outcome = "NOT_ATTEMPTED_MISSING_CORPUS_SCOPE"
    elif provider_required and not provider_configured:
        outcome = "PROVIDER_NOT_CONFIGURED"
    elif valid_leaf_chunks == 0:
        outcome = "NOT_ATTEMPTED_NO_LEAF_CHUNKS"
    elif rejection_codes & _PROVIDER_FAILURE_CODES:
        outcome = (
            "PARTIAL_PROVIDER_FAILURE" if accepted_item_count else "PROVIDER_FAILED"
        )
    elif rejection_codes & _STRUCTURE_REJECTION_CODES:
        outcome = (
            "PARTIAL_STRUCTURE_REJECTION"
            if accepted_item_count
            else "STRUCTURE_REJECTED"
        )
    elif accepted_relations:
        outcome = "EXECUTED_WITH_RELATIONS"
    elif result.rejected_items:
        outcome = "EXECUTED_NO_RELATIONS_WITH_REJECTIONS"
    else:
        outcome = "EXECUTED_NO_RELATIONS"
    payload = {
        "schema_version": 2,
        "source_id": request.document.source_id,
        "document_id": request.document.id,
        "source_kind": source_kind.value if source_kind is not None else "UNKNOWN",
        "lane": lane,
        "outcome": outcome,
        "provider_configured": provider_configured,
        "leaf_chunk_count": valid_leaf_chunks,
        "accepted_entity_count": accepted_entities,
        "accepted_mention_count": accepted_mentions,
        "accepted_relation_count": accepted_relations,
        "rejected_item_count": len(result.rejected_items),
        "rejection_codes": sorted(rejection_codes),
    }
    return EXTRACTION_OBSERVATION_PREFIX + json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


class KnowledgeExtractor(ExtractorPort):
    """Route code, normative specifications, and other text through separate lanes.

    ExtractionRequest intentionally omits corpus_id in the frozen public contract.
    Configure either one corpus_id for a single-corpus service or a source-to-corpus
    map for a multi-corpus service; the extractor never guesses a corpus from a
    source identifier.
    """

    def __init__(
        self,
        generation_provider: GenerationProvider | None = None,
        *,
        corpus_id: str | None = None,
        corpus_ids_by_source: Mapping[str, str] | None = None,
        source_kinds_by_id: Mapping[str, SourceKind] | None = None,
        normative_source_ids: set[str] | frozenset[str] | None = None,
        timeout_seconds: float = 30.0,
        max_schema_repairs: int = 1,
        _python_source_index: _PythonSourceIndex | None = None,
        _python_source_version_id: str | None = None,
    ) -> None:
        if corpus_id is not None and not corpus_id.strip():
            raise ValueError("corpus_id must be non-empty when supplied")
        self.generation_provider = generation_provider
        self.corpus_id = corpus_id
        self.corpus_ids_by_source = dict(corpus_ids_by_source or {})
        self.source_kinds_by_id = {
            source_id: SourceKind(source_kind)
            for source_id, source_kind in (source_kinds_by_id or {}).items()
        }
        self.normative_source_ids = frozenset(normative_source_ids or ())
        self.timeout_seconds = timeout_seconds
        self.max_schema_repairs = max_schema_repairs
        self._python_source_index = _python_source_index
        self._python_source_version_id = _python_source_version_id

    def _corpus_id_for_source(self, source_id: str) -> str | None:
        return self.corpus_ids_by_source.get(source_id, self.corpus_id)

    async def extract(self, request: ExtractionRequest) -> ExtractionResult:
        corpus_id = self._corpus_id_for_source(request.document.source_id)
        source_kind = self.source_kinds_by_id.get(request.document.source_id)
        if _is_python_source(
            request.document.source_id,
            request.document.relative_path,
            self.source_kinds_by_id,
        ):
            result = extract_static_python(
                request,
                corpus_id=corpus_id,
                source_index=self._python_source_index,
                source_version_id=self._python_source_version_id,
            )
            observation = _observation(
                request,
                result,
                lane="PYTHON_AST",
                source_kind=source_kind,
                provider_configured=None,
                corpus_id=corpus_id,
                provider_required=False,
            )
            return result.model_copy(update={"warnings": [*result.warnings, observation]})

        normative_specification = request.document.source_id in self.normative_source_ids
        extractor = StructuredTextExtractor(
            self.generation_provider,
            corpus_id=corpus_id,
            timeout_seconds=self.timeout_seconds,
            max_schema_repairs=self.max_schema_repairs,
            normative_specification=normative_specification,
        )
        result = await extractor.extract(request)
        observation = _observation(
            request,
            result,
            lane=("NORMATIVE_TEXT" if normative_specification else "STRUCTURED_TEXT"),
            source_kind=source_kind,
            provider_configured=self.generation_provider is not None,
            corpus_id=corpus_id,
            provider_required=True,
        )
        return result.model_copy(update={"warnings": [*result.warnings, observation]})
