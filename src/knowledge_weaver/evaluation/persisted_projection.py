"""Read a Generation's complete semantic mapping projection from PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select

from knowledge_weaver.evaluation.generation_binding import PersistedGenerationLookup
from knowledge_weaver.models.contracts import (
    Chunk,
    Document,
    Entity,
    Evidence,
    Generation,
    Mention,
    Provenance,
    Relation,
    Source,
    SourceVersion,
)
from knowledge_weaver.storage.schema import (
    ChunkRow,
    DocumentRow,
    EntityRow,
    EvidenceRow,
    GenerationChunkRow,
    GenerationDocumentRow,
    GenerationEntityRow,
    GenerationEvidenceRow,
    GenerationMentionRow,
    GenerationProvenanceRow,
    GenerationRelationRow,
    GenerationSourceVersionRow,
    MentionRow,
    ProvenanceRow,
    RelationEvidenceRow,
    RelationRow,
    SourceRow,
    SourceVersionDocumentRow,
    SourceVersionRow,
)


class _ReadDatabase(Protocol):
    def session(self) -> Any: ...


@dataclass(frozen=True)
class PersistedGoldProjection:
    """The source-bound records required by the strict Gold mapper."""

    generation: Generation
    sources: tuple[Source, ...]
    source_versions: tuple[SourceVersion, ...]
    documents: tuple[Document, ...]
    chunks: tuple[Chunk, ...]
    evidence: tuple[Evidence, ...]
    entities: tuple[Entity, ...]
    mentions: tuple[Mention, ...]
    provenance: tuple[Provenance, ...]
    relations: tuple[Relation, ...]


class GoldProjectionLookup(Protocol):
    async def load_gold_projection(self, generation_id: str) -> PersistedGoldProjection: ...


_MEMBERSHIP_TABLES: dict[str, tuple[Any, str]] = {
    "source_version": (GenerationSourceVersionRow, "source_version_id"),
    "document": (GenerationDocumentRow, "document_id"),
    "chunk": (GenerationChunkRow, "chunk_id"),
    "evidence": (GenerationEvidenceRow, "evidence_id"),
    "provenance": (GenerationProvenanceRow, "provenance_id"),
    "entity": (GenerationEntityRow, "entity_id"),
    "mention": (GenerationMentionRow, "mention_id"),
    "relation": (GenerationRelationRow, "relation_id"),
}


def _exact_rows(rows: list[Any], ids: list[str], label: str) -> list[Any]:
    if len({row.id for row in rows}) != len(rows) or sorted(row.id for row in rows) != sorted(ids):
        raise ValueError(f"persisted Generation {label} rows are incomplete or inconsistent")
    return rows


async def _member_ids(session: Any, generation_id: str) -> dict[str, list[str]]:
    output: dict[str, list[str]] = {}
    for kind, (model, member_column) in _MEMBERSHIP_TABLES.items():
        result = await session.scalars(
            select(getattr(model, member_column)).where(model.generation_id == generation_id)
        )
        ids = list(result.all())
        if len(ids) != len(set(ids)):
            raise ValueError(f"persisted Generation has duplicate {kind} membership")
        output[kind] = ids
    return output


class PostgresGoldProjectionLookup:
    """Load immutable Generation members through the configured Postgres store."""

    def __init__(self, store: PersistedGenerationLookup, database: _ReadDatabase | None = None) -> None:
        self._store = store
        selected_database = database if database is not None else getattr(store, "database", None)
        if selected_database is None:
            raise ValueError("persisted Generation store has no database session boundary")
        self._database = selected_database

    async def load_gold_projection(self, generation_id: str) -> PersistedGoldProjection:
        generation = await self._store.generation(generation_id)
        async with self._database.session() as session:
            members = await _member_ids(session, generation_id)
            visible_members = {
                "source_version": generation.source_version_ids,
                "chunk": generation.chunk_ids,
                "entity": generation.entity_ids,
                "relation": generation.relation_ids,
            }
            if any(sorted(members[kind]) != sorted(ids) for kind, ids in visible_members.items()):
                raise ValueError("persisted Generation member links differ from its stored record")

            async def rows_for(model: Any, ids: list[str], label: str) -> list[Any]:
                if not ids:
                    return []
                result = await session.scalars(select(model).where(model.id.in_(ids)))
                return _exact_rows(list(result.all()), ids, label)

            version_rows = await rows_for(
                SourceVersionRow, members["source_version"], "SourceVersion"
            )
            source_ids = sorted({row.source_id for row in version_rows})
            source_rows = await rows_for(SourceRow, source_ids, "Source")
            document_rows = await rows_for(DocumentRow, members["document"], "Document")
            chunk_rows = await rows_for(ChunkRow, members["chunk"], "Chunk")
            evidence_rows = await rows_for(EvidenceRow, members["evidence"], "Evidence")
            provenance_rows = await rows_for(
                ProvenanceRow, members["provenance"], "Provenance"
            )
            entity_rows = await rows_for(EntityRow, members["entity"], "Entity")
            mention_rows = await rows_for(MentionRow, members["mention"], "Mention")
            relation_rows = await rows_for(RelationRow, members["relation"], "Relation")

            relation_evidence_rows = []
            if members["relation"]:
                result = await session.scalars(
                    select(RelationEvidenceRow).where(
                        RelationEvidenceRow.relation_id.in_(members["relation"])
                    )
                )
                relation_evidence_rows = list(result.all())
            source_version_document_rows = []
            if members["source_version"]:
                result = await session.scalars(
                    select(SourceVersionDocumentRow).where(
                        SourceVersionDocumentRow.source_version_id.in_(members["source_version"])
                    )
                )
                source_version_document_rows = list(result.all())
            actual_evidence_by_relation: dict[str, set[str]] = {}
            for row in relation_evidence_rows:
                actual_evidence_by_relation.setdefault(row.relation_id, set()).add(
                    row.evidence_id
                )

            sources = tuple(
                Source(
                    id=row.id,
                    corpus_id=row.corpus_id,
                    kind=row.kind,
                    logical_uri=row.logical_uri,
                    display_name=row.display_name,
                    license=row.license,
                    created_at=row.created_at,
                )
                for row in source_rows
            )
            source_versions = tuple(
                SourceVersion.model_validate(
                    {
                        "id": row.id,
                        "source_id": row.source_id,
                        "manifest_hash": row.manifest_hash,
                        "revision": row.revision,
                        "dirty": row.dirty,
                        "files": row.files,
                    }
                )
                for row in version_rows
            )
            documents = tuple(
                Document.model_validate(
                    {
                        "id": row.id,
                        "source_id": row.source_id,
                        "relative_path": row.relative_path,
                        "raw_hash": row.raw_hash,
                        "parser_fingerprint": row.parser_fingerprint,
                        "normalized_text": row.normalized_text,
                        "blocks": row.blocks,
                        "warnings": row.warnings,
                    }
                )
                for row in document_rows
            )
            chunks = tuple(Chunk.model_validate(row.payload) for row in chunk_rows)
            evidence = tuple(Evidence.model_validate(row.payload) for row in evidence_rows)
            provenance = tuple(Provenance.model_validate(row.payload) for row in provenance_rows)
            entities = tuple(Entity.model_validate(row.payload) for row in entity_rows)
            mentions = tuple(Mention.model_validate(row.payload) for row in mention_rows)
            relations_list = [Relation.model_validate(row.payload) for row in relation_rows]

            actual_version_documents = {
                (row.source_version_id, row.document_id)
                for row in source_version_document_rows
            }
            expected_version_documents = {
                (version.id, file.document_id)
                for version in source_versions
                for file in version.files
            }
            if actual_version_documents != expected_version_documents:
                raise ValueError(
                    "persisted SourceVersion document links differ from its file manifest"
                )
            documents_by_id = {item.id: item for item in documents}
            for version in source_versions:
                for file in version.files:
                    document = documents_by_id.get(file.document_id)
                    if (
                        document is None
                        or document.source_id != version.source_id
                        or document.relative_path != file.relative_path
                        or document.raw_hash != file.raw_hash
                    ):
                        raise ValueError(
                            "persisted SourceVersion file is outside or differs from Generation Documents"
                        )

            for row, chunk_item in zip(chunk_rows, chunks, strict=True):
                if (
                    row.id,
                    row.document_id,
                    row.parent_id,
                    row.is_leaf,
                    row.text,
                    row.text_hash,
                ) != (
                    chunk_item.id,
                    chunk_item.document_id,
                    chunk_item.parent_id,
                    chunk_item.is_leaf,
                    chunk_item.text,
                    chunk_item.text_hash,
                ):
                    raise ValueError("persisted Chunk columns differ from its payload")
            for row, evidence_item in zip(evidence_rows, evidence, strict=True):
                if (
                    row.id,
                    row.chunk_id,
                    row.provenance_id,
                    row.start,
                    row.end,
                    row.quote,
                ) != (
                    evidence_item.id,
                    evidence_item.chunk_id,
                    evidence_item.provenance_id,
                    evidence_item.start,
                    evidence_item.end,
                    evidence_item.quote,
                ):
                    raise ValueError("persisted Evidence columns differ from its payload")
            for row, provenance_item in zip(provenance_rows, provenance, strict=True):
                if (row.id, row.source_id, row.document_id, row.method) != (
                    provenance_item.id,
                    provenance_item.source_id,
                    provenance_item.document_id,
                    provenance_item.method.value,
                ):
                    raise ValueError("persisted Provenance columns differ from its payload")
            for row, entity_item in zip(entity_rows, entities, strict=True):
                if (row.id, row.corpus_id, row.kind, row.namespace, row.identity_key) != (
                    entity_item.id,
                    entity_item.corpus_id,
                    entity_item.kind.value,
                    entity_item.namespace,
                    entity_item.identity_key,
                ):
                    raise ValueError("persisted Entity columns differ from its payload")
            for row, mention_item in zip(mention_rows, mentions, strict=True):
                if (row.id, row.entity_id, row.evidence_id, row.surface_form) != (
                    mention_item.id,
                    mention_item.entity_id,
                    mention_item.evidence_id,
                    mention_item.surface_form,
                ):
                    raise ValueError("persisted Mention columns differ from its payload")
            for row, relation_item in zip(relation_rows, relations_list, strict=True):
                actual_evidence = actual_evidence_by_relation.get(row.id, set())
                if (
                    row.id != relation_item.id
                    or row.subject_id != relation_item.subject_id
                    or row.object_id != relation_item.object_id
                    or row.provenance_id != relation_item.provenance_id
                    or row.predicate != relation_item.predicate.value
                    or actual_evidence != set(relation_item.evidence_ids)
                    or not actual_evidence.issubset(set(members["evidence"]))
                ):
                    raise ValueError("persisted Relation columns or Evidence links differ from its payload")

            return PersistedGoldProjection(
                generation=generation,
                sources=sources,
                source_versions=source_versions,
                documents=documents,
                chunks=chunks,
                evidence=evidence,
                entities=entities,
                mentions=mentions,
                provenance=provenance,
                relations=tuple(relations_list),
            )
