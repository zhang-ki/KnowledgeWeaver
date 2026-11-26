from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path, PureWindowsPath
from typing import Any, cast
from uuid import uuid4

import httpx
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from knowledge_weaver.agent import AgentServiceImpl
from knowledge_weaver.agent.verifier import StructuredEvidenceVerifier
from knowledge_weaver.core.budget import BudgetLedger
from knowledge_weaver.core.config import RemoteUseApproval
from knowledge_weaver.core.errors import ErrorCode, KnowledgeWeaverError, ProviderUnavailable
from knowledge_weaver.core.ids import deterministic_id
from knowledge_weaver.evaluation.local_generation import (
    DevLocalOpenAIChatProfile,
    DevLocalOpenAICompatibleGenerationProvider,
)
from knowledge_weaver.graph import GraphRetriever
from knowledge_weaver.ingestion.paths import PathSafetyError, is_link_or_reparse
from knowledge_weaver.models import (
    Answer,
    Budget,
    DeleteSourceRequest,
    GraphOptions,
    IndexQuery,
    IngestRequest,
    InvestigationRequest,
    Language,
    PolicyKind,
    Query,
    QueryContext,
    RetrievalQuery,
    RetrievalResult,
    Source,
    SourceKind,
    SubQuestion,
)
from knowledge_weaver.ports import CandidateIndex, GenerationProvider
from knowledge_weaver.providers.embedding import LocalQwenEmbeddingProvider
from knowledge_weaver.providers.generation import CloudGenerationProvider
from knowledge_weaver.retrieval import (
    Bm25Retriever,
    DenseRetriever,
    EntityRetriever,
    HybridRetriever,
    SymbolRetriever,
)
from knowledge_weaver.storage.database import Database
from knowledge_weaver.storage.knowledge_store import PostgresKnowledgeStore
from knowledge_weaver.storage.pipeline import PipelineService
from knowledge_weaver.storage.schema import (
    AnswerRow,
    CorpusRow,
    DocumentRow,
    GenerationRow,
    InvestigationRow,
    JobRow,
    QueryLeaseRow,
    QueryRow,
    SourceRow,
    SourceVersionRow,
)

from .config import AppConfig
from .models import (
    DeleteSource,
    ExperimentEvidence,
    IngestionCreate,
    InvestigationCreate,
    JobView,
    RetrievalCreate,
    SourceCreate,
    SourceView,
)

_APP_JOB_PREFIX = "b08:"
_MAX_INVESTIGATION_SECONDS = 600
_FIXED_POLICY_LIMITS = {
    "max_steps": 6,
    "max_tool_calls": 12,
    "max_llm_calls": 8,
    "max_total_tokens": 12_000,
    "max_graph_edges": 200,
}


def _canonical_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_within(root: Path, child: Path) -> bool:
    root_value = os.path.normcase(str(root.resolve(strict=True)))
    child_value = os.path.normcase(str(child))
    try:
        return os.path.commonpath((root_value, child_value)) == root_value
    except ValueError:
        return False


def validate_source_path(value: str, roots: tuple[Path, ...]) -> Path:
    if not roots:
        raise PathSafetyError("No local source roots are configured")
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raise PathSafetyError("Source path must be absolute")
    try:
        resolved = raw.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PathSafetyError("Source path does not exist") from exc
    if not resolved.is_dir():
        raise PathSafetyError("Source path must be a directory")

    for root in roots:
        if not _is_within(root, resolved):
            continue
        if not _is_within(root, Path(os.path.abspath(raw))):
            raise PathSafetyError("Source path resolves through a link")
        relative = Path(os.path.relpath(Path(os.path.abspath(raw)), root))
        current = root
        for part in relative.parts:
            if part in {".", ".."}:
                continue
            current = current / part
            if is_link_or_reparse(current):
                raise PathSafetyError("Linked source paths are not allowed")
        return resolved
    raise PathSafetyError("Source path is outside the configured local roots")


def _safe_display_name(value: str | None, fallback: str) -> str:
    label = (value or fallback).strip()
    windows = PureWindowsPath(label)
    if not label or len(label) > 120 or windows.drive or Path(label).is_absolute():
        raise KnowledgeWeaverError(
            ErrorCode.INVALID_INPUT,
            "Display name must be a short label, not a filesystem path.",
        )
    if "/" in label or "\\" in label:
        raise KnowledgeWeaverError(
            ErrorCode.INVALID_INPUT,
            "Display name must not contain path separators.",
        )
    return label


def _request_hash_payload(body: InvestigationCreate) -> dict[str, Any]:
    return body.model_dump(mode="json", exclude={"retry_failed"})


