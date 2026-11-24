from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select, update

from knowledge_weaver.core.errors import ErrorCode, KnowledgeWeaverError
from knowledge_weaver.core.ids import (
    deterministic_id,
    evidence_id,
    new_id,
    provenance_id,
    sha256_text,
)
from knowledge_weaver.extraction import KnowledgeExtractor
from knowledge_weaver.extraction.ast_extractor import (
    _build_python_source_index,
    _PythonSourceIndex,
)
from knowledge_weaver.extraction.extractor import EXTRACTION_OBSERVATION_PREFIX
from knowledge_weaver.indexing.bm25 import TOKENIZER_VERSION, Bm25Index
from knowledge_weaver.indexing.dense import QdrantDenseIndex
from knowledge_weaver.indexing.graph_store import PostgresGraphStore
from knowledge_weaver.indexing.symbol import EntityIndex, SymbolIndex
from knowledge_weaver.ingestion import ManifestBuilder, StructuralChunker, StructuralParser
from knowledge_weaver.models import (
    Chunk,
    ChunkConfig,
    DeleteSourceRequest,
    Document,
    Evidence,
    ExtractionRequest,
    ExtractionResult,
    FileManifestEntry,
    GenerationState,
    IndexReport,
    IngestRequest,
    ParseLimits,
    ParseRequest,
    PipelineServiceStatus,
    Provenance,
    ProvenanceMethod,
    Source,
    SourceKind,
    SourceVersion,
)
from knowledge_weaver.ports import EmbeddingProvider, Extractor
from knowledge_weaver.storage.knowledge_store import PostgresKnowledgeStore

