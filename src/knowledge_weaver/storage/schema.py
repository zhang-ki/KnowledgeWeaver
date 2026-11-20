from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSONB, list[Any]: JSONB}


class CorpusRow(Base):
    __tablename__ = "corpora"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    active_generation_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "active_generation_id"],
            ["generations.corpus_id", "generations.id"],
            name="fk_corpora_active_generation_same_corpus",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
    )


class SourceRow(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    corpus_id: Mapped[str] = mapped_column(ForeignKey("corpora.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    logical_uri: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    license: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("corpus_id", "logical_uri", name="uq_source_corpus_uri"),)


class SourceVersionRow(Base):
    __tablename__ = "source_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[str | None] = mapped_column(Text)
    dirty: Mapped[bool] = mapped_column(Boolean, nullable=False)
    files: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    __table_args__ = (UniqueConstraint("source_id", "manifest_hash", name="uq_source_version_manifest"),)


class DocumentRow(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_fingerprint: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    blocks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)


class SourceVersionDocumentRow(Base):
    __tablename__ = "source_version_documents"
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id", ondelete="CASCADE"), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)


class BlockRow(Base):
    __tablename__ = "blocks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ChunkRow(Base):
    __tablename__ = "chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("chunks.id", ondelete="SET NULL"))
    is_leaf: Mapped[bool] = mapped_column(Boolean, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ProvenanceRow(Base):
    __tablename__ = "provenance"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class EvidenceRow(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), index=True)
    provenance_id: Mapped[str] = mapped_column(ForeignKey("provenance.id", ondelete="RESTRICT"), index=True)
    start: Mapped[int] = mapped_column(Integer, nullable=False)
    end: Mapped[int] = mapped_column(Integer, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class EntityRow(Base):
    __tablename__ = "entities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    corpus_id: Mapped[str] = mapped_column(ForeignKey("corpora.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    namespace: Mapped[str] = mapped_column(Text, nullable=False)
    identity_key: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    __table_args__ = (UniqueConstraint("corpus_id", "kind", "identity_key", name="uq_entity_identity"),)


class MentionRow(Base):
    __tablename__ = "mentions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id", ondelete="CASCADE"), index=True)
    surface_form: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class RelationRow(Base):
    __tablename__ = "relations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subject_id: Mapped[str] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    object_id: Mapped[str] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    provenance_id: Mapped[str] = mapped_column(ForeignKey("provenance.id", ondelete="RESTRICT"), index=True)
    predicate: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class RelationEvidenceRow(Base):
    __tablename__ = "relation_evidence"
    relation_id: Mapped[str] = mapped_column(ForeignKey("relations.id", ondelete="CASCADE"), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True)


class GenerationRow(Base):
    __tablename__ = "generations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    corpus_id: Mapped[str] = mapped_column(ForeignKey("corpora.id", ondelete="CASCADE"), index=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    source_version_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    index_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    embedding_fingerprint: Mapped[str] = mapped_column(String(256), nullable=False)
    generation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("corpus_id", "id", name="uq_generation_corpus_id"),)


class GenerationSourceVersionRow(Base):
    __tablename__ = "generation_source_versions"
    generation_id: Mapped[str] = mapped_column(ForeignKey("generations.id", ondelete="CASCADE"), primary_key=True)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id", ondelete="CASCADE"), primary_key=True)


class GenerationDocumentRow(Base):
    __tablename__ = "generation_documents"
    generation_id: Mapped[str] = mapped_column(ForeignKey("generations.id", ondelete="CASCADE"), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)


class GenerationChunkRow(Base):
    __tablename__ = "generation_chunks"
    generation_id: Mapped[str] = mapped_column(ForeignKey("generations.id", ondelete="CASCADE"), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True)


class GenerationEvidenceRow(Base):
    __tablename__ = "generation_evidence"
    generation_id: Mapped[str] = mapped_column(ForeignKey("generations.id", ondelete="CASCADE"), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True)


class GenerationProvenanceRow(Base):
    __tablename__ = "generation_provenance"
    generation_id: Mapped[str] = mapped_column(ForeignKey("generations.id", ondelete="CASCADE"), primary_key=True)
    provenance_id: Mapped[str] = mapped_column(ForeignKey("provenance.id", ondelete="CASCADE"), primary_key=True)


class GenerationEntityRow(Base):
    __tablename__ = "generation_entities"
    generation_id: Mapped[str] = mapped_column(ForeignKey("generations.id", ondelete="CASCADE"), primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True)


class GenerationMentionRow(Base):
    __tablename__ = "generation_mentions"
    generation_id: Mapped[str] = mapped_column(ForeignKey("generations.id", ondelete="CASCADE"), primary_key=True)
    mention_id: Mapped[str] = mapped_column(ForeignKey("mentions.id", ondelete="CASCADE"), primary_key=True)


class GenerationRelationRow(Base):
    __tablename__ = "generation_relations"
    generation_id: Mapped[str] = mapped_column(ForeignKey("generations.id", ondelete="CASCADE"), primary_key=True)
    relation_id: Mapped[str] = mapped_column(ForeignKey("relations.id", ondelete="CASCADE"), primary_key=True)


class QueryLeaseRow(Base):
    __tablename__ = "query_leases"
    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    generation_id: Mapped[str] = mapped_column(ForeignKey("generations.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JobRow(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    corpus_id: Mapped[str] = mapped_column(ForeignKey("corpora.id", ondelete="CASCADE"), index=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("corpus_id", "idempotency_key", name="uq_job_idempotency"),)


class QueryRow(Base):
    __tablename__ = "queries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    corpus_id: Mapped[str] = mapped_column(ForeignKey("corpora.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class InvestigationRow(Base):
    __tablename__ = "investigations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    corpus_id: Mapped[str] = mapped_column(ForeignKey("corpora.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class RetrievalRecordRow(Base):
    __tablename__ = "retrieval_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class AnswerRow(Base):
    __tablename__ = "answers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
