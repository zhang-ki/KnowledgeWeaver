"""PostgreSQL-backed fact storage and immutable generation publishing."""

from typing import Any

__all__ = ["Database", "PipelineService", "PostgresKnowledgeStore"]


def __getattr__(name: str) -> Any:
    if name == "Database":
        from .database import Database

        return Database
    if name == "PostgresKnowledgeStore":
        from .knowledge_store import PostgresKnowledgeStore

        return PostgresKnowledgeStore
    if name == "PipelineService":
        from .pipeline import PipelineService

        return PipelineService
    raise AttributeError(name)
