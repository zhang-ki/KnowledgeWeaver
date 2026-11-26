from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from knowledge_weaver.core.errors import ErrorCode, ErrorEnvelope, KnowledgeWeaverError
from knowledge_weaver.models import GraphOptions

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
from .runtime import LocalProduct


def install_windows_selector_policy() -> None:
    """Select psycopg's supported event loop before Uvicorn creates one on Windows."""
    if os.name == "nt":
        policy_type = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
        if policy_type is not None and not isinstance(
            asyncio.get_event_loop_policy(), policy_type
        ):
            asyncio.set_event_loop_policy(policy_type())


# This runs during module import, before Uvicorn creates its asyncio loop.
install_windows_selector_policy()


def _status_for(code: ErrorCode) -> int:
    if code is ErrorCode.INVALID_INPUT:
        return status.HTTP_400_BAD_REQUEST
    if code is ErrorCode.PATH_DENIED:
        return status.HTTP_403_FORBIDDEN
    if code is ErrorCode.MISSING_REFERENCE:
        return status.HTTP_404_NOT_FOUND
    if code in {ErrorCode.GENERATION_CONFLICT, ErrorCode.NO_READY_GENERATION, ErrorCode.INDEX_NOT_READY}:
        return status.HTTP_409_CONFLICT
    if code in {ErrorCode.UNSUPPORTED_FORMAT, ErrorCode.PARSE_FAILED, ErrorCode.SCHEMA_INVALID, ErrorCode.LIMIT_EXCEEDED}:
        return status.HTTP_422_UNPROCESSABLE_ENTITY
    if code in {ErrorCode.RATE_LIMITED, ErrorCode.BUDGET_EXHAUSTED}:
        return status.HTTP_429_TOO_MANY_REQUESTS
    if code is ErrorCode.DEADLINE_EXCEEDED:
        return status.HTTP_504_GATEWAY_TIMEOUT
    if code is ErrorCode.PROVIDER_UNAVAILABLE:
        return status.HTTP_503_SERVICE_UNAVAILABLE
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def create_app(product: LocalProduct | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        active_product = product or LocalProduct.from_environment()
        await active_product.start()
        application.state.product = active_product
        try:
            yield
        finally:
            await active_product.close()

    application = FastAPI(
        title="KnowledgeWeaver Local",
        version="1",
        description="Loopback-only API for source indexing and evidence-grounded investigation.",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def request_id_middleware(request: Request, call_next: Any) -> Response:
        request_id = str(uuid4())
        request.state.request_id = request_id
        response = cast(Response, await call_next(request))
        response.headers["X-Request-ID"] = request_id
        return response

    @application.exception_handler(KnowledgeWeaverError)
    async def knowledge_error_handler(
        request: Request,
        exc: KnowledgeWeaverError,
    ) -> JSONResponse:
        error = exc.envelope
        if error.request_id is None:
            error = error.model_copy(update={"request_id": request.state.request_id})
        return JSONResponse(status_code=_status_for(error.code), content=error.model_dump(mode="json"))

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        locations = [
            ".".join(str(part) for part in error.get("loc", ()) if part != "body")
            for error in exc.errors()
        ]
        envelope = ErrorEnvelope(
            code=ErrorCode.SCHEMA_INVALID,
            message="Request fields do not match the API contract.",
            request_id=request.state.request_id,
            retryable=False,
            details={"fields": locations},
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=envelope.model_dump(mode="json"),
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        code = ErrorCode.MISSING_REFERENCE if exc.status_code == 404 else ErrorCode.INVALID_INPUT
        envelope = ErrorEnvelope(
            code=code,
            message="The requested API route or resource is not available.",
            request_id=request.state.request_id,
            retryable=False,
        )
        return JSONResponse(status_code=exc.status_code, content=envelope.model_dump(mode="json"))

    @application.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        envelope = ErrorEnvelope(
            code=ErrorCode.INTERNAL_ERROR,
            message="The request could not be completed. Check local service status with doctor.",
            request_id=request.state.request_id,
            retryable=False,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=envelope.model_dump(mode="json"),
        )

    def get_product(request: Request) -> LocalProduct:
        active_product = getattr(request.app.state, "product", None)
        if active_product is None:
            raise KnowledgeWeaverError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Local services are not initialized.",
                details={"category": "not_ready"},
            )
        return cast(LocalProduct, active_product)

    @application.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/readyz")
    async def readiness(request: Request) -> JSONResponse:
        active_product = get_product(request)
        capabilities = await active_product.capabilities()
        http_status = (
            status.HTTP_200_OK
            if capabilities["services"]["postgres"] == "healthy"
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        return JSONResponse(status_code=http_status, content=capabilities)

    @application.post("/v1/sources", response_model=SourceView, status_code=status.HTTP_201_CREATED)
    async def create_source(body: SourceCreate, request: Request) -> SourceView:
        return await get_product(request).register_source(body)

    @application.get("/v1/sources", response_model=list[SourceView])
    async def list_sources(request: Request) -> list[SourceView]:
        return await get_product(request).list_sources()

    @application.post("/v1/ingestions", response_model=JobView, status_code=status.HTTP_202_ACCEPTED)
    async def ingest(body: IngestionCreate, request: Request) -> JobView:
        return await get_product(request).submit_ingestion(body)

    @application.get("/v1/jobs/{job_id}", response_model=JobView)
    async def job(job_id: str, request: Request) -> JobView:
        return await get_product(request).get_job(job_id)

    @application.delete("/v1/sources/{source_id}", response_model=JobView, status_code=status.HTTP_202_ACCEPTED)
    async def delete_source(
        source_id: str,
        request: Request,
        body: DeleteSource,
    ) -> JobView:
        return await get_product(request).submit_delete(source_id, body)

    @application.post("/v1/retrievals")
    async def retrieve(body: RetrievalCreate, request: Request) -> dict[str, Any]:
        return await get_product(request).retrieve(body)

    @application.post("/v1/investigations", status_code=status.HTTP_202_ACCEPTED)
    async def investigate(body: InvestigationCreate, request: Request) -> dict[str, str]:
        result = await get_product(request).submit_investigation(body)
        return {"run_id": result.job_id, "state": result.state}

    @application.get("/v1/investigations/{run_id}")
    async def investigation(run_id: str, request: Request) -> dict[str, Any]:
        return await get_product(request).get_investigation(run_id)

    @application.get("/v1/evidence/{evidence_id}")
    async def evidence(
        evidence_id: str,
        request: Request,
        generation_id: str = Query(min_length=36, max_length=36),
        corpus_id: str | None = None,
    ) -> dict[str, Any]:
        return await get_product(request).evidence(evidence_id, corpus_id, generation_id)

    @application.get("/v1/graphs")
    async def graph(
        request: Request,
        query: str = Query(min_length=1, max_length=20_000),
        corpus_id: str | None = None,
        generation_id: str | None = None,
        source_ids: list[str] | None = Query(default=None),  # noqa: B008
        max_hops: int = Query(default=2, ge=1, le=3),
        max_edges: int = Query(default=200, ge=1, le=200),
    ) -> dict[str, Any]:
        body = RetrievalCreate(
            query=query,
            corpus_id=corpus_id,
            generation_id=generation_id,
            source_ids=source_ids,
            channel="graph",
            top_k=20,
            graph_options=GraphOptions(max_hops=max_hops, max_edges=max_edges),
        )
        return await get_product(request).graph(body)

    @application.get("/v1/experiments", response_model=list[ExperimentEvidence])
    async def experiments(request: Request) -> list[ExperimentEvidence]:
        return get_product(request).experiments()

    return application


app = create_app()
