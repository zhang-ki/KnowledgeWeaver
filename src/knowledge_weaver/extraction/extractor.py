from __future__ import annotations

from collections.abc import Mapping

from knowledge_weaver.models import ExtractionRequest, ExtractionResult
from knowledge_weaver.ports import Extractor as ExtractorPort
from knowledge_weaver.ports import GenerationProvider

from .ast_extractor import extract_static_python
from .text_extractor import StructuredTextExtractor


class KnowledgeExtractor(ExtractorPort):
    """Route Python files to static analysis and other documents to structured LLM extraction.

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
        timeout_seconds: float = 30.0,
        max_schema_repairs: int = 1,
    ) -> None:
        if corpus_id is not None and not corpus_id.strip():
            raise ValueError("corpus_id must be non-empty when supplied")
        self.generation_provider = generation_provider
        self.corpus_id = corpus_id
        self.corpus_ids_by_source = dict(corpus_ids_by_source or {})
        self.timeout_seconds = timeout_seconds
        self.max_schema_repairs = max_schema_repairs

    def _corpus_id_for_source(self, source_id: str) -> str | None:
        return self.corpus_ids_by_source.get(source_id, self.corpus_id)

    async def extract(self, request: ExtractionRequest) -> ExtractionResult:
        corpus_id = self._corpus_id_for_source(request.document.source_id)
        if request.document.relative_path.casefold().endswith((".py", ".pyi")):
            return extract_static_python(request, corpus_id=corpus_id)
        extractor = StructuredTextExtractor(
            self.generation_provider,
            corpus_id=corpus_id,
            timeout_seconds=self.timeout_seconds,
            max_schema_repairs=self.max_schema_repairs,
        )
        return await extractor.extract(request)
