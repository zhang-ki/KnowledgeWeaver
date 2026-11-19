from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from knowledge_weaver.models import (
    Action,
    Answer,
    Chunk,
    ChunkConfig,
    CitationCheck,
    Decision,
    DeleteSourceRequest,
    Document,
    EmbeddingBatch,
    EmbeddingMode,
    Evidence,
    ExperimentManifest,
    ExtractionRequest,
    ExtractionResult,
    GraphSnapshot,
    IndexCandidate,
    IndexQuery,
    IndexReport,
    IngestRequest,
    InvestigationRequest,
    InvestigationState,
    LLMRequest,
    LLMResult,
    Observation,
    ParsedSource,
    ParseRequest,
    QueryContext,
    RetrievalHit,
    RetrievalQuery,
    RetrievalResult,
    RunSpec,
    SupportMatrix,
    VerifyRequest,
)


@runtime_checkable
class Parser(Protocol):
    async def parse(self, request: ParseRequest) -> ParsedSource: ...


@runtime_checkable
class Chunker(Protocol):
    def split(self, document: Document, config: ChunkConfig) -> list[Chunk]: ...


@runtime_checkable
class Extractor(Protocol):
    async def extract(self, request: ExtractionRequest) -> ExtractionResult: ...


@runtime_checkable
class PipelineService(Protocol):
    async def ingest(self, request: IngestRequest) -> IndexReport: ...

    async def delete_source(self, request: DeleteSourceRequest) -> IndexReport: ...


@runtime_checkable
class KnowledgeStore(Protocol):
    async def resolve_context(self, corpus_id: str, request_id: str) -> QueryContext: ...

    async def get_chunks(self, ids: list[str], context: QueryContext) -> list[Chunk]: ...

    async def get_evidence(self, ids: list[str], context: QueryContext) -> list[Evidence]: ...

    async def get_chunk_evidence(
        self, ids: list[str], context: QueryContext
    ) -> list[Evidence]: ...

    async def validate_citations(self, ids: list[str], context: QueryContext) -> CitationCheck: ...


@runtime_checkable
class CandidateIndex(Protocol):
    async def search(self, query: IndexQuery) -> list[IndexCandidate]: ...


@runtime_checkable
class GraphStore(Protocol):
    async def snapshot(self, context: QueryContext) -> GraphSnapshot: ...


@runtime_checkable
class Retriever(Protocol):
    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult: ...


@runtime_checkable
class HybridRetriever(Retriever, Protocol):
    def __init__(
        self, channels: Mapping[str, Retriever], reranker: Reranker | None
    ) -> None: ...


@runtime_checkable
class Reranker(Protocol):
    async def rerank(
        self, query: str, hits: list[RetrievalHit], context: QueryContext, limit: int
    ) -> list[RetrievalHit]: ...


@runtime_checkable
class EvidenceVerifier(Protocol):
    async def evaluate(self, request: VerifyRequest) -> SupportMatrix: ...


@runtime_checkable
class RetrievalPolicy(Protocol):
    def choose(self, state: InvestigationState, candidates: list[Action]) -> Decision: ...

    def observe(self, state: InvestigationState, observation: Observation) -> InvestigationState: ...


@runtime_checkable
class AgentService(Protocol):
    async def investigate(self, request: InvestigationRequest) -> Answer: ...


@runtime_checkable
class GenerationProvider(Protocol):
    async def generate(self, request: LLMRequest) -> LLMResult: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str], mode: EmbeddingMode) -> EmbeddingBatch: ...


@runtime_checkable
class EvaluationRunner(Protocol):
    async def run(self, spec: RunSpec) -> ExperimentManifest: ...


__all__ = [
    "AgentService",
    "CandidateIndex",
    "Chunker",
    "EmbeddingProvider",
    "EvaluationRunner",
    "EvidenceVerifier",
    "Extractor",
    "GenerationProvider",
    "GraphStore",
    "HybridRetriever",
    "KnowledgeStore",
    "Parser",
    "PipelineService",
    "Reranker",
    "RetrievalPolicy",
    "Retriever",
]