def _internal_evidence() -> list[ExperimentEvidence]:
    provenance_hash = "4b2e43bc70fc8d74b94f4d4ac1c42c7e8c7af6b938c8ab98b394b344f313b67e"
    return [
        ExperimentEvidence(
            evidence_set="B07 internal Dev: real Dense two-query run",
            status="PASS_INTERNAL_SYNTHETIC_ONLY",
            summary=(
                "一条合成来源问题得到同一持久化 Generation 中经 PostgreSQL 校验的引用回答；"
                "一条无答案问题返回 insufficient，且没有 claim 或 citation。"
            ),
            provenance="docs/deliveries/B07-INTERNAL-FAST-LANDING-20261125.md",
            provenance_sha256=provenance_hash,
            rows=[
                {
                    "case": "supported answer",
                    "status": "answered",
                    "question": "What exact synthetic token does acceptance_token return?",
                    "answer": "acceptance_token returns the synthetic local token LARKSPUR.",
                    "dense_rank": 1,
                    "dense_score": 0.82498664,
                    "citation_validated_in_postgres": True,
                    "citation_id": "4f3047d5-8bc6-5727-abb7-128aa8db4a23",
                    "generation_id": "40b9ebb5-6963-47c6-b478-c5970620643b",
                },
                {
                    "case": "unsupported answer",
                    "status": "insufficient",
                    "question": "What exact PostgreSQL hostname does acceptance_token connect to?",
                    "claims": 0,
                    "citations": 0,
                    "dense_hits": 3,
                    "supported_hostname_evidence": 0,
                },
                {
                    "case": "independent graph check",
                    "status": "PASS_INTERNAL_SINGLE_HOP_ONLY",
                    "edge": "PyJWT.decode_complete → PyJWS.decode_complete",
                    "evidence_closed": True,
                    "reverse_direction_rejected": True,
                    "wrong_generation_rejected": True,
                    "report_sha256": "7fb23300e62448bc487ad8952bb7c4b2cb3e02ea1c8a1e442548ac00ae1c6254",
                },
            ],
            limitations=[
                "这是内部合成 Dev 证据，不是正式 B07、冻结 Gold/Test 矩阵分数或 B07.final 验收。",
                "两条 Dense 查询和一次图单跳检查没有证明 PDF 与代码的跨源多跳能力。",
                "正式 PublicPortBenchmarkExecutor 因 cache-scope receipts 不完整而 fail closed；未绕过该门禁。",
                "没有训练 SEGR 权重；本产品默认使用 fixed，只有显式选择 SEGR 开发模式时才可能使用 prior。",
            ],
            training_policy=(
                "未训练；本地 Agent 默认 policy=fixed。prior 是已标记的冷启动规则，不代表学得的 SEGR。"
            ),
        )
    ]


class _UnavailableIndex(CandidateIndex):
    async def search(self, query: IndexQuery) -> list[Any]:
        raise ProviderUnavailable(
            "Dense retrieval is not configured for this local installation.",
            category="configuration_missing",
            retryable=False,
            request_id=query.context.request_id,
        )


class _UnavailableGenerationProvider(GenerationProvider):
    async def generate(self, request: Any) -> Any:
        raise ProviderUnavailable(
            "A loopback chat model is not configured for this local installation.",
            category="configuration_missing",
            retryable=False,
        )


