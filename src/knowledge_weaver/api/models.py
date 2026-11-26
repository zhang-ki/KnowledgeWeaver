from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_weaver.models import GraphOptions, SourceKind


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceCreate(ApiModel):
    path: str = Field(min_length=1, max_length=2048)
    kind: SourceKind
    display_name: str | None = Field(default=None, max_length=120)
    license: str | None = Field(default=None, max_length=500)


class SourceView(ApiModel):
    id: str
    corpus_id: str
    kind: SourceKind
    display_name: str
    license: str | None = None
    indexed: bool = False


class IngestionCreate(ApiModel):
    source_id: str
    idempotency_key: str = Field(min_length=1, max_length=200)
    strict: bool = True
    retry_failed: bool = False


class DeleteSource(ApiModel):
    idempotency_key: str = Field(min_length=1, max_length=200)
    retry_failed: bool = False


class JobView(ApiModel):
    job_id: str
    operation: str
    state: str
    result: dict[str, Any] | None = None


class RetrievalCreate(ApiModel):
    query: str = Field(min_length=1, max_length=20_000)
    corpus_id: str | None = None
    generation_id: str | None = None
    source_ids: list[str] | None = None
    channel: Literal["bm25", "dense", "symbol", "entity", "graph", "hybrid"] = "hybrid"
    top_k: int = Field(default=10, ge=1, le=100)
    graph_options: GraphOptions | None = None


class InvestigationCreate(ApiModel):
    query: str = Field(min_length=1, max_length=20_000)
    corpus_id: str | None = None
    generation_id: str | None = None
    source_ids: list[str] | None = None
    idempotency_key: str = Field(min_length=1, max_length=200)
    policy: Literal["fixed", "rule"] = "fixed"
    max_steps: int = Field(default=6, ge=1, le=6)
    max_tool_calls: int = Field(default=12, ge=1, le=12)
    max_llm_calls: int = Field(default=8, ge=1, le=8)
    max_total_tokens: int = Field(default=12_000, ge=1, le=12_000)
    max_graph_edges: int = Field(default=200, ge=1, le=200)
    timeout_seconds: int = Field(default=300, ge=5, le=600)
    retry_failed: bool = False

    @field_validator("source_ids")
    @classmethod
    def unique_sources(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("source_ids must be unique")
        return value


class ExperimentEvidence(ApiModel):
    evidence_set: str
    status: str
    summary: str
    provenance: str
    provenance_sha256: str
    rows: list[dict[str, Any]]
    limitations: list[str]
    training_policy: str
