"""Model-to-schema mapping helpers."""

from app.models import Document, IngestionJob
from app.schemas import DocumentOut, JobOut


def document_out(document: Document) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        tenant_id=document.tenant_id,
        workspace_id=document.workspace_id,
        collection_id=document.collection_id,
        external_id=document.external_id,
        title=document.title,
        source_url=document.source_url,
        document_type=document.document_type,
        content_hash=document.content_hash,
        metadata=dict(document.doc_metadata or {}),
        status=document.status,
        error=document.error,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def job_out(job: IngestionJob) -> JobOut:
    return JobOut(
        id=job.id,
        tenant_id=job.tenant_id,
        workspace_id=job.workspace_id,
        collection_id=job.collection_id,
        document_id=job.document_id,
        retry_of_job_id=job.retry_of_job_id,
        attempt=job.attempt,
        status=job.status,
        chunks_created=job.chunks_created,
        chunks_deleted=job.chunks_deleted,
        embedding_tokens=job.embedding_tokens,
        error=job.error,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
    )