class LocalProduct:
    """B08 composition root and single-process local operation runner."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.database = Database(config.database_url)
        embedding_provider = self._new_embedding_provider(
            self._new_budget(max_steps=1, max_tool_calls=40, max_llm_calls=20)
        )
        dense_ready = config.qdrant_url is not None and embedding_provider is not None
        self.pipeline = PipelineService(
            self.database,
            data_dir=config.data_dir,
            allowed_roots=list(config.allowed_roots),
            qdrant_url=config.qdrant_url if dense_ready else None,
            qdrant_api_key=config.qdrant_api_key if dense_ready else None,
            embedding_provider=embedding_provider if dense_ready else None,
        )
        self.store: PostgresKnowledgeStore = self.pipeline.knowledge_store
        self._dense_lock = asyncio.Lock()
        self._job_lock = asyncio.Lock()
        self._job_slots = asyncio.Semaphore(1)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._started = False

        self.agent = AgentServiceImpl(
            self.store,
            self._agent_retrievers,
            self._generation_provider,
            verifier_factory=lambda generate: StructuredEvidenceVerifier(generate),
        )

    @classmethod
    def from_environment(cls) -> LocalProduct:
        return cls(AppConfig.from_environment())

    @staticmethod
    def _new_budget(
        *,
        max_steps: int = 6,
        max_tool_calls: int = 12,
        max_llm_calls: int = 8,
        max_total_tokens: int = 12_000,
        max_graph_edges: int = 200,
        timeout_seconds: int = 300,
    ) -> BudgetLedger:
        deadline = datetime.now(UTC) + timedelta(seconds=timeout_seconds)
        return BudgetLedger(
            Budget(
                deadline_utc=deadline,
                max_steps=max_steps,
                max_tool_calls=max_tool_calls,
                max_llm_calls=max_llm_calls,
                max_total_tokens=max_total_tokens,
                max_graph_edges=max_graph_edges,
            )
        )

    def _new_embedding_provider(self, ledger: BudgetLedger) -> LocalQwenEmbeddingProvider | None:
        profile = self.config.embedding_profile
        if profile is None:
            return None
        if profile.timeout_seconds < 60.0:
            profile = profile.model_copy(update={"timeout_seconds": 60.0})
        return LocalQwenEmbeddingProvider(profile, ledger)

    def _generation_provider(self, ledger: BudgetLedger) -> GenerationProvider:
        if self.config.remote_chat_profile is not None:
            approval = RemoteUseApproval(
                external_data_approved=True,
                budget_approved=True,
                calls_explicitly_enabled=True,
                currency_cap=Decimal("10.00"),
            )
            return CloudGenerationProvider(
                self.config.remote_chat_profile,
                approval,
                ledger,
                settings=self.config.runtime_settings,
            )
        if self.config.local_chat_endpoint is None or self.config.local_chat_model is None:
            return _UnavailableGenerationProvider()
        profile = DevLocalOpenAIChatProfile(
            endpoint_url=self.config.local_chat_endpoint,
            model=self.config.local_chat_model,
        )
        return DevLocalOpenAICompatibleGenerationProvider(profile, ledger)

    def _agent_retrievers(self, ledger: BudgetLedger) -> dict[str, Any]:
        if self.pipeline.dense is not None:
            provider = self._new_embedding_provider(ledger)
            if provider is None:
                raise KnowledgeWeaverError(
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    "Dense retrieval has no local embedding provider.",
                )
            self.pipeline.dense.provider = provider
            dense_index: CandidateIndex = self.pipeline.dense
        else:
            dense_index = _UnavailableIndex()
        graph = GraphRetriever(self.pipeline.graph_store, self.pipeline.entity, self.store)
        channels: dict[str, Any] = {
            "bm25": Bm25Retriever(self.pipeline.bm25, self.store),
            "dense": DenseRetriever(dense_index, self.store),
            "symbol": SymbolRetriever(self.pipeline.symbol, self.store),
            "entity": EntityRetriever(self.pipeline.entity, self.store),
            "graph": graph,
        }
        channels["hybrid"] = HybridRetriever(
            {name: channels[name] for name in ("bm25", "dense", "symbol", "entity")}
        )
        return channels

    def retrievers(self) -> dict[str, Any]:
        ledger = self._new_budget(max_tool_calls=30, max_total_tokens=40_000)
        return self._agent_retrievers(ledger)

    async def start(self) -> None:
        if self._started:
            return
        try:
            async with self.database.session() as session:
                await session.execute(text("SELECT 1"))
                corpus = await session.get(CorpusRow, self.config.corpus_id)
                if corpus is None:
                    session.add(
                        CorpusRow(
                            id=self.config.corpus_id,
                            active_generation_id=None,
                            created_at=datetime.now(UTC),
                        )
                    )
                stale = (
                    await session.scalars(
                        select(JobRow).where(
                            JobRow.corpus_id == self.config.corpus_id,
                            JobRow.state == "running",
                            JobRow.idempotency_key.like(f"{_APP_JOB_PREFIX}%"),
                        )
                    )
                ).all()
                for row in stale:
                    row.state = "failed"
                    old_result = row.result or {}
                    row.result = {
                        **old_result,
                        "operation": self._operation_for_key(row.idempotency_key),
                        "error": {
                            "code": "RESTARTED_BEFORE_COMPLETION",
                            "message": "The local process stopped before this job completed.",
                            "retryable": True,
                        },
                    }
                await session.commit()
        except SQLAlchemyError as exc:
            raise RuntimeError(
                "Local PostgreSQL is unavailable or the B03 schema has not been migrated. "
                f"Run the local doctor and database migration steps. ({type(exc).__name__})"
            ) from exc
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self._started = True

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.pipeline.dense is not None:
            await self.pipeline.dense.close()
        await self.database.dispose()
        self._started = False

    async def capabilities(self) -> dict[str, Any]:
        database_status = "unavailable"
        try:
            async with self.database.session() as session:
                await session.execute(text("SELECT 1"))
            database_status = "healthy"
        except SQLAlchemyError:
            database_status = "unavailable"

        qdrant_status = "not_configured"
        if self.config.qdrant_url is not None:
            qdrant_status = "unavailable"
            headers = {"api-key": self.config.qdrant_api_key} if self.config.qdrant_api_key else {}
            try:
                async with httpx.AsyncClient(
                    timeout=2.0,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    response = await client.get(
                        f"{self.config.qdrant_url.rstrip('/')}/collections",
                        headers=headers,
                    )
                if response.status_code == 200:
                    qdrant_status = "healthy"
            except httpx.HTTPError:
                pass

        has_chat = (
            self.config.local_chat_endpoint is not None
            or self.config.remote_chat_profile is not None
        )
        statuses = {
            "postgres": database_status,
            "qdrant": qdrant_status,
            "local_embedding": (
                "configured" if self.config.embedding_profile is not None else "not_configured"
            ),
            "local_chat": (
                "configured" if self.config.local_chat_endpoint is not None else "not_configured"
            ),
            "remote_chat": (
                f"configured:{self.config.remote_chat_profile.model}"
                if self.config.remote_chat_profile is not None
                else "disabled"
            ),
            "external_providers": (
                "enabled" if self.config.remote_chat_profile is not None else "disabled"
            ),
            "policy": "fixed; no trained SEGR weights loaded",
        }
        healthy = database_status == "healthy"
        fully_configured = (
            healthy
            and qdrant_status == "healthy"
            and self.config.embedding_profile is not None
            and has_chat
        )
        return {
            "status": "ready" if fully_configured else "degraded",
            "services": statuses,
            "can_answer_with_local_model": bool(
                healthy
                and self.config.local_chat_endpoint is not None
                and self.config.embedding_profile is not None
                and qdrant_status == "healthy"
            ),
            "can_answer": bool(fully_configured),
        }

    async def register_source(self, body: SourceCreate) -> SourceView:
        try:
            path = validate_source_path(body.path, self.config.allowed_roots)
        except PathSafetyError as exc:
            raise KnowledgeWeaverError(
                ErrorCode.PATH_DENIED,
                "Source path is not within an approved local source root.",
            ) from exc
        display_name = _safe_display_name(body.display_name, path.name)
        canonical = os.path.normcase(str(path))
        async with self.database.session() as session:
            rows = (
                await session.scalars(
                    select(SourceRow).where(SourceRow.corpus_id == self.config.corpus_id)
                )
            ).all()
            for row in rows:
                if os.path.normcase(str(Path(row.logical_uri).resolve(strict=False))) == canonical:
                    if row.kind != body.kind.value:
                        raise KnowledgeWeaverError(
                            ErrorCode.GENERATION_CONFLICT,
                            "This local folder is already registered with a different source type.",
                        )
                    row.display_name = display_name
                    row.license = body.license
                    await session.commit()
                    return self._source_view(row, indexed=not bool(row.deleted_at))
            source = SourceRow(
                id=str(uuid4()),
                corpus_id=self.config.corpus_id,
                kind=body.kind.value,
                logical_uri=str(path),
                display_name=display_name,
                license=body.license,
                created_at=datetime.now(UTC),
            )
            session.add(source)
            await session.commit()
            return self._source_view(source, indexed=False)

    @staticmethod
    def _source_view(source: SourceRow, *, indexed: bool) -> SourceView:
        return SourceView(
            id=source.id,
            corpus_id=source.corpus_id,
            kind=SourceKind(source.kind),
            display_name=source.display_name,
            license=source.license,
            indexed=indexed,
        )

    async def list_sources(self) -> list[SourceView]:
        async with self.database.session() as session:
            rows = (
                await session.scalars(
                    select(SourceRow)
                    .where(
                        SourceRow.corpus_id == self.config.corpus_id,
                        SourceRow.deleted_at.is_(None),
                    )
                    .order_by(SourceRow.created_at, SourceRow.id)
                )
            ).all()
            corpus = await session.get(CorpusRow, self.config.corpus_id)
            active = (
                await session.get(GenerationRow, corpus.active_generation_id)
                if corpus is not None and corpus.active_generation_id
                else None
            )
            active_sources: set[str] = set()
            if active is not None and active.source_version_ids:
                versions = (
                    await session.scalars(
                        select(SourceVersionRow).where(
                            SourceVersionRow.id.in_(active.source_version_ids)
                        )
                    )
                ).all()
                active_sources = {item.source_id for item in versions}
            return [self._source_view(row, indexed=row.id in active_sources) for row in rows]

    async def submit_ingestion(self, body: IngestionCreate) -> JobView:
        payload = {"source_id": body.source_id, "strict": body.strict}
        existing = await self._find_job("ingestion", body.idempotency_key)
        if existing is not None:
            if existing.request_hash != _canonical_hash(payload):
                raise KnowledgeWeaverError(
                    ErrorCode.GENERATION_CONFLICT,
                    "This idempotency key was already used for a different request.",
                )
            if existing.state != "failed" or not body.retry_failed:
                return self._job_view(existing, "ingestion")
        source = await self._source(body.source_id)

        async def run() -> dict[str, Any]:
            spec = Source(
                id=source.id,
                corpus_id=source.corpus_id,
                kind=SourceKind(source.kind),
                logical_uri=source.logical_uri,
                display_name=source.display_name,
                license=source.license,
                created_at=source.created_at,
            )
            request = IngestRequest(
                corpus_id=self.config.corpus_id,
                source_spec=spec,
                idempotency_key=f"b08-pipeline-ingest:{body.idempotency_key}",
                strict=body.strict,
            )
            if self.pipeline.dense is not None:
                ingest_ledger = self._new_budget(
                    max_steps=1,
                    max_tool_calls=200,
                    max_llm_calls=200,
                    max_total_tokens=500_000,
                    timeout_seconds=1800,
                )
                provider = self._new_embedding_provider(ingest_ledger)
                if provider is None:
                    raise ProviderUnavailable(
                        "Dense indexing has no local embedding provider.",
                        category="configuration_missing",
                    )
                self.pipeline.dense.provider = provider
            report = await self.pipeline.ingest(request)
            return {"report": report.model_dump(mode="json")}

        return await self._submit_job(
            operation="ingestion",
            idempotency_key=body.idempotency_key,
            request_payload=payload,
            retry_failed=body.retry_failed,
            handler=run,
        )

    async def submit_delete(self, source_id: str, body: DeleteSource) -> JobView:
        payload = {"source_id": source_id}
        existing = await self._find_job("delete", body.idempotency_key)
        if existing is not None:
            if existing.request_hash != _canonical_hash(payload):
                raise KnowledgeWeaverError(
                    ErrorCode.GENERATION_CONFLICT,
                    "This idempotency key was already used for a different request.",
                )
            if existing.state != "failed" or not body.retry_failed:
                return self._job_view(existing, "delete")
        await self._source(source_id)

        async def run() -> dict[str, Any]:
            if self.pipeline.dense is not None:
                provider = self._new_embedding_provider(
                    self._new_budget(
                        max_steps=1,
                        max_tool_calls=200,
                        max_llm_calls=200,
                        max_total_tokens=500_000,
                        timeout_seconds=1800,
                    )
                )
                if provider is None:
                    raise ProviderUnavailable(
                        "Dense indexing has no local embedding provider.",
                        category="configuration_missing",
                    )
                self.pipeline.dense.provider = provider
            request = DeleteSourceRequest(
                corpus_id=self.config.corpus_id,
                source_id=source_id,
                idempotency_key=f"b08-pipeline-delete:{body.idempotency_key}",
            )
            report = await self.pipeline.delete_source(request)
            return {"report": report.model_dump(mode="json")}

        return await self._submit_job(
            operation="delete",
            idempotency_key=body.idempotency_key,
            request_payload=payload,
            retry_failed=body.retry_failed,
            handler=run,
        )

    async def _source(self, source_id: str) -> SourceRow:
        async with self.database.session() as session:
            source = await session.get(SourceRow, source_id)
            if source is None or source.corpus_id != self.config.corpus_id:
                raise KnowledgeWeaverError(
                    ErrorCode.MISSING_REFERENCE,
                    "Source does not exist in the local corpus.",
                )
            if source.deleted_at is not None:
                raise KnowledgeWeaverError(
                    ErrorCode.MISSING_REFERENCE,
                    "Source has been logically deleted.",
                )
            return cast(SourceRow, source)

    @staticmethod
    def _operation_for_key(key: str) -> str:
        parts = key.split(":", 2)
        return parts[1] if len(parts) > 1 else "unknown"

    def _job_id(self, operation: str, idempotency_key: str) -> tuple[str, str]:
        full_key = f"{_APP_JOB_PREFIX}{operation}:{idempotency_key}"
        return deterministic_id("job", {"corpus_id": self.config.corpus_id, "idempotency_key": full_key}), full_key

    async def _find_job(self, operation: str, idempotency_key: str) -> JobRow | None:
        job_id, _ = self._job_id(operation, idempotency_key)
        async with self.database.session() as session:
            return cast(JobRow | None, await session.get(JobRow, job_id))

    async def _submit_job(
        self,
        *,
        operation: str,
        idempotency_key: str,
        request_payload: dict[str, Any],
        stored_request_payload: dict[str, Any] | None = None,
        retry_failed: bool,
        handler: Callable[[], Awaitable[dict[str, Any]]],
    ) -> JobView:
        job_id, full_key = self._job_id(operation, idempotency_key)
        request_hash = _canonical_hash(request_payload)
        schedule = False
        async with self._job_lock:
            async with self.database.session() as session:
                row = await session.get(JobRow, job_id)
                if row is not None:
                    if row.request_hash != request_hash:
                        raise KnowledgeWeaverError(
                            ErrorCode.GENERATION_CONFLICT,
                            "This idempotency key was already used for a different request.",
                        )
                    if row.state == "failed" and retry_failed:
                        row.state = "running"
                        row.result = {
                            "operation": operation,
                            "request": stored_request_payload or request_payload,
                        }
                        await session.commit()
                        schedule = True
                    else:
                        return self._job_view(row, operation)
                else:
                    row = JobRow(
                        id=job_id,
                        corpus_id=self.config.corpus_id,
                        idempotency_key=full_key,
                        request_hash=request_hash,
                        state="running",
                        result={
                            "operation": operation,
                            "request": stored_request_payload or request_payload,
                        },
                    )
                    session.add(row)
                    await session.commit()
                    schedule = True
            if schedule:
                task = asyncio.create_task(self._run_job(job_id, operation, handler))
                self._tasks[job_id] = task
                task.add_done_callback(lambda _: self._tasks.pop(job_id, None))
        return JobView(job_id=job_id, operation=operation, state="running")

    @staticmethod
    def _job_view(row: JobRow, operation: str | None = None) -> JobView:
        result = row.result or {}
        public_result = {key: value for key, value in result.items() if key != "request"}
        return JobView(
            job_id=row.id,
            operation=operation or LocalProduct._operation_for_key(row.idempotency_key),
            state=row.state,
            result=public_result or None,
        )

    async def _run_job(
        self,
        job_id: str,
        operation: str,
        handler: Callable[[], Awaitable[dict[str, Any]]],
    ) -> None:
        async with self._job_slots:
            try:
                output = await handler()
                result: dict[str, Any] = {"operation": operation, **output}
                state = "succeeded"
            except asyncio.CancelledError:
                raise
            except KnowledgeWeaverError as exc:
                result = {"operation": operation, "error": exc.to_dict()}
                state = "failed"
            except Exception:  # noqa: BLE001 - background failures must leave a sanitized PG result.
                result = {
                    "operation": operation,
                    "error": {
                        "code": ErrorCode.INTERNAL_ERROR.value,
                        "message": "The local job failed. Run doctor for service status.",
                        "retryable": False,
                    },
                }
                state = "failed"
            async with self.database.session() as session:
                row = await session.get(JobRow, job_id)
                if row is not None:
                    old_result = row.result or {}
                    row.result = {**old_result, **result}
                    row.state = state
                    await session.commit()

    async def get_job(self, job_id: str) -> JobView:
        async with self.database.session() as session:
            row = await session.get(JobRow, job_id)
            if row is None or not row.idempotency_key.startswith(_APP_JOB_PREFIX):
                raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "Local job does not exist.")
            return self._job_view(row)

    async def submit_investigation(self, body: InvestigationCreate) -> JobView:
        request_hash = _canonical_hash(_request_hash_payload(body))
        existing = await self._find_job("investigation", body.idempotency_key)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise KnowledgeWeaverError(
                    ErrorCode.GENERATION_CONFLICT,
                    "This idempotency key was already used for a different investigation.",
                )
            if existing.state != "failed" or not body.retry_failed:
                return self._job_view(existing, "investigation")
            stored = (existing.result or {}).get("request")
            if not isinstance(stored, dict):
                raise KnowledgeWeaverError(
                    ErrorCode.INTERNAL_ERROR,
                    "The failed investigation has no retry record.",
                )
            original = InvestigationRequest.model_validate(stored)
            context = await self._refresh_context(
                original.context.generation_id,
                original.context.source_ids,
                body.timeout_seconds,
            )
            retry_request = original.model_copy(
                update={
                    "context": context,
                    "query": original.query.model_copy(update={"context": context}),
                    "budget": original.budget.model_copy(update={"deadline_utc": context.deadline_utc}),
                }
            )
            handler = lambda: self._run_investigation(retry_request)
            return await self._submit_job(
                operation="investigation",
                idempotency_key=body.idempotency_key,
                request_payload=_request_hash_payload(body),
                stored_request_payload={"request": retry_request.model_dump(mode="json")},
                retry_failed=True,
                handler=handler,
            )

        context = await self.resolve_context(
            body.corpus_id,
            body.generation_id,
            body.source_ids,
            str(uuid4()),
            timeout_seconds=body.timeout_seconds,
        )
        query = Query(
            id=str(uuid4()),
            text=body.query,
            context=context,
            requested_sources=body.source_ids or [],
            subquestions=[
                SubQuestion(
                    id=str(uuid4()),
                    text=body.query,
                )
            ],
        )
        budget = Budget(
            deadline_utc=context.deadline_utc,
            max_steps=body.max_steps,
            max_tool_calls=body.max_tool_calls,
            max_llm_calls=body.max_llm_calls,
            max_total_tokens=body.max_total_tokens,
            max_graph_edges=body.max_graph_edges,
        )
        request = InvestigationRequest(
            query=query,
            context=context,
            budget=budget,
            policy=PolicyKind(body.policy),
            language=Language.en if query.text.isascii() else Language.zh,
            features={
                "enable_expansion": False,
                "enable_reranker": False,
                "enable_sufficiency_stop": True,
                "enable_dynamic_stop": True,
            },
        )
        handler = lambda: self._run_investigation(request)
        try:
            return await self._submit_job(
                operation="investigation",
                idempotency_key=body.idempotency_key,
                request_payload=_request_hash_payload(body),
                stored_request_payload={"request": request.model_dump(mode="json")},
                retry_failed=body.retry_failed,
                handler=handler,
            )
        except Exception:
            await self.store.release_lease(context.request_id)
            raise

    async def _run_investigation(self, request: InvestigationRequest) -> dict[str, Any]:
        try:
            async with self._dense_lock:
                answer: Answer = await self.agent.investigate(request)
            trace = self.agent.get_trace(answer.investigation_id)
            async with self.database.session() as session:
                query_payload = request.query.model_dump(mode="json")
                existing_query = await session.get(QueryRow, request.query.id)
                if existing_query is None:
                    session.add(
                        QueryRow(
                            id=request.query.id,
                            corpus_id=request.context.corpus_id,
                            payload=query_payload,
                        )
                    )
                else:
                    existing_query.payload = query_payload
                existing_investigation = await session.get(
                    InvestigationRow, answer.investigation_id
                )
                investigation_payload = {
                    "query_id": request.query.id,
                    "query": request.query.text,
                    "context": request.context.model_dump(mode="json"),
                    "status": answer.status.value,
                    "answer_id": answer.id,
                    "trace": trace,
                }
                if existing_investigation is None:
                    session.add(
                        InvestigationRow(
                            id=answer.investigation_id,
                            corpus_id=request.context.corpus_id,
                            payload=investigation_payload,
                        )
                    )
                else:
                    existing_investigation.payload = investigation_payload
                if await session.get(AnswerRow, answer.id) is None:
                    session.add(
                        AnswerRow(
                            id=answer.id,
                            investigation_id=answer.investigation_id,
                            payload=answer.model_dump(mode="json"),
                        )
                    )
                await session.commit()
            return {
                "investigation_id": answer.investigation_id,
                "status": answer.status.value,
                "answer": answer.model_dump(mode="json"),
                "trace": trace,
                "policy_profile": request.policy.value,
                "training": "none; fixed/rule policy only",
            }
        finally:
            await self.store.release_lease(request.context.request_id)

    async def get_investigation(self, run_id: str) -> dict[str, Any]:
        job = await self.get_job(run_id)
        if job.operation != "investigation":
            raise KnowledgeWeaverError(ErrorCode.MISSING_REFERENCE, "Investigation run does not exist.")
        result = job.result or {}
        response: dict[str, Any] = {"run_id": run_id, "state": job.state}
        for field in ("investigation_id", "status", "answer", "trace", "error", "policy_profile", "training"):
            if field in result:
                response[field] = result[field]
        return response

    async def resolve_context(
        self,
        corpus_id: str | None,
        generation_id: str | None,
        source_ids: list[str] | None,
        request_id: str,
        *,
        timeout_seconds: int = 120,
    ) -> QueryContext:
        actual_corpus = corpus_id or self.config.corpus_id
        if actual_corpus != self.config.corpus_id:
            raise KnowledgeWeaverError(
                ErrorCode.MISSING_REFERENCE,
                "The requested corpus is not available in this local product.",
            )
        if source_ids:
            async with self.database.session() as session:
                found = (
                    await session.scalars(
                        select(SourceRow.id).where(
                            SourceRow.id.in_(source_ids),
                            SourceRow.corpus_id == actual_corpus,
                            SourceRow.deleted_at.is_(None),
                        )
                    )
                ).all()
            if set(found) != set(source_ids):
                raise KnowledgeWeaverError(
                    ErrorCode.MISSING_REFERENCE,
                    "One or more source filters are not available in this corpus.",
                )

        deadline = datetime.now(UTC) + timedelta(seconds=timeout_seconds)
        if generation_id is None:
            active = await self.store.resolve_context(actual_corpus, request_id)
            context = active.model_copy(
                update={
                    "deadline_utc": deadline,
                    "source_ids": source_ids,
                }
            )
        else:
            generation = await self.store.generation(generation_id)
            if generation.corpus_id != actual_corpus or generation.state not in {"READY", "RETIRED"}:
                raise KnowledgeWeaverError(
                    ErrorCode.GENERATION_CONFLICT,
                    "Requested generation is not available in this corpus.",
                )
            context = QueryContext(
                corpus_id=actual_corpus,
                generation_id=generation_id,
                request_id=request_id,
                deadline_utc=deadline,
                source_ids=source_ids,
            )
        async with self.database.session() as session:
            await session.merge(
                QueryLeaseRow(
                    request_id=request_id,
                    generation_id=context.generation_id,
                    expires_at=deadline + timedelta(seconds=30),
                )
            )
            await session.commit()
        return context

    async def _refresh_context(
        self,
        generation_id: str,
        source_ids: list[str] | None,
        timeout_seconds: int,
    ) -> QueryContext:
        return await self.resolve_context(
            self.config.corpus_id,
            generation_id,
            source_ids,
            str(uuid4()),
            timeout_seconds=timeout_seconds,
        )

    async def retrieve(self, body: RetrievalCreate) -> dict[str, Any]:
        request_id = str(uuid4())
        context = await self.resolve_context(
            body.corpus_id,
            body.generation_id,
            body.source_ids,
            request_id,
        )
        try:
            query = RetrievalQuery(
                text=body.query,
                context=context,
                top_k=body.top_k,
                graph_options=body.graph_options or GraphOptions(),
            )
            ledger = self._new_budget(max_tool_calls=30, max_total_tokens=40_000)
            async with self._dense_lock:
                retrievers = self._agent_retrievers(ledger)
                route = retrievers[body.channel]
                result: RetrievalResult = await route.retrieve(query)
            return {
                "channel": body.channel,
                "generation_id": context.generation_id,
                "result": result.model_dump(mode="json"),
            }
        finally:
            await self.store.release_lease(context.request_id)

    async def evidence(
        self,
        evidence_id: str,
        corpus_id: str | None,
        generation_id: str,
    ) -> dict[str, Any]:
        request_id = str(uuid4())
        context = await self.resolve_context(
            corpus_id,
            generation_id,
            None,
            request_id,
            timeout_seconds=30,
        )
        try:
            evidence_list = await self.store.get_evidence([evidence_id], context)
            evidence = evidence_list[0]
            chunks = await self.store.get_chunks([evidence.chunk_id], context)
            locator = evidence.locator.model_dump(mode="json")
            source_id = chunks[0].document_id
            async with self.database.session() as session:
                # The source name is resolved through the chunk's document, never by returning its path.
                document = await session.get(DocumentRow, source_id)
                source = await session.get(SourceRow, document.source_id) if document else None
            return {
                "evidence_id": evidence.id,
                "generation_id": context.generation_id,
                "source_id": source.id if source else None,
                "source_name": source.display_name if source else "Local source",
                "relative_path": locator.get("path"),
                "locator": locator,
                "quote": evidence.quote,
                "chunk_id": evidence.chunk_id,
            }
        finally:
            await self.store.release_lease(context.request_id)

    async def graph(self, body: RetrievalCreate) -> dict[str, Any]:
        graph_body = body.model_copy(update={"channel": "graph"})
        result = await self.retrieve(graph_body)
        return result

    @staticmethod
    def experiments() -> list[ExperimentEvidence]:
        return _internal_evidence()
