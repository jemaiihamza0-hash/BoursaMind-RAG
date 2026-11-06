"""Versioned HTTP API for BoursaMind-RAG."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, File, Query, Response, UploadFile
from fastapi import status as http_status
from sqlalchemy import case, func, select

from app.auth.dependencies import (
    AdminDep,
    DbSessionDep,
    QueryPrincipalDep,
    UsagePrincipalDep,
    WritePrincipalDep,
)
from app.citations.service import CitationService
from app.config import get_settings
from app.documents.service import DocumentService
from app.embeddings.providers import get_embedding_provider
from app.evaluation.service import EvaluationService
from app.external_sources.api import router as external_sources_router
from app.financial.api import router as financial_router
from app.models import (
    Collection,
    Document,
    DocumentChunk,
    DocumentStatus,
    IngestionJob,
    JobStatus,
    Tenant,
    UsageEvent,
    Workspace,
)
from app.observability import log_event, metrics
from app.parsers.uploads import read_upload_text
from app.retrieval.service import QueryService
from app.schemas import (
    CollectionCreate,
    CollectionOut,
    DiagnosticsOut,
    DocumentCreate,
    DocumentCreatedOut,
    DocumentDeleteOut,
    DocumentOut,
    DocumentVisibilityChange,
    DocumentVisibilityOut,
    EvaluationReport,
    HealthOut,
    IndexRequest,
    IndexRunOut,
    OperationalActivityOut,
    OperationalJobOut,
    OperationalJobRetryOut,
    OperationalJobsPageOut,
    QueryRequest,
    QueryResponse,
    SourceOut,
    StatusOut,
    TenantCreate,
    TenantCreatedOut,
    UsageOut,
    WorkspaceCreate,
    WorkspaceOut,
)
from app.tenants.service import TenantService
from app.usage.service import UsageService

router = APIRouter(prefix="/v1")
router.include_router(financial_router)
router.include_router(external_sources_router)
logger = logging.getLogger(__name__)
_last_database_status: str | None = None


@router.get("/metrics")
def process_metrics(_admin: AdminDep) -> dict:
    """Internal metrics, protected by the existing RAG administrator key."""
    return metrics.snapshot()


def _operational_job(job: IngestionJob) -> OperationalJobOut:
    return OperationalJobOut(
        id=job.id,
        document_id=job.document_id,
        retry_of_job_id=job.retry_of_job_id,
        attempt=job.attempt,
        status=job.status,
        chunks_created=job.chunks_created,
        embedding_tokens=job.embedding_tokens,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        error_code="indexing_failed" if job.status == JobStatus.FAILED else None,
    )


def _health(session: DbSessionDep) -> HealthOut:
    global _last_database_status
    checks = {"service": "ok", "database": "ok"}
    try:
        session.execute(select(func.count(Tenant.id))).scalar_one()
    except Exception:  # noqa: BLE001
        checks["database"] = "error"
        metrics.increment("dependency_failures_total", dependency="postgresql", result="error")
    if checks["database"] != _last_database_status:
        log_event(
            logger,
            logging.INFO if checks["database"] == "ok" else logging.ERROR,
            "dependency_status_changed",
            dependency="postgresql",
            previous=_last_database_status or "unknown",
            status=checks["database"],
        )
        _last_database_status = checks["database"]
    status = "ok" if all(value == "ok" for value in checks.values()) else "degraded"
    return HealthOut(status=status, checks=checks, version=get_settings().app_version)


@router.get("/health", response_model=HealthOut)
def health(session: DbSessionDep) -> HealthOut:
    """Compatibility health report; readiness consumers should use /ready."""
    return _health(session)


@router.get("/health/live", response_model=HealthOut)
def health_live() -> HealthOut:
    return HealthOut(
        status="ok",
        checks={"service": "ok"},
        version=get_settings().app_version,
    )


@router.get("/health/ready", response_model=HealthOut)
def health_ready(response: Response, session: DbSessionDep) -> HealthOut:
    result = _health(session)
    if result.status != "ok":
        response.status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE
    return result


@router.get("/status", response_model=StatusOut)
def status(_principal: UsagePrincipalDep, session: DbSessionDep) -> StatusOut:
    settings = get_settings()
    tenant_id = _principal.tenant.id
    jobs = {
        status_value: int(
            session.execute(
                select(func.count(IngestionJob.id)).where(
                    IngestionJob.tenant_id == tenant_id,
                    IngestionJob.status == status_value,
                )
            ).scalar_one()
        )
        for status_value in (
            JobStatus.QUEUED,
            JobStatus.RUNNING,
            JobStatus.SUCCESS,
            JobStatus.PARTIAL,
            JobStatus.FAILED,
        )
    }
    return StatusOut(
        service=settings.app_name,
        database="ok",
        vector_backend=settings.vector_backend,
        embedding_model=get_embedding_provider().model,
        llm_provider=settings.llm_provider,
        tenants=1,
        workspaces=int(
            session.execute(
                select(func.count(Workspace.id)).where(Workspace.tenant_id == tenant_id)
            ).scalar_one()
        ),
        collections=int(
            session.execute(
                select(func.count(Collection.id)).where(Collection.tenant_id == tenant_id)
            ).scalar_one()
        ),
        documents=int(
            session.execute(
                select(func.count(Document.id)).where(Document.tenant_id == tenant_id)
            ).scalar_one()
        ),
        chunks=int(
            session.execute(
                select(func.count(DocumentChunk.id)).where(DocumentChunk.tenant_id == tenant_id)
            ).scalar_one()
        ),
        jobs=jobs,
    )


@router.get("/diagnostics", response_model=DiagnosticsOut)
def diagnostics(principal: UsagePrincipalDep, session: DbSessionDep) -> DiagnosticsOut:
    """Return safe operational telemetry for the authenticated tenant.

    Raw ingestion errors and document contents are intentionally excluded: an
    administration console needs actionable state, not provider responses that
    may contain request data or credentials.
    """
    tenant_id = principal.tenant.id
    since = datetime.now(UTC) - timedelta(hours=24)
    activity = list(
        session.execute(
            select(UsageEvent)
            .where(UsageEvent.tenant_id == tenant_id)
            .order_by(UsageEvent.created_at.desc())
            .limit(20)
        ).scalars()
    )
    metrics = session.execute(
        select(
            func.count(UsageEvent.id),
            func.sum(case((UsageEvent.status_code < 400, 1), else_=0)),
            func.avg(UsageEvent.latency_ms),
            func.max(UsageEvent.created_at),
        ).where(
            UsageEvent.tenant_id == tenant_id,
            UsageEvent.created_at >= since,
        )
    ).one()
    jobs = list(
        session.execute(
            select(IngestionJob)
            .where(IngestionJob.tenant_id == tenant_id)
            .order_by(IngestionJob.created_at.desc())
            .limit(20)
        ).scalars()
    )

    request_count = int(metrics[0] or 0)
    successful = int(metrics[1] or 0)
    failed = request_count - successful
    average_latency = round(float(metrics[2])) if metrics[2] is not None else None
    last_request_at = metrics[3]
    last_index_attempt_at = session.execute(
        select(func.max(IngestionJob.created_at)).where(IngestionJob.tenant_id == tenant_id)
    ).scalar_one_or_none()
    last_successful_index_at = session.execute(
        select(func.max(IngestionJob.finished_at)).where(
            IngestionJob.tenant_id == tenant_id,
            IngestionJob.status == JobStatus.SUCCESS,
        )
    ).scalar_one_or_none()
    if last_successful_index_at is None:
        last_successful_index_at = session.execute(
            select(func.max(IngestionJob.created_at)).where(
                IngestionJob.tenant_id == tenant_id,
                IngestionJob.status == JobStatus.SUCCESS,
            )
        ).scalar_one_or_none()

    return DiagnosticsOut(
        generated_at=datetime.now(UTC),
        requests_24h=request_count,
        successful_requests_24h=successful,
        failed_requests_24h=failed,
        success_rate_24h=round(successful / request_count * 100, 1) if request_count else None,
        average_latency_ms_24h=average_latency,
        last_request_at=last_request_at,
        last_index_attempt_at=last_index_attempt_at,
        last_successful_index_at=last_successful_index_at,
        pending_documents=int(
            session.execute(
                select(func.count(Document.id)).where(
                    Document.tenant_id == tenant_id,
                    Document.status == DocumentStatus.PENDING,
                )
            ).scalar_one()
        ),
        failed_documents=int(
            session.execute(
                select(func.count(Document.id)).where(
                    Document.tenant_id == tenant_id,
                    Document.status == DocumentStatus.FAILED,
                )
            ).scalar_one()
        ),
        recent_activity=[
            OperationalActivityOut(
                request_id=row.request_id,
                operation=row.operation,
                status_code=row.status_code,
                latency_ms=row.latency_ms,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                embedding_tokens=row.embedding_tokens,
                chunks_retrieved=row.chunks_retrieved,
                provider=row.provider,
                error_code=row.error_code,
                created_at=row.created_at,
            )
            for row in activity
        ],
        recent_jobs=[_operational_job(row) for row in jobs],
    )


@router.post("/tenants", response_model=TenantCreatedOut, status_code=201)
def create_tenant(_admin: AdminDep, payload: TenantCreate, session: DbSessionDep):
    return TenantService(session).create_tenant(payload)


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(principal: QueryPrincipalDep, session: DbSessionDep):
    return TenantService(session).list_workspaces(principal.tenant)


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
def create_workspace(payload: WorkspaceCreate, principal: WritePrincipalDep, session: DbSessionDep):
    return TenantService(session).create_workspace(principal.tenant, payload)


@router.get("/collections", response_model=list[CollectionOut])
def list_collections(
    principal: QueryPrincipalDep,
    session: DbSessionDep,
    workspace_id: int | None = Query(default=None),
):
    return TenantService(session).list_collections(principal.tenant, workspace_id)


@router.post("/collections", response_model=CollectionOut, status_code=201)
def create_collection(
    payload: CollectionCreate, principal: WritePrincipalDep, session: DbSessionDep
):
    return TenantService(session).create_collection(principal.tenant, payload)


@router.post("/documents", response_model=DocumentCreatedOut, status_code=201)
def create_document(payload: DocumentCreate, principal: WritePrincipalDep, session: DbSessionDep):
    return DocumentService(session).create(principal, payload)


@router.post("/documents/upload", response_model=DocumentCreatedOut, status_code=201)
async def upload_document(
    principal: WritePrincipalDep,
    session: DbSessionDep,
    file: Annotated[UploadFile, File()],
    workspace_id: int | None = Query(default=None),
    collection_id: int | None = Query(default=None),
    title: str | None = Query(default=None),
    document_type: str = Query(default="DOCUMENT"),
    index_now: bool = Query(default=True),
):
    text, filename = await read_upload_text(file)
    payload = DocumentCreate(
        workspace_id=workspace_id,
        collection_id=collection_id,
        title=title or filename,
        text=text,
        source_url=None,
        document_type=document_type,
        metadata={"uploaded_filename": filename},
        index_now=index_now,
    )
    return DocumentService(session).create(principal, payload)


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(
    principal: QueryPrincipalDep,
    session: DbSessionDep,
    workspace_id: int | None = Query(default=None),
    collection_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    return DocumentService(session).list_documents(
        principal,
        workspace_id=workspace_id,
        collection_id=collection_id,
        limit=limit,
    )


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, principal: QueryPrincipalDep, session: DbSessionDep):
    return DocumentService(session).get_document(principal, document_id)


@router.delete("/documents", response_model=DocumentDeleteOut)
def delete_document(
    principal: WritePrincipalDep,
    session: DbSessionDep,
    external_id: str = Query(min_length=1, max_length=255),
):
    return DocumentService(session).delete_by_external_id(principal, external_id)


@router.post("/documents/visibility", response_model=DocumentVisibilityOut)
def reconcile_document_visibility(
    payload: DocumentVisibilityChange,
    principal: WritePrincipalDep,
    session: DbSessionDep,
):
    return DocumentService(session).reconcile_visibility(principal, payload)


@router.get("/jobs", response_model=OperationalJobsPageOut)
def list_jobs(
    principal: UsagePrincipalDep,
    session: DbSessionDep,
    status: str | None = Query(default=None),
    document_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=500),
):
    jobs, total = DocumentService(session).list_jobs(
        principal,
        status=status,
        document_id=document_id,
        limit=limit,
        offset=offset,
    )
    return OperationalJobsPageOut(
        items=[_operational_job(job) for job in jobs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/jobs/{job_id}", response_model=OperationalJobOut)
def get_job(job_id: int, principal: UsagePrincipalDep, session: DbSessionDep):
    return _operational_job(DocumentService(session).get_job(principal, job_id))


@router.post("/jobs/{job_id}/retry", response_model=OperationalJobRetryOut)
def retry_job(job_id: int, principal: WritePrincipalDep, session: DbSessionDep):
    job, idempotent = DocumentService(session).retry_job(principal, job_id)
    return OperationalJobRetryOut(
        original_job_id=job_id,
        job=_operational_job(job),
        idempotent=idempotent,
    )


@router.post("/index", response_model=IndexRunOut)
def index(payload: IndexRequest, principal: WritePrincipalDep, session: DbSessionDep):
    return DocumentService(session).index_pending(principal, payload)


@router.post("/query", response_model=QueryResponse)
def query(payload: QueryRequest, principal: QueryPrincipalDep, session: DbSessionDep):
    return QueryService(session).query(principal, payload)


@router.get("/sources/{chunk_id}", response_model=SourceOut)
def source(
    chunk_id: int,
    principal: QueryPrincipalDep,
    session: DbSessionDep,
    visibility_owner: str | None = Query(default=None, min_length=1, max_length=180),
):
    return CitationService(session).get_source(
        principal, chunk_id, visibility_owner=visibility_owner
    )


@router.get("/usage", response_model=UsageOut)
def usage(principal: UsagePrincipalDep, session: DbSessionDep, days: int = Query(30, ge=1, le=365)):
    return UsageService(session).report(principal, days=days)


@router.post("/evaluation/run", response_model=EvaluationReport)
def run_evaluation(
    principal: QueryPrincipalDep,
    session: DbSessionDep,
    dataset: str = Query(default="generic", pattern=r"^[a-zA-Z0-9_-]+$"),
):
    return EvaluationService(session).run(principal, dataset)
