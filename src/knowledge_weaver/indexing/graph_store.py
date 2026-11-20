from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from sqlalchemy import select

from knowledge_weaver.core.errors import ErrorCode, KnowledgeWeaverError
from knowledge_weaver.models import (
    Entity,
    Evidence,
    GraphSnapshot,
    Mention,
    Provenance,
    QueryContext,
    Relation,
)
from knowledge_weaver.storage.knowledge_store import PostgresKnowledgeStore
from knowledge_weaver.storage.schema import (
    ChunkRow,
    DocumentRow,
    EntityRow,
    EvidenceRow,
    GenerationRow,
    MentionRow,
    ProvenanceRow,
    RelationEvidenceRow,
    RelationRow,
)

if TYPE_CHECKING:
    from knowledge_weaver.storage.database import Database


class PostgresGraphStore:
    def __init__(self, database: Database, knowledge_store: PostgresKnowledgeStore) -> None:
        self.database = database
        self.knowledge_store = knowledge_store

    async def snapshot(self, context: QueryContext) -> GraphSnapshot:
        async with self.database.session() as session:
            generation = await session.get(GenerationRow, context.generation_id)
            if generation is None or generation.corpus_id != context.corpus_id or generation.state not in {"READY", "RETIRED"}:
                raise KnowledgeWeaverError(ErrorCode.NO_READY_GENERATION, "Requested graph generation is unavailable.", request_id=context.request_id)
            members = await self.knowledge_store._members(session, context.generation_id, "entity")
            mention_ids = await self.knowledge_store._members(session, context.generation_id, "mention")
            relations_ids = await self.knowledge_store._members(session, context.generation_id, "relation")
            evidence_members = await self.knowledge_store._members(session, context.generation_id, "evidence")
            entities = [Entity.model_validate(row.payload) for row in (await session.scalars(select(EntityRow).where(EntityRow.id.in_(members)))).all()] if members else []
            mentions_raw = (await session.scalars(select(MentionRow).where(MentionRow.id.in_(mention_ids), MentionRow.entity_id.in_(members)))).all() if mention_ids and members else []
            relation_rows = (await session.scalars(select(RelationRow).where(RelationRow.id.in_(relations_ids)))).all() if relations_ids else []
            edge_evidence = set((await session.scalars(select(RelationEvidenceRow.evidence_id).where(RelationEvidenceRow.relation_id.in_(relations_ids)))).all()) if relations_ids else set()
            evidence_ids = evidence_members & (edge_evidence | {m.evidence_id for m in mentions_raw})
            evidence_rows = (await session.scalars(select(EvidenceRow).where(EvidenceRow.id.in_(evidence_ids)))).all() if evidence_ids else []
            chunk_ids = {row.chunk_id for row in evidence_rows}
            chunk_rows = (await session.scalars(select(ChunkRow).where(ChunkRow.id.in_(chunk_ids)))).all() if chunk_ids else []
            chunks = {row.id: row for row in chunk_rows}
            document_ids = {chunks[row.chunk_id].document_id for row in evidence_rows if row.chunk_id in chunks}
            document_rows = (await session.scalars(select(DocumentRow).where(DocumentRow.id.in_(document_ids)))).all() if document_ids else []
            documents = {row.id: row for row in document_rows}
            source_filter = None if context.source_ids is None else set(context.source_ids)
            allowed_evidence = {row.id for row in evidence_rows if row.chunk_id in chunks and documents.get(chunks[row.chunk_id].document_id) and (source_filter is None or documents[chunks[row.chunk_id].document_id].source_id in source_filter)}
            mentions = [Mention.model_validate(row.payload) for row in mentions_raw if row.evidence_id in allowed_evidence]
            by_entity: dict[str, set[str]] = {}
            for mention in mentions:
                by_entity.setdefault(mention.entity_id, set()).add(mention.evidence_id)
            entity_ids = {entity_id for entity_id, refs in by_entity.items() if refs}
            entities = [item for item in entities if item.id in entity_ids]
            evidence = [Evidence.model_validate(row.payload) for row in evidence_rows if row.id in allowed_evidence]
            evidence_lookup = {item.id for item in evidence}
            relations: list[Relation] = []
            provenance_ids: set[str] = set()
            for row in relation_rows:
                fact = Relation.model_validate(row.payload)
                if fact.subject_id not in entity_ids or fact.object_id not in entity_ids:
                    continue
                if not fact.evidence_ids or not set(fact.evidence_ids).issubset(evidence_lookup):
                    continue
                if fact.provenance_id not in provenance_ids:
                    provenance_ids.add(fact.provenance_id)
                relations.append(fact)
            mention_provs = {item.provenance_id for item in evidence}
            provenance_ids.update(mention_provs)
            prov_rows = (await session.scalars(select(ProvenanceRow).where(ProvenanceRow.id.in_(provenance_ids)))).all() if provenance_ids else []
            provenance = [Provenance.model_validate(row.payload) for row in prov_rows]
            if not context.include_inferred:
                inferred = {item.id for item in provenance if item.method.value == "RULE_INFERRED"}
                relations = [item for item in relations if item.provenance_id not in inferred]
            payload = {"entities": [x.model_dump(mode="json") for x in entities], "mentions": [x.model_dump(mode="json") for x in mentions], "relations": [x.model_dump(mode="json") for x in relations], "evidence": [x.model_dump(mode="json") for x in evidence], "provenance": [x.model_dump(mode="json") for x in provenance]}
            digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            return GraphSnapshot(context=context, entities=entities, mentions=mentions, relations=relations, evidence=evidence, provenance=provenance, snapshot_hash=digest)