from .schema import (
    BlockRow,
    ChunkRow,
    CorpusRow,
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
    GenerationRow,
    GenerationSourceVersionRow,
    JobRow,
    MentionRow,
    ProvenanceRow,
    QueryLeaseRow,
    RelationEvidenceRow,
    RelationRow,
    SourceRow,
    SourceVersionDocumentRow,
    SourceVersionRow,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from .database import Database


_EXTRACTION_ROUTING_PROFILE_VERSION = "kw-extraction-routing-v2"
_NORMATIVE_PROMPT_VERSION = "kw-normative-response-schema-v3"
_PYTHON_SOURCE_INDEX_VERSION = "kw-python-source-index-v1"
_PYTHON_AST_EXTRACTION_VERSION = "kw-python-ast-extraction-v2"


def _source_extraction_profile_sha256(
    source: Source,
    *,
    normative_source: bool,
    provider_configured: bool | None,
) -> str:
    payload = {
        "profile_version": _EXTRACTION_ROUTING_PROFILE_VERSION,
        "source_kind": source.kind.value,
        "normative_source": normative_source,
        "normative_prompt_version": (
            _NORMATIVE_PROMPT_VERSION if normative_source else None
        ),
        "provider_configured": (
            None if source.kind is SourceKind.python_repo else provider_configured
        ),
        "python_source_index_version": (
            _PYTHON_SOURCE_INDEX_VERSION
            if source.kind is SourceKind.python_repo
            else None
        ),
        "python_ast_extraction_version": (
            _PYTHON_AST_EXTRACTION_VERSION
            if source.kind is SourceKind.python_repo
            else None
        ),
    }
    return sha256_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _extraction_observations(result: ExtractionResult) -> list[str]:
    return [
        warning
        for warning in result.warnings
        if warning.startswith(EXTRACTION_OBSERVATION_PREFIX)
    ]


def _python_source_index_for_version(
    source: Source,
    source_version: SourceVersion,
    *,
    documents_by_id: Mapping[str, Document],
    corpus_id: str,
) -> _PythonSourceIndex | None:
    if source.kind is not SourceKind.python_repo or source_version.source_id != source.id:
        return None
    documents: list[Document] = []
    incomplete_code_set = False
    for entry in source_version.files:
        is_python_file = entry.relative_path.casefold().endswith((".py", ".pyi"))
        document = documents_by_id.get(entry.document_id)
        if (
            document is None
            or document.source_id != source.id
            or document.relative_path.replace("\\", "/").casefold()
            != entry.relative_path.replace("\\", "/").casefold()
            or document.raw_hash != entry.raw_hash
        ):
            incomplete_code_set = incomplete_code_set or is_python_file
            continue
        documents.append(document)
    if incomplete_code_set:
        documents = []
    return _build_python_source_index(
        source_id=source.id,
        source_version_id=source_version.id,
        corpus_id=corpus_id,
        documents=documents,
    )


class PipelineService:
    """Build, validate and CAS publish an immutable corpus generation."""

    def __init__(
        self,
        database: Database,
        *,
        data_dir: str | Path,
        allowed_roots: list[str | Path],
        qdrant_url: str | None = None,
        qdrant_api_key: str | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        parser: StructuralParser | None = None,
        chunker: StructuralChunker | None = None,
        extractor: Extractor | None = None,
        normative_source_ids: set[str] | frozenset[str] | None = None,
        chunk_config: ChunkConfig | None = None,
        limits: ParseLimits | None = None,
        failure_injector: Callable[[str], None | Awaitable[None]] | None = None,
    ) -> None:
        self.database = database
        self.data_dir = Path(data_dir)
        self.allowed_roots = [Path(item).resolve() for item in allowed_roots]
        self.parser = parser or StructuralParser()
        self.chunker = chunker or StructuralChunker()
        self.extractor = extractor
        self.normative_source_ids = frozenset(normative_source_ids or ())
        self.chunk_config = chunk_config or ChunkConfig(fingerprint="kw-chunker-v1")
        self.limits = limits or ParseLimits()
        self.failure_injector = failure_injector
        index_root = self.data_dir / "indexes"
        self.bm25 = Bm25Index(index_root)
        self.symbol = SymbolIndex(index_root)
        self.entity = EntityIndex(index_root)
        self.dense = (
            QdrantDenseIndex(qdrant_url, embedding_provider, api_key=qdrant_api_key)
            if qdrant_url and embedding_provider
            else None
        )
        self.knowledge_store = PostgresKnowledgeStore(database)
        self.graph_store = PostgresGraphStore(database, self.knowledge_store)
        self.candidate_indexes = {
            "bm25": self.bm25,
            "dense": self.dense,
            "symbol": self.symbol,
            "entity": self.entity,
        }

    async def ingest(self, request: IngestRequest) -> IndexReport:
        source = request.source_spec
        if source.corpus_id != request.corpus_id:
            raise KnowledgeWeaverError(ErrorCode.INVALID_INPUT, "Source corpus_id must match ingest corpus_id.")
        root = Path(source.logical_uri)
        if not root.is_dir():
            raise KnowledgeWeaverError(ErrorCode.PATH_DENIED, "Source logical_uri must resolve to an approved local directory.")
        resolved_root = root.resolve()
        if not any(resolved_root == allowed or allowed in resolved_root.parents for allowed in self.allowed_roots):
            raise KnowledgeWeaverError(ErrorCode.PATH_DENIED, "Source root is outside the configured ingest allowlist.")
        root = resolved_root
        normative_source = source.id in self.normative_source_ids
        if normative_source and source.kind is SourceKind.python_repo:
            raise KnowledgeWeaverError(
                ErrorCode.INVALID_INPUT,
                "A Python repository source cannot use the normative text profile.",
            )
        provider_configured = (
            self.extractor.generation_provider is not None
            if isinstance(self.extractor, KnowledgeExtractor)
            else False
            if self.extractor is None
            else None
        )
        extraction_profile_sha256 = _source_extraction_profile_sha256(
            source,
            normative_source=normative_source,
            provider_configured=provider_configured,
        )
        req_payload = request.model_dump(mode="json")
        req_payload["extraction_profile_sha256"] = extraction_profile_sha256
        request_hash = sha256_text(json.dumps(req_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        job_id = deterministic_id("job", {"corpus_id": request.corpus_id, "idempotency_key": request.idempotency_key})
        async with self.database.session() as session:
            job = await session.get(JobRow, job_id)
            if job is not None:
                if job.request_hash != request_hash:
                    raise KnowledgeWeaverError(ErrorCode.GENERATION_CONFLICT, "Idempotency key was already used for a different request.")
                if job.result:
                    return IndexReport.model_validate(job.result)
            corpus = await session.get(CorpusRow, request.corpus_id)
            if corpus is None:
                corpus = CorpusRow(id=request.corpus_id, active_generation_id=None, created_at=datetime.now(UTC))
                session.add(corpus)
                await session.flush()
            existing = await session.get(SourceRow, source.id)
            if existing is not None and existing.corpus_id != source.corpus_id:
                raise KnowledgeWeaverError(ErrorCode.GENERATION_CONFLICT, "Source id belongs to a different corpus.")
            if existing is None:
                session.add(SourceRow(id=source.id, corpus_id=source.corpus_id, kind=source.kind.value, logical_uri=source.logical_uri, display_name=source.display_name, license=source.license, created_at=source.created_at))
                await session.flush()
            else:
                existing.logical_uri = source.logical_uri
                existing.display_name = source.display_name
                existing.license = source.license
                existing.deleted_at = None
            active_id = corpus.active_generation_id
            active = await session.get(GenerationRow, active_id) if active_id else None
            previous_generation = active_id
            previous = await self._source_version_for_generation(session, active, source.id) if active else None
            manifest = ManifestBuilder().build(source, root, self.limits, previous=previous)
            if manifest.source_version is None:
                raise KnowledgeWeaverError(ErrorCode.PATH_DENIED, "Source manifest could not be safely built.")
            version = manifest.source_version
            active_profiles = (
                active.index_manifest.get("extraction_profiles_by_source", {})
                if active is not None
                else {}
            )
            if not isinstance(active_profiles, dict):
                active_profiles = {}
            if (
                previous is not None
                and previous.manifest_hash == version.manifest_hash
                and active_profiles.get(source.id) == extraction_profile_sha256
            ):
                report = IndexReport(job_id=job_id, generation_id=active_id, state=PipelineServiceStatus.succeeded, counts={"sources": 1}, warnings=list(manifest.warnings), previous_generation_id=active_id, no_change=True)
                await self._record_job(session, job_id, request, request_hash, report)
                await session.commit()
                return report
            old_members = await self._member_rows(session, active_id) if active_id else {}
            if active is not None:
                old_members.setdefault("source_version", set()).update(active.source_version_ids)
            members_versions = set(old_members.get("source_version", set()))
            for old_version_id in list(members_versions):
                old_version = await session.get(SourceVersionRow, old_version_id)
                if old_version is None or old_version.source_id == source.id:
                    members_versions.discard(old_version_id)
            members_versions.add(version.id)
            planned_versions = sorted(members_versions)
            generation_id = new_id()
            gen_hash = sha256_text(json.dumps({"corpus_id": request.corpus_id, "source_version_ids": planned_versions, "previous": active_id}, sort_keys=True, separators=(",", ":")))
            generation = GenerationRow(id=generation_id, corpus_id=request.corpus_id, state=GenerationState.BUILDING.value,
                source_version_ids=planned_versions, index_manifest={}, embedding_fingerprint="pending", generation_hash=gen_hash, created_at=datetime.now(UTC), published_at=None)
            session.add(generation)
            await session.flush()
            try:
                members = await self._replace_source_members(session, old_members, source.id, active_id)
                members.setdefault("source_version", set()).add(version.id)
                await self._persist_version(session, version)
                parse_version, reused = await self._parse_incremental(session, source, root, version, previous)
                parsed = await self.parser.parse_detailed(ParseRequest(source=source, source_version=parse_version, allowed_root=str(root), limits=self.limits))
                if request.strict and (manifest.rejected_files or parsed.parsed_source.rejected_files):
                    raise KnowledgeWeaverError(ErrorCode.PARSE_FAILED, "Strict ingest rejected one or more source files.")
                all_docs = await self._load_reused_documents(session, reused)
                all_docs.update({item.id: item for item in parsed.parsed_source.documents})
                documents = [all_docs[item.document_id] for item in version.files if item.document_id in all_docs]
                chunks_by_doc = {doc_id: await self._load_doc_chunks(session, doc_id) for doc_id in reused}
                for parsed_document in parsed.parsed_source.documents:
                    chunks_by_doc[parsed_document.id] = self.chunker.split(parsed_document, self.chunk_config)
                python_source_index = _python_source_index_for_version(
                    source,
                    version,
                    documents_by_id=all_docs,
                    corpus_id=request.corpus_id,
                )
                extractor = self.extractor
                if extractor is None or isinstance(extractor, KnowledgeExtractor):
                    inherited_source_kinds = (
                        extractor.source_kinds_by_id if extractor is not None else {}
                    )
                    inherited_normative_sources = (
                        extractor.normative_source_ids if extractor is not None else frozenset()
                    )
                    extractor = KnowledgeExtractor(
                        extractor.generation_provider if extractor is not None else None,
                        corpus_id=request.corpus_id,
                        corpus_ids_by_source=(
                            extractor.corpus_ids_by_source if extractor is not None else None
                        ),
                        source_kinds_by_id={
                            **inherited_source_kinds,
                            source.id: source.kind,
                        },
                        normative_source_ids=(
                            set(inherited_normative_sources)
                            | (set(self.normative_source_ids) & {source.id})
                        ),
                        timeout_seconds=(
                            extractor.timeout_seconds if extractor is not None else 30.0
                        ),
                        max_schema_repairs=(
                            extractor.max_schema_repairs if extractor is not None else 1
                        ),
                        _python_source_index=python_source_index,
                        _python_source_version_id=(
                            version.id if python_source_index is not None else None
                        ),
                    )
                extraction_observations: list[str] = []
                for entry in version.files:
                    document_item = all_docs.get(entry.document_id)
                    if document_item is None:
                        if request.strict:
                            raise KnowledgeWeaverError(ErrorCode.PARSE_FAILED, "A manifest member could not be parsed.")
                        continue
                    document = document_item
                    await self._persist_document(session, document)
                    if await session.get(SourceVersionDocumentRow, (version.id, document.id)) is None:
                        session.add(SourceVersionDocumentRow(source_version_id=version.id, document_id=document.id))
                    chunks = chunks_by_doc[document.id]
                    await self._persist_chunks(session, chunks)
                    members.setdefault("document", set()).add(document.id)
                    members.setdefault("chunk", set()).update(chunk.id for chunk in chunks)
                    leaf_chunks = [chunk for chunk in chunks if chunk.is_leaf]
                    extracted = await extractor.extract(
                        ExtractionRequest(
                            document=document,
                            chunks=leaf_chunks,
                            producer_fingerprint=(
                                f"kw-extractor-v2:{extraction_profile_sha256}"
                            ),
                        )
                    )
                    extraction_observations.extend(_extraction_observations(extracted))
                    provenance = {item.id: item for item in extracted.provenance}
                    evidence = {item.id: item for item in extracted.evidence}
                    for chunk in leaf_chunks:
                        for span in chunk.spans:
                            base = Provenance(id=provenance_id(ProvenanceMethod.PARSER_EXCERPT.value, "kw-parser-evidence-v1", document.source_id, document.id), method=ProvenanceMethod.PARSER_EXCERPT, producer_fingerprint="kw-parser-evidence-v1", source_id=document.source_id, document_id=document.id)
                            provenance[base.id] = base
                            span_text = chunk.text[span.chunk_start:span.chunk_end]
                            leading = len(span_text) - len(span_text.lstrip())
                            trailing = len(span_text.rstrip())
                            if trailing <= leading:
                                continue
                            evidence_start = span.chunk_start + leading
                            evidence_end = span.chunk_start + trailing
                            quote = chunk.text[evidence_start:evidence_end]
                            ev = Evidence(id=evidence_id(chunk.id, evidence_start, evidence_end, quote, base.id), chunk_id=chunk.id, start=evidence_start, end=evidence_end, quote=quote, locator=span.locator, provenance_id=base.id)
                            ev.assert_matches_chunk(chunk)
                            evidence[ev.id] = ev
                    await self._persist_extraction(session, extracted, evidence, provenance, members)
                await session.flush()
                await self._inject("after_facts")
                index_manifest, embed_fingerprint = await self._build_indexes(session, request.corpus_id, generation_id, members)
                next_extraction_profiles = dict(active_profiles)
                next_extraction_profiles[source.id] = extraction_profile_sha256
                index_manifest["extraction_profiles_by_source"] = dict(
                    sorted(next_extraction_profiles.items())
                )
                await self._inject("after_indexes")
                await self._validate_generation(session, members)
                member_hashes = {kind: sha256_text("\n".join(sorted(ids))) for kind, ids in sorted(members.items())}
                index_manifest["member_hashes"] = member_hashes
                generation.generation_hash = sha256_text(json.dumps({"source_version_ids": generation.source_version_ids, "member_hashes": member_hashes, "index_manifest": index_manifest}, sort_keys=True, separators=(",", ":")))
                index_manifest["generation_hash"] = generation.generation_hash
                generation.index_manifest = index_manifest
                generation.embedding_fingerprint = embed_fingerprint
                generation.state = GenerationState.READY.value
                generation.published_at = datetime.now(UTC)
                membership_tables = {
                    "source_version": (GenerationSourceVersionRow, "source_version_id"),
                    "document": (GenerationDocumentRow, "document_id"),
                    "chunk": (GenerationChunkRow, "chunk_id"),
                    "evidence": (GenerationEvidenceRow, "evidence_id"),
                    "provenance": (GenerationProvenanceRow, "provenance_id"),
                    "entity": (GenerationEntityRow, "entity_id"),
                    "mention": (GenerationMentionRow, "mention_id"),
                    "relation": (GenerationRelationRow, "relation_id"),
                }
                for kind, ids in members.items():
                    table, column = membership_tables[kind]
                    for member_id in sorted(ids):
                        session.add(table(generation_id=generation_id, **{column: member_id}))
                await session.flush()
                await self._inject("before_cas")
                statement = update(CorpusRow).where(CorpusRow.id == request.corpus_id, CorpusRow.active_generation_id == active_id).values(active_generation_id=generation_id)
                if active_id is None:
                    statement = update(CorpusRow).where(CorpusRow.id == request.corpus_id, CorpusRow.active_generation_id.is_(None)).values(active_generation_id=generation_id)
                result = await session.execute(statement)
                if result.rowcount != 1:
                    raise KnowledgeWeaverError(ErrorCode.GENERATION_CONFLICT, "Another publisher changed the active generation.")
                await self._inject("after_cas")
                if active is not None:
                    active.state = GenerationState.RETIRED.value
                report = IndexReport(job_id=job_id, generation_id=generation_id, state=PipelineServiceStatus.succeeded,
                    counts={"documents": len(documents), "chunks": len(members.get("chunk", set())), "evidence": len(members.get("evidence", set())), "entities": len(members.get("entity", set())), "relations": len(members.get("relation", set()))}, warnings=list(manifest.warnings) + list(parsed.parsed_source.warnings) + extraction_observations, previous_generation_id=previous_generation)
                await self._record_job(session, job_id, request, request_hash, report)
                await session.commit()
                return report
            except Exception:
                await session.rollback()
                if self.dense is not None:
                    await self.dense.delete_generation(request.corpus_id, generation_id)
                artifact_dir = self.data_dir / "indexes" / request.corpus_id / generation_id
                if artifact_dir.exists() and self.data_dir.resolve() in artifact_dir.resolve().parents:
                    for artifact in artifact_dir.iterdir():
                        if artifact.is_file() and artifact.name in {"bm25.json", "symbol.json", "entity.json"}:
                            artifact.unlink()
                    try:
                        artifact_dir.rmdir()
                    except OSError:
                        pass
                raise

    async def delete_source(self, request: DeleteSourceRequest) -> IndexReport:
        generation_id = new_id()
        try:
            return await self._delete_source_transaction(request, generation_id)
        except Exception:
            if self.dense is not None:
                await self.dense.delete_generation(request.corpus_id, generation_id)
            artifact_dir = self.data_dir / "indexes" / request.corpus_id / generation_id
            if artifact_dir.exists() and self.data_dir.resolve() in artifact_dir.resolve().parents:
                for artifact in artifact_dir.iterdir():
                    if artifact.is_file() and artifact.name in {"bm25.json", "symbol.json", "entity.json"}:
                        artifact.unlink()
                try:
                    artifact_dir.rmdir()
                except OSError:
                    pass
            raise

    async def _delete_source_transaction(self, request: DeleteSourceRequest, new_generation_id: str) -> IndexReport:
        async with self.database.session() as session:
            job_id = deterministic_id("job", {"corpus_id": request.corpus_id, "idempotency_key": request.idempotency_key})
            request_hash = sha256_text(json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")))
            prior_job = await session.get(JobRow, job_id)
            if prior_job is not None:
                if prior_job.request_hash != request_hash:
                    raise KnowledgeWeaverError(ErrorCode.GENERATION_CONFLICT, "Idempotency key was already used for a different delete request.")
                if prior_job.result:
                    return IndexReport.model_validate(prior_job.result)
            corpus = await session.get(CorpusRow, request.corpus_id)
            source = await session.get(SourceRow, request.source_id)
            if corpus is None or source is None or source.corpus_id != request.corpus_id:
                raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "Source does not exist in this corpus.")
            active = await session.get(GenerationRow, corpus.active_generation_id) if corpus.active_generation_id else None
            if active is None:
                raise KnowledgeWeaverError(ErrorCode.NO_READY_GENERATION, "Corpus has no published generation.")
            source_is_active = False
            for version_id in active.source_version_ids:
                version = await session.get(SourceVersionRow, version_id)
                if version is not None and version.source_id == source.id:
                    source_is_active = True
                    break
            if not source_is_active:
                report = IndexReport(job_id=job_id, generation_id=active.id, state=PipelineServiceStatus.succeeded, previous_generation_id=active.id, no_change=True)
                await self._record_job(session, job_id, request, request_hash, report)
                await session.commit()
                return report
            old_members = await self._member_rows(session, active.id)
            members = await self._replace_source_members(session, old_members, source.id, active.id)
            versions = []
            for version_id in active.source_version_ids:
                version_row = await session.get(SourceVersionRow, version_id)
                if version_row is not None and version_row.source_id != source.id:
                    versions.append(version_id)
            row = GenerationRow(id=new_generation_id, corpus_id=request.corpus_id, state="BUILDING", source_version_ids=versions, index_manifest={}, embedding_fingerprint=active.embedding_fingerprint, generation_hash=sha256_text(new_generation_id), created_at=datetime.now(UTC))
            session.add(row)
            await session.flush()
            members.setdefault("source_version", set()).difference_update(
                version_id for version_id in list(members.get("source_version", set()))
                if version_id not in versions
            )
            await self._validate_generation(session, members)
            index_manifest, embed_fingerprint = await self._build_indexes(session, request.corpus_id, new_generation_id, members)
            await self._inject("after_facts")
            await self._inject("after_indexes")
            member_hashes = {kind: sha256_text("\n".join(sorted(ids))) for kind, ids in sorted(members.items())}
            index_manifest["member_hashes"] = member_hashes
            row.generation_hash = sha256_text(json.dumps({"source_version_ids": versions, "member_hashes": member_hashes, "index_manifest": index_manifest}, sort_keys=True, separators=(",", ":")))
            index_manifest["generation_hash"] = row.generation_hash
            row.index_manifest = index_manifest
            row.embedding_fingerprint = embed_fingerprint
            membership_tables = {
                "source_version": (GenerationSourceVersionRow, "source_version_id"),
                "document": (GenerationDocumentRow, "document_id"),
                "chunk": (GenerationChunkRow, "chunk_id"),
                "evidence": (GenerationEvidenceRow, "evidence_id"),
                "provenance": (GenerationProvenanceRow, "provenance_id"),
                "entity": (GenerationEntityRow, "entity_id"),
                "mention": (GenerationMentionRow, "mention_id"),
                "relation": (GenerationRelationRow, "relation_id"),
            }
            for kind, ids in members.items():
                table, column = membership_tables[kind]
                for member in ids:
                    session.add(table(generation_id=new_generation_id, **{column: member}))
            row.state = "READY"
            row.published_at = datetime.now(UTC)
            source.deleted_at = datetime.now(UTC)
            await session.flush()
            await self._inject("before_cas")
            result = await session.execute(update(CorpusRow).where(CorpusRow.id == request.corpus_id, CorpusRow.active_generation_id == active.id).values(active_generation_id=new_generation_id))
            if result.rowcount != 1:
                raise KnowledgeWeaverError(ErrorCode.GENERATION_CONFLICT, "Another publisher changed the active generation.")
            await self._inject("after_cas")
            active.state = "RETIRED"
            report = IndexReport(job_id=job_id, generation_id=new_generation_id, state=PipelineServiceStatus.succeeded, counts={"sources": max(0, len(versions)), "chunks": len(members.get("chunk", set()))}, previous_generation_id=active.id)
            await self._record_job(session, job_id, request, request_hash, report)
            await session.commit()
            return report

    async def _source_version_for_generation(self, session: AsyncSession, generation: GenerationRow | None, source_id: str) -> SourceVersion | None:
        if generation is None:
            return None
        for version_id in generation.source_version_ids:
            row = await session.get(SourceVersionRow, version_id)
            if row and row.source_id == source_id:
                return SourceVersion(id=row.id, source_id=row.source_id, manifest_hash=row.manifest_hash, revision=row.revision, dirty=row.dirty, files=row.files)
        return None

    async def _member_rows(self, session: AsyncSession, generation_id: str) -> dict[str, set[str]]:
        tables: dict[str, tuple[Any, str]] = {
            "source_version": (GenerationSourceVersionRow, "source_version_id"),
            "document": (GenerationDocumentRow, "document_id"),
            "chunk": (GenerationChunkRow, "chunk_id"),
            "evidence": (GenerationEvidenceRow, "evidence_id"),
            "provenance": (GenerationProvenanceRow, "provenance_id"),
            "entity": (GenerationEntityRow, "entity_id"),
            "mention": (GenerationMentionRow, "mention_id"),
            "relation": (GenerationRelationRow, "relation_id"),
        }
        output: dict[str, set[str]] = {}
        for kind, (model, member_column) in tables.items():
            result: Any = await session.scalars(select(getattr(model, member_column)).where(model.generation_id == generation_id))
            output[kind] = set(result.all())
        return output

    async def _replace_source_members(self, session: AsyncSession, old: dict[str, set[str]], source_id: str, generation_id: str | None) -> dict[str, set[str]]:
        members = {kind: set(ids) for kind, ids in old.items()}
        if not generation_id:
            return members
        old_versions = (await session.scalars(select(SourceVersionRow).where(SourceVersionRow.id.in_(members.get("source_version", set()))))).all() if members.get("source_version") else []
        removed_versions = {row.id for row in old_versions if row.source_id == source_id}
        members.setdefault("source_version", set()).difference_update(removed_versions)
        doc_ids = set((await session.scalars(select(SourceVersionDocumentRow.document_id).where(SourceVersionDocumentRow.source_version_id.in_(removed_versions)))).all()) if removed_versions else set()
        if not doc_ids:
            return members
        members.setdefault("document", set()).difference_update(doc_ids)
        chunk_ids = set((await session.scalars(select(ChunkRow.id).where(ChunkRow.document_id.in_(doc_ids)))).all())
        members.setdefault("chunk", set()).difference_update(chunk_ids)
        evidence_ids = set((await session.scalars(select(EvidenceRow.id).where(EvidenceRow.chunk_id.in_(chunk_ids)))).all()) if chunk_ids else set()
        members.setdefault("evidence", set()).difference_update(evidence_ids)
        provenance_ids = set((await session.scalars(select(ProvenanceRow.id).where(ProvenanceRow.document_id.in_(doc_ids)))).all())
        members.setdefault("provenance", set()).difference_update(provenance_ids)
        removed_mention_ids = set((await session.scalars(select(MentionRow.id).where(MentionRow.evidence_id.in_(evidence_ids)))).all()) if evidence_ids else set()
        affected_entity_ids = set((await session.scalars(select(MentionRow.entity_id).where(MentionRow.id.in_(removed_mention_ids)))).all()) if removed_mention_ids else set()
        members.setdefault("mention", set()).difference_update(removed_mention_ids)
        remaining_entity_ids = set((await session.scalars(select(MentionRow.entity_id).where(
            MentionRow.id.in_(members.get("mention", set())),
            MentionRow.entity_id.in_(affected_entity_ids),
        ))).all()) if members.get("mention") and affected_entity_ids else set()
        removed_entity_ids = affected_entity_ids - remaining_entity_ids
        members.setdefault("entity", set()).difference_update(removed_entity_ids)
        evidence_relation_ids = set((await session.scalars(select(RelationEvidenceRow.relation_id).where(RelationEvidenceRow.evidence_id.in_(evidence_ids)))).all()) if evidence_ids else set()
        members.setdefault("relation", set()).difference_update(evidence_relation_ids)
        if members.get("relation"):
            relation_rows = (await session.scalars(select(RelationRow).where(RelationRow.id.in_(members["relation"])))).all()
            invalid_relation_ids = {
                row.id for row in relation_rows
                if row.subject_id not in members.get("entity", set())
                or row.object_id not in members.get("entity", set())
                or row.provenance_id not in members.get("provenance", set())
            }
            members["relation"].difference_update(invalid_relation_ids)
        return members

    async def _persist_version(self, session: AsyncSession, version: SourceVersion) -> None:
        existing = await session.get(SourceVersionRow, version.id)
        if existing is None:
            session.add(SourceVersionRow(id=version.id, source_id=version.source_id, manifest_hash=version.manifest_hash, revision=version.revision, dirty=version.dirty, files=[item.model_dump(mode="json") for item in version.files]))

    async def _parse_incremental(self, session: AsyncSession, source: Source, root: Path, version: SourceVersion, previous: SourceVersion | None) -> tuple[SourceVersion, set[str]]:
        old_by_path = {item.relative_path.casefold(): item for item in previous.files} if previous else {}
        changed: list[FileManifestEntry] = []
        reused: set[str] = set()
        for entry in version.files:
            prior = old_by_path.get(entry.relative_path.casefold())
            if prior and prior.document_id == entry.document_id:
                present = await session.get(DocumentRow, entry.document_id)
                if present:
                    reused.add(entry.document_id)
                    continue
            changed.append(entry)
        filtered = version.model_copy(update={"files": changed})
        return filtered, reused

    async def _load_reused_documents(self, session: AsyncSession, ids: set[str]) -> dict[str, Document]:
        if not ids:
            return {}
        rows = (await session.scalars(select(DocumentRow).where(DocumentRow.id.in_(ids)))).all()
        return {row.id: Document.model_validate({"id": row.id, "source_id": row.source_id, "relative_path": row.relative_path, "raw_hash": row.raw_hash, "parser_fingerprint": row.parser_fingerprint, "normalized_text": row.normalized_text, "blocks": row.blocks, "warnings": row.warnings}) for row in rows}

    async def _load_doc_chunks(self, session: AsyncSession, document_id: str) -> list[Chunk]:
        rows = (await session.scalars(select(ChunkRow).where(ChunkRow.document_id == document_id))).all()
        return [Chunk.model_validate(row.payload) for row in rows]

    async def _persist_document(self, session: AsyncSession, document: Document) -> None:
        if await session.get(DocumentRow, document.id) is None:
            session.add(DocumentRow(id=document.id, source_id=document.source_id, relative_path=document.relative_path, raw_hash=document.raw_hash, parser_fingerprint=document.parser_fingerprint, normalized_text=document.normalized_text, blocks=[block.model_dump(mode="json") for block in document.blocks], warnings=document.warnings))
            # ORM rows lack relationships, so the unit of work cannot order this
            # parent/child pair from mapper dependencies alone.
            await session.flush()
            for block in document.blocks:
                session.add(BlockRow(id=block.id, document_id=document.id, payload=block.model_dump(mode="json")))

    async def _persist_chunks(self, session: AsyncSession, chunks: list[Chunk]) -> None:
        chunks_by_id = {chunk.id: chunk for chunk in chunks}
        external_parent_ids = {
            chunk.parent_id
            for chunk in chunks
            if chunk.parent_id is not None and chunk.parent_id not in chunks_by_id
        }
        for parent_id in sorted(external_parent_ids):
            if await session.get(ChunkRow, parent_id) is None:
                raise KnowledgeWeaverError(
                    ErrorCode.MISSING_REFERENCE,
                    "Chunk parent does not exist.",
                )

        remaining_ids = set(chunks_by_id)
        depths: dict[str, int] = {}
        while remaining_ids:
            ready_ids = [
                chunk.id
                for chunk in chunks
                if chunk.id in remaining_ids
                and (chunk.parent_id is None or chunk.parent_id not in remaining_ids)
            ]
            if not ready_ids:
                raise KnowledgeWeaverError(
                    ErrorCode.MISSING_REFERENCE,
                    "Chunk parent hierarchy contains a cycle.",
                )
            for chunk_id in ready_ids:
                chunk = chunks_by_id[chunk_id]
                chunk_parent_id = chunk.parent_id
                depths[chunk_id] = (
                    depths[chunk_parent_id] + 1
                    if chunk_parent_id is not None and chunk_parent_id in depths
                    else 0
                )
                remaining_ids.remove(chunk_id)

        for depth in sorted(set(depths.values())):
            for chunk in chunks:
                if depths[chunk.id] != depth or await session.get(ChunkRow, chunk.id) is not None:
                    continue
                session.add(
                    ChunkRow(
                        id=chunk.id,
                        document_id=chunk.document_id,
                        parent_id=chunk.parent_id,
                        is_leaf=chunk.is_leaf,
                        text=chunk.text,
                        text_hash=chunk.text_hash,
                        payload=chunk.model_dump(mode="json"),
                    )
                )
            await session.flush()

    async def _persist_extraction(self, session: AsyncSession, extracted: ExtractionResult, evidence: dict[str, Evidence], provenance: dict[str, Provenance], members: dict[str, set[str]]) -> None:
        for provenance_row in provenance.values():
            if await session.get(ProvenanceRow, provenance_row.id) is None:
                session.add(ProvenanceRow(id=provenance_row.id, source_id=provenance_row.source_id, document_id=provenance_row.document_id, method=provenance_row.method.value, payload=provenance_row.model_dump(mode="json")))
            members.setdefault("provenance", set()).add(provenance_row.id)
        for evidence_row in evidence.values():
            if await session.get(EvidenceRow, evidence_row.id) is None:
                session.add(EvidenceRow(id=evidence_row.id, chunk_id=evidence_row.chunk_id, provenance_id=evidence_row.provenance_id, start=evidence_row.start, end=evidence_row.end, quote=evidence_row.quote, payload=evidence_row.model_dump(mode="json")))
            members.setdefault("evidence", set()).add(evidence_row.id)
        for entity_fact in extracted.entities:
            if await session.get(EntityRow, entity_fact.id) is None:
                session.add(EntityRow(id=entity_fact.id, corpus_id=entity_fact.corpus_id, kind=entity_fact.kind.value, namespace=entity_fact.namespace, identity_key=entity_fact.identity_key, payload=entity_fact.model_dump(mode="json")))
            members.setdefault("entity", set()).add(entity_fact.id)
        for mention_fact in extracted.mentions:
            if await session.get(MentionRow, mention_fact.id) is None:
                session.add(MentionRow(id=mention_fact.id, entity_id=mention_fact.entity_id, evidence_id=mention_fact.evidence_id, surface_form=mention_fact.surface_form, payload=mention_fact.model_dump(mode="json")))
            members.setdefault("mention", set()).add(mention_fact.id)
        for relation_fact in extracted.relations:
            if await session.get(RelationRow, relation_fact.id) is None:
                session.add(RelationRow(id=relation_fact.id, subject_id=relation_fact.subject_id, object_id=relation_fact.object_id, provenance_id=relation_fact.provenance_id, predicate=relation_fact.predicate.value, payload=relation_fact.model_dump(mode="json")))
            members.setdefault("relation", set()).add(relation_fact.id)
            for evidence_ref in relation_fact.evidence_ids:
                if await session.get(RelationEvidenceRow, (relation_fact.id, evidence_ref)) is None:
                    session.add(RelationEvidenceRow(relation_id=relation_fact.id, evidence_id=evidence_ref))

    async def _validate_generation(self, session: AsyncSession, members: dict[str, set[str]]) -> None:
        chunks = (await session.scalars(select(ChunkRow).where(ChunkRow.id.in_(members.get("chunk", set()))))).all() if members.get("chunk") else []
        chunk_by_id = {row.id: Chunk.model_validate(row.payload) for row in chunks}
        evidence_rows = (await session.scalars(select(EvidenceRow).where(EvidenceRow.id.in_(members.get("evidence", set()))))).all() if members.get("evidence") else []
        for evidence_row in evidence_rows:
            evidence = Evidence.model_validate(evidence_row.payload)
            chunk = chunk_by_id.get(evidence.chunk_id)
            if chunk is None:
                raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "Evidence points outside generation chunk members.")
            evidence.assert_matches_chunk(chunk)
            if evidence.provenance_id not in members.get("provenance", set()):
                raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "Evidence provenance is not a generation member.")
        mention_rows = (await session.scalars(select(MentionRow).where(MentionRow.id.in_(members.get("mention", set()))))).all() if members.get("mention") else []
        for mention_row in mention_rows:
            if mention_row.entity_id not in members.get("entity", set()) or mention_row.evidence_id not in members.get("evidence", set()):
                raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "Mention references are not closed within the generation.")
        relation_rows = (await session.scalars(select(RelationRow).where(RelationRow.id.in_(members.get("relation", set()))))).all() if members.get("relation") else []
        relation_evidence = (await session.scalars(select(RelationEvidenceRow).where(RelationEvidenceRow.relation_id.in_(members.get("relation", set()))))).all() if members.get("relation") else []
        refs_by_relation: dict[str, set[str]] = {}
        for relation_evidence_row in relation_evidence:
            refs_by_relation.setdefault(relation_evidence_row.relation_id, set()).add(relation_evidence_row.evidence_id)
        for relation_row in relation_rows:
            if relation_row.subject_id not in members.get("entity", set()) or relation_row.object_id not in members.get("entity", set()):
                raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "Relation endpoints are not generation members.")
            if relation_row.provenance_id not in members.get("provenance", set()) or not refs_by_relation.get(relation_row.id) or not refs_by_relation[relation_row.id].issubset(members.get("evidence", set())):
                raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "Relation evidence/provenance is not closed within the generation.")
        mentioned_entity_ids = {row.entity_id for row in mention_rows}
        if not members.get("entity", set()).issubset(mentioned_entity_ids):
            raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "Every generation entity must have a current generation mention.")

    async def _build_indexes(self, session: AsyncSession, corpus_id: str, generation_id: str, members: dict[str, set[str]]) -> tuple[dict[str, Any], str]:
        chunk_rows = (await session.scalars(select(ChunkRow).where(ChunkRow.id.in_(members.get("chunk", set()))))).all() if members.get("chunk") else []
        chunks = [Chunk.model_validate(row.payload) for row in chunk_rows if row.is_leaf]
        chunk_by_id = {chunk.id: chunk for chunk in chunks}
        doc_ids = {chunk.document_id for chunk in chunks}
        doc_rows = (await session.scalars(select(DocumentRow).where(DocumentRow.id.in_(doc_ids)))).all() if doc_ids else []
        doc_by_id = {row.id: row for row in doc_rows}
        rows = [{"chunk_id": chunk.id, "source_id": doc_by_id[chunk.document_id].source_id, "text": chunk.text} for chunk in chunks if chunk.document_id in doc_by_id]
        paths = {"bm25": self.bm25.build(corpus_id, generation_id, rows)}
        symbol_rows: list[dict[str, Any]] = []
        entity_rows: list[dict[str, Any]] = []
        if members.get("mention"):
            mention_rows = (await session.scalars(select(MentionRow).where(MentionRow.id.in_(members["mention"])))).all()
            entity_ids = {item.entity_id for item in mention_rows}
            entities = {row.id: row for row in (await session.scalars(select(EntityRow).where(EntityRow.id.in_(entity_ids)))).all()} if entity_ids else {}
            evidence_ids = {item.evidence_id for item in mention_rows}
            evidence_rows = {row.id: row for row in (await session.scalars(select(EvidenceRow).where(EvidenceRow.id.in_(evidence_ids)))).all()} if evidence_ids else {}
            for mention in mention_rows:
                evidence = evidence_rows.get(mention.evidence_id)
                if evidence is None:
                    continue
                chunk = chunk_by_id.get(evidence.chunk_id)
                if chunk is None or chunk.document_id not in doc_by_id:
                    continue
                entity = entities[mention.entity_id]
                from knowledge_weaver.models import Entity

                fact = Entity.model_validate(entity.payload)
                record = {"chunk_id": chunk.id, "source_id": doc_by_id[chunk.document_id].source_id, "entity_id": fact.id, "name": fact.canonical_name, "namespace": fact.namespace, "identity_key": fact.identity_key, "aliases": fact.aliases}
                entity_rows.append({"chunk_id": chunk.id, "source_id": record["source_id"], "entity_id": fact.id, "canonical_name": fact.canonical_name, "namespace": fact.namespace, "identity_key": fact.identity_key, "aliases": fact.aliases})
                if fact.kind.value in {"module", "class", "function"}:
                    symbol_rows.append(record)
        paths["symbol"] = self.symbol.build(corpus_id, generation_id, symbol_rows)
        paths["entity"] = self.entity.build(corpus_id, generation_id, entity_rows)
        embed_meta = {"dimension": None, "model": None, "revision": None, "fingerprint": "none"}
        if self.dense is not None:
            embed_meta = await self.dense.publish(corpus_id, generation_id, rows)
        manifest = {"tokenizer_version": TOKENIZER_VERSION, "index_paths": paths, "dense": embed_meta, "counts": {"chunks": len(chunks), "evidence": len(members.get("evidence", set())), "entities": len(members.get("entity", set())), "relations": len(members.get("relation", set()))}}
        return manifest, str(embed_meta.get("fingerprint") or "none")

    async def _record_job(self, session: AsyncSession, job_id: str, request: IngestRequest | DeleteSourceRequest, request_hash: str, report: IndexReport) -> None:
        row = await session.get(JobRow, job_id)
        if row is None:
            session.add(JobRow(id=job_id, corpus_id=request.corpus_id, idempotency_key=request.idempotency_key, request_hash=request_hash, state="succeeded", result=report.model_dump(mode="json")))
        else:
            row.state = "succeeded"
            row.result = report.model_dump(mode="json")

    async def collect_garbage(self, corpus_id: str, *, now: datetime | None = None, minimum_retention: timedelta = timedelta(hours=24)) -> list[str]:
        """Remove expired, unleased generation memberships, Qdrant points and owned JSON indexes."""
        current = now or datetime.now(UTC)
        cutoff = current - minimum_retention
        async with self.database.session() as session:
            corpus = await session.get(CorpusRow, corpus_id)
            if corpus is None:
                return []
            await session.execute(delete(QueryLeaseRow).where(QueryLeaseRow.expires_at <= current))
            generations = (await session.scalars(select(GenerationRow).where(GenerationRow.corpus_id == corpus_id, GenerationRow.published_at <= cutoff, GenerationRow.id != (corpus.active_generation_id or "")))).all()
            removed: list[str] = []
            for generation in generations:
                lease = await session.scalar(select(QueryLeaseRow.request_id).where(QueryLeaseRow.generation_id == generation.id, QueryLeaseRow.expires_at > current).limit(1))
                if lease is not None:
                    continue
                if self.dense is not None:
                    await self.dense.delete_generation(corpus_id, generation.id)
                directory = self.data_dir / "indexes" / corpus_id / generation.id
                if directory.exists() and self.data_dir.resolve() in directory.resolve().parents:
                    for name in ("bm25.json", "symbol.json", "entity.json"):
                        file = directory / name
                        if file.is_file():
                            file.unlink()
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
                await session.delete(generation)
                removed.append(generation.id)
            await session.commit()
            return removed

    async def _inject(self, stage: str) -> None:
        if self.failure_injector is not None:
            result = self.failure_injector(stage)
            if inspect.isawaitable(result):
                await result
