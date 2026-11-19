"""Independent provider adapters and deterministic offline fakes."""

from .embedding import CloudEmbeddingProvider, LocalQwenEmbeddingProvider
from .fake import FakeEmbeddingProvider, FakeGenerationProvider, FakeProvider
from .generation import CloudGenerationProvider
from .profiles import LocalQwenProfile, OpenAIChatProfile, OpenAIEmbeddingProfile, RerankProfile
from .reranking import ChunkTextResolver, CloudReranker

__all__ = [
    "ChunkTextResolver",
    "CloudEmbeddingProvider",
    "CloudGenerationProvider",
    "CloudReranker",
    "FakeEmbeddingProvider",
    "FakeGenerationProvider",
    "FakeProvider",
    "LocalQwenEmbeddingProvider",
    "LocalQwenProfile",
    "OpenAIChatProfile",
    "OpenAIEmbeddingProfile",
    "RerankProfile",
]
