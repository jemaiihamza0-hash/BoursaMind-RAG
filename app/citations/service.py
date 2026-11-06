"""Citation lookup service."""

from fastapi import HTTPException
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.auth.security import Principal
from app.models import Document, DocumentChunk, DocumentVisibilityGrant
from app.retrieval.service import QueryService
from app.schemas import SourceOut


class CitationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_source(
        self, principal: Principal, chunk_id: int, *, visibility_owner: str | None = None
    ) -> SourceOut:
        any_grant = exists(
            select(DocumentVisibilityGrant.id).where(
                DocumentVisibilityGrant.tenant_id == principal.tenant.id,
                DocumentVisibilityGrant.document_id == Document.id,
            )
        )
        query = (
            select(DocumentChunk)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                DocumentChunk.id == chunk_id,
                DocumentChunk.tenant_id == principal.tenant.id,
                Document.tenant_id == principal.tenant.id,
                Document.status == "INDEXED",
            )
        )
        if visibility_owner is None:
            query = query.where(~any_grant)
        else:
            query = query.where(
                (~any_grant)
                | exists(
                    select(DocumentVisibilityGrant.id).where(
                        DocumentVisibilityGrant.tenant_id == principal.tenant.id,
                        DocumentVisibilityGrant.document_id == Document.id,
                        DocumentVisibilityGrant.owner_key == visibility_owner,
                    )
                )
            )
        chunk = self.session.execute(query).scalar_one_or_none()
        if chunk is None:
            raise HTTPException(status_code=404, detail="Source not found.")
        result = QueryService(self.session)._result(
            {
                "chunk": chunk,
                "signals": type(
                    "Signals",
                    (),
                    {
                        "score": 1.0,
                        "vector_similarity": 1.0,
                        "lexical_score": 0.0,
                        "metadata_match": 1.0,
                    },
                )(),
            }
        )
        return SourceOut(citation=result.citation, content=chunk.content)
