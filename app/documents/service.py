"""Document ingestion and indexing service."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.security import Principal
from app.chunking.chunker import chunk_text, split_pages
from app.config import get_settings
from app.documents import mappers
from app.embeddings.providers import get_embedding_provider
from app.models import (
    Document,
    DocumentChunk,
    DocumentStatus,
    DocumentVisibilityGrant,
    IngestionJob,
    JobStatus,
    Tenant,
    VisibilityOperation,
)
from app.observability import log_event, metrics
from app.schemas import (
    DocumentCreate,
    DocumentCreatedOut,
    DocumentDeleteOut,
    DocumentVisibilityChange,
    DocumentVisibilityOut,
    IndexRequest,
    IndexRunOut,
)
from app.tenants.service import TenantService

logger = logging.getLogger(__name__)


def content_hash(text: str) -> str:
    return sha256((text or "").encode("utf-8")).hexdigest()


class DocumentService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.settings = get_settings()

    def create(self, principal: Principal, payload: DocumentCreate) -> DocumentCreatedOut:
        tenant = self.session.execute(
            select(Tenant).where(Tenant.id == principal.tenant.id).with_for_update()
        ).scalar_one()
        workspace, collection = TenantService(self.session).get_collection(
            principal.tenant,
            workspace_id=payload.workspace_id,
            collection_id=payload.collection_id,
        )
        document = None
        if payload.external_id:
            document = (
                self.session.execute(
                    select(Document).where(
                        Document.tenant_id == principal.tenant.id,
                        Document.collection_id == collection.id,
                        Document.external_id == payload.external_id,
                    )
                )
                .scalars()
                .first()
            )
        if document is None:
            now = datetime.now(UTC)
            month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
            created_this_month = int(
                self.session.execute(
                    select(func.count(Document.id)).where(
                        Document.tenant_id == tenant.id,
                        Document.created_at >= month_start,
                    )
                ).scalar_one()
            )
            if (
                tenant.monthly_document_quota > 0
                and created_this_month >= tenant.monthly_document_quota
            ):
                raise HTTPException(status_code=429, detail="Monthly document quota exhausted.")
            document = Document(
                tenant_id=principal.tenant.id,
                workspace_id=workspace.id,
                collection_id=collection.id,
                external_id=payload.external_id,
                title=payload.title,
                source_url=payload.source_url,
                document_type=payload.document_type,
                content_hash=content_hash(payload.text),
                text=payload.text,
                doc_metadata=dict(payload.metadata or {}),
                status=DocumentStatus.PENDING,
            )
            self.session.add(document)
        else:
            document.workspace_id = workspace.id
            document.collection_id = collection.id
            document.title = payload.title
            document.source_url = payload.source_url
            document.document_type = payload.document_type
            document.content_hash = content_hash(payload.text)
            document.text = payload.text
            document.doc_metadata = dict(payload.metadata or {})
            document.status = DocumentStatus.PENDING
            document.error = None
        self.session.flush()
        if payload.owner_keys is not None:
            requested_owners = {owner.strip() for owner in payload.owner_keys if owner.strip()}
            if not requested_owners:
                raise HTTPException(status_code=422, detail="At least one owner key is required.")
            current = {
                grant.owner_key: grant
                for grant in self.session.execute(
                    select(DocumentVisibilityGrant).where(
                        DocumentVisibilityGrant.tenant_id == principal.tenant.id,
                        DocumentVisibilityGrant.document_id == document.id,
                    )
                ).scalars()
            }
            for removed in set(current) - requested_owners:
                self.session.delete(current[removed])
            for added in requested_owners - set(current):
                self.session.add(
                    DocumentVisibilityGrant(
                        tenant_id=principal.tenant.id,
                        document_id=document.id,
                        owner_key=added,
                    )
                )
        job = None
        if payload.index_now:
            job = self._index_one(document, chunking=payload.chunking)
        self.session.commit()
        self.session.refresh(document)
        if job is not None:
            self.session.refresh(job)
        return DocumentCreatedOut(
            document=mappers.document_out(document),
            job=mappers.job_out(job) if job else None,
        )

    def list_documents(
        self,
        principal: Principal,
        *,
        workspace_id: int | None = None,
        collection_id: int | None = None,
        limit: int = 100,
    ):
        query = select(Document).where(Document.tenant_id == principal.tenant.id)
        if workspace_id is not None:
            query = query.where(Document.workspace_id == workspace_id)
        if collection_id is not None:
            query = query.where(Document.collection_id == collection_id)
        rows = self.session.execute(query.order_by(Document.id.desc()).limit(limit)).scalars().all()
        return [mappers.document_out(row) for row in rows]

    def get_document(self, principal: Principal, document_id: int):
        document = self.session.get(Document, document_id)
        if document is None or document.tenant_id != principal.tenant.id:
            raise HTTPException(status_code=404, detail="Document not found.")
        return mappers.document_out(document)

    def delete_by_external_id(
        self, principal: Principal, external_id: str
    ) -> DocumentDeleteOut:
        """Idempotently remove one tenant-owned document and all its chunks."""
        document = self.session.execute(
            select(Document)
            .where(
                Document.tenant_id == principal.tenant.id,
                Document.external_id == external_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if document is None:
            return DocumentDeleteOut(deleted=False)
        chunks_deleted = int(
            self.session.execute(
                select(func.count(DocumentChunk.id)).where(
                    DocumentChunk.tenant_id == principal.tenant.id,
                    DocumentChunk.document_id == document.id,
                )
            ).scalar_one()
        )
        self.session.delete(document)
        self.session.commit()
        return DocumentDeleteOut(deleted=True, chunks_deleted=chunks_deleted)

    def reconcile_visibility(
        self, principal: Principal, payload: DocumentVisibilityChange
    ) -> DocumentVisibilityOut:
        """Apply an owner grant change atomically and idempotently.

        The operation row and all grant/document mutations commit together. A
        process or database failure before commit therefore leaves a clean retry.
        """
        existing = self.session.execute(
            select(VisibilityOperation)
            .where(
                VisibilityOperation.tenant_id == principal.tenant.id,
                VisibilityOperation.operation_id == payload.operation_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if existing is not None:
            if (
                existing.external_id != payload.external_id
                or existing.owner_key != payload.owner_key
                or existing.visible != payload.visible
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Operation id is already associated with a different change.",
                )
            return self._visibility_out(existing, idempotent=True)

        documents = list(
            self.session.execute(
                select(Document)
                .where(
                    Document.tenant_id == principal.tenant.id,
                    Document.external_id == payload.external_id,
                )
                .with_for_update()
            ).scalars()
        )
        if len(documents) > 1:
            raise HTTPException(
                status_code=409,
                detail="External id is ambiguous across collections.",
            )
        document = documents[0] if documents else None
        if payload.visible and document is None:
            raise HTTPException(status_code=404, detail="Document not found.")

        deleted = False
        chunks_deleted = 0
        remaining = 0
        document_id = document.id if document is not None else None
        if document is not None:
            grant = self.session.execute(
                select(DocumentVisibilityGrant).where(
                    DocumentVisibilityGrant.tenant_id == principal.tenant.id,
                    DocumentVisibilityGrant.document_id == document.id,
                    DocumentVisibilityGrant.owner_key == payload.owner_key,
                )
            ).scalar_one_or_none()
            if payload.visible:
                if grant is None:
                    self.session.add(
                        DocumentVisibilityGrant(
                            tenant_id=principal.tenant.id,
                            document_id=document.id,
                            owner_key=payload.owner_key,
                        )
                    )
                    self.session.flush()
            elif grant is not None:
                self.session.delete(grant)
                self.session.flush()

            remaining = int(
                self.session.execute(
                    select(func.count(DocumentVisibilityGrant.id)).where(
                        DocumentVisibilityGrant.tenant_id == principal.tenant.id,
                        DocumentVisibilityGrant.document_id == document.id,
                    )
                ).scalar_one()
            )
            # A document without any grants is tenant-public. Revoking an owner
            # that never had a grant must therefore stay an idempotent no-op;
            # only removal of an existing final grant retires the document.
            if not payload.visible and grant is not None and remaining == 0:
                chunks_deleted = int(
                    self.session.execute(
                        select(func.count(DocumentChunk.id)).where(
                            DocumentChunk.tenant_id == principal.tenant.id,
                            DocumentChunk.document_id == document.id,
                        )
                    ).scalar_one()
                )
                self.session.delete(document)
                deleted = True

        operation = VisibilityOperation(
            tenant_id=principal.tenant.id,
            operation_id=payload.operation_id,
            external_id=payload.external_id,
            owner_key=payload.owner_key,
            visible=payload.visible,
            document_id=document_id,
            document_deleted=deleted,
            chunks_deleted=chunks_deleted,
            remaining_owners=remaining,
        )
        self.session.add(operation)
        self._before_visibility_commit(operation)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            replay = self.session.execute(
                select(VisibilityOperation).where(
                    VisibilityOperation.tenant_id == principal.tenant.id,
                    VisibilityOperation.operation_id == payload.operation_id,
                )
            ).scalar_one_or_none()
            if replay is not None:
                if (
                    replay.external_id != payload.external_id
                    or replay.owner_key != payload.owner_key
                    or replay.visible != payload.visible
                ):
                    raise HTTPException(
                        status_code=409, detail="Operation id conflict."
                    ) from None
                return self._visibility_out(replay, idempotent=True)
            raise
        return self._visibility_out(operation, idempotent=False)

    def _before_visibility_commit(self, _operation: VisibilityOperation) -> None:
        """Failure-injection seam used to prove transaction-safe reconciliation."""

    @staticmethod
    def _visibility_out(
        operation: VisibilityOperation, *, idempotent: bool
    ) -> DocumentVisibilityOut:
        return DocumentVisibilityOut(
            operation_id=operation.operation_id,
            external_id=operation.external_id,
            owner_key=operation.owner_key,
            visible=operation.visible,
            idempotent=idempotent,
            document_deleted=operation.document_deleted,
            chunks_deleted=operation.chunks_deleted,
            remaining_owners=operation.remaining_owners,
        )

    def list_jobs(
        self,
        principal: Principal,
        *,
        status: str | None = None,
        document_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[IngestionJob], int]:
        filters = [IngestionJob.tenant_id == principal.tenant.id]
        if status is not None:
            filters.append(IngestionJob.status == status)
        if document_id is not None:
            filters.append(IngestionJob.document_id == document_id)
        total = int(
            self.session.execute(select(func.count(IngestionJob.id)).where(*filters)).scalar_one()
        )
        rows = list(
            self.session.execute(
                select(IngestionJob)
                .where(*filters)
                .order_by(IngestionJob.created_at.desc(), IngestionJob.id.desc())
                .offset(offset)
                .limit(limit)
            ).scalars()
        )
        return rows, total

    def get_job(self, principal: Principal, job_id: int) -> IngestionJob:
        job = self.session.execute(
            select(IngestionJob).where(
                IngestionJob.id == job_id,
                IngestionJob.tenant_id == principal.tenant.id,
            )
        ).scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=404, detail="Ingestion job not found.")
        return job

    def retry_job(self, principal: Principal, job_id: int) -> tuple[IngestionJob, bool]:
        original = self.session.execute(
            select(IngestionJob)
            .where(
                IngestionJob.id == job_id,
                IngestionJob.tenant_id == principal.tenant.id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if original is None:
            raise HTTPException(status_code=404, detail="Ingestion job not found.")
        if original.status not in {JobStatus.FAILED, JobStatus.PARTIAL}:
            raise HTTPException(
                status_code=409, detail="Only failed or partial jobs can be retried."
            )

        existing = self.session.execute(
            select(IngestionJob).where(IngestionJob.retry_of_job_id == original.id)
        ).scalar_one_or_none()
        if existing is not None:
            return existing, True
        if original.document_id is None:
            raise HTTPException(
                status_code=409, detail="The source document is no longer available."
            )

        document = self.session.execute(
            select(Document)
            .where(
                Document.id == original.document_id,
                Document.tenant_id == principal.tenant.id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if document is None:
            raise HTTPException(
                status_code=409, detail="The source document is no longer available."
            )
        active = self.session.execute(
            select(IngestionJob.id).where(
                IngestionJob.tenant_id == principal.tenant.id,
                IngestionJob.document_id == document.id,
                IngestionJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
        ).first()
        if active is not None:
            raise HTTPException(
                status_code=409, detail="An ingestion job is already active for this document."
            )

        try:
            job = self._index_one(
                document,
                retry_of_job_id=original.id,
                attempt=max(original.attempt, 1) + 1,
            )
            self.session.commit()
            self.session.refresh(job)
            return job, False
        except IntegrityError:
            self.session.rollback()
            existing = self.session.execute(
                select(IngestionJob).where(IngestionJob.retry_of_job_id == original.id)
            ).scalar_one_or_none()
            if existing is not None:
                return existing, True
            raise

    def index_pending(self, principal: Principal, payload: IndexRequest) -> IndexRunOut:
        provider = get_embedding_provider()
        query = select(Document).where(
            Document.tenant_id == principal.tenant.id,
            Document.status.in_([DocumentStatus.PENDING, DocumentStatus.FAILED]),
        )
        if payload.workspace_id is not None:
            query = query.where(Document.workspace_id == payload.workspace_id)
        if payload.collection_ids:
            query = query.where(Document.collection_id.in_(payload.collection_ids))
        if payload.document_ids:
            query = query.where(Document.id.in_(payload.document_ids))
        docs = (
            self.session.execute(
                query.order_by(Document.id)
                .limit(payload.limit)
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )

        report = IndexRunOut(embedding_model=provider.model)
        for document in docs:
            try:
                job = self._index_one(document)
                report.jobs_created += 1
                report.documents_indexed += int(job.status == JobStatus.SUCCESS)
                report.chunks_created += job.chunks_created
                report.chunks_deleted += job.chunks_deleted
                report.embedding_tokens += job.embedding_tokens
                if job.status != JobStatus.SUCCESS and job.error:
                    report.errors.append(job.error)
            except Exception:  # noqa: BLE001 - keep indexing the rest
                report.status = "PARTIAL"
                report.errors.append("Indexing failed; consult server diagnostics.")
        if report.errors and not report.documents_indexed:
            report.status = "FAILED"
        self.session.commit()
        return report

    def _index_one(
        self,
        document: Document,
        *,
        chunking=None,
        retry_of_job_id: int | None = None,
        attempt: int = 1,
    ) -> IngestionJob:
        provider = get_embedding_provider()
        started = time.perf_counter()
        job = IngestionJob(
            tenant_id=document.tenant_id,
            workspace_id=document.workspace_id,
            collection_id=document.collection_id,
            document_id=document.id,
            retry_of_job_id=retry_of_job_id,
            attempt=attempt,
            status=JobStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        self.session.add(job)
        self.session.flush()

        try:
            chunk_size = (
                chunking.chunk_size
                if chunking and chunking.chunk_size is not None
                else self.settings.chunk_target_tokens
            )
            chunk_overlap = (
                chunking.chunk_overlap
                if chunking and chunking.chunk_overlap is not None
                else self.settings.chunk_overlap_tokens
            )
            chunks = chunk_text(
                document.text,
                target_tokens=chunk_size,
                overlap_tokens=chunk_overlap,
                min_tokens=self.settings.chunk_min_tokens,
                pages=split_pages(document.text),
            )
            if not chunks:
                document.status = DocumentStatus.FAILED
                document.error = "Document produced no chunks."
                job.status = JobStatus.FAILED
                job.error = document.error
                job.finished_at = datetime.now(UTC)
                duration = time.perf_counter() - started
                metrics.increment(
                    "rag_index_jobs_total", result="empty", provider=provider.provider
                )
                metrics.observe("rag_index_duration_seconds", duration, result="empty")
                log_event(
                    logger,
                    logging.WARNING,
                    "rag_index_completed",
                    result="empty",
                    provider=provider.provider,
                    chunks=0,
                    duration_ms=round(duration * 1000, 2),
                )
                return job

            embedding_started = time.perf_counter()
            embed_result = self._embed_sync(provider, [chunk.content for chunk in chunks])
            metrics.observe(
                "rag_embedding_duration_seconds",
                time.perf_counter() - embedding_started,
                provider=provider.provider,
            )
            # Swap only after embedding succeeds, and protect the stored index
            # with a savepoint if any replacement row fails validation.
            with self.session.begin_nested():
                deleted = (
                    self.session.execute(
                        delete(DocumentChunk).where(DocumentChunk.document_id == document.id)
                    ).rowcount
                    or 0
                )
                for chunk, vector in zip(chunks, embed_result.vectors, strict=True):
                    metadata = dict(document.doc_metadata or {})
                    is_official = bool(metadata.get("is_official", False))
                    self.session.add(
                        DocumentChunk(
                            tenant_id=document.tenant_id,
                            workspace_id=document.workspace_id,
                            collection_id=document.collection_id,
                            document_id=document.id,
                            chunk_index=chunk.index,
                            content=chunk.content,
                            token_count=chunk.token_count,
                            content_hash=content_hash(chunk.content),
                            embedding=vector,
                            embedding_model=embed_result.model,
                            embedding_dimension=len(vector),
                            page_number=chunk.page_number,
                            title=document.title,
                            source_url=document.source_url,
                            document_type=document.document_type,
                            chunk_metadata=metadata,
                            is_official=is_official,
                            source_name=metadata.get("source_name") or metadata.get("source"),
                        )
                    )
                self.session.flush()

            document.status = DocumentStatus.INDEXED
            document.error = None
            job.status = JobStatus.SUCCESS
            job.chunks_created = len(chunks)
            job.chunks_deleted = int(deleted)
            job.embedding_tokens = embed_result.tokens
            job.finished_at = datetime.now(UTC)
            duration = time.perf_counter() - started
            metrics.increment("rag_index_jobs_total", result="success", provider=provider.provider)
            metrics.observe("rag_index_duration_seconds", duration, result="success")
            log_event(
                logger,
                logging.INFO,
                "rag_index_completed",
                result="success",
                provider=provider.provider,
                chunks=len(chunks),
                duration_ms=round(duration * 1000, 2),
                attempt=attempt,
            )
            return job
        except Exception as exc:  # noqa: BLE001 - persist failure on job and document
            document.status = DocumentStatus.FAILED
            document.error = "Indexing failed; consult server diagnostics."
            job.status = JobStatus.FAILED
            job.error = document.error
            job.finished_at = datetime.now(UTC)
            duration = time.perf_counter() - started
            metrics.increment("rag_index_jobs_total", result="failure", provider=provider.provider)
            metrics.observe("rag_index_duration_seconds", duration, result="failure")
            log_event(
                logger,
                logging.ERROR,
                "rag_index_failed",
                result="failure",
                provider=provider.provider,
                duration_ms=round(duration * 1000, 2),
                attempt=attempt,
                error_type=type(exc).__name__,
            )
            return job

    def _embed_sync(self, provider, texts: list[str]):
        import asyncio
        import threading

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(provider.embed(texts))
        result = {}
        error = {}

        def _runner() -> None:
            try:
                result["value"] = asyncio.run(provider.embed(texts))
            except Exception as exc:  # noqa: BLE001
                error["value"] = exc

        thread = threading.Thread(target=_runner)
        thread.start()
        thread.join()
        if error:
            raise error["value"]
        return result["value"]
