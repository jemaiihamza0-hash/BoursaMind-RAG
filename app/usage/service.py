"""Usage and quota accounting."""

from collections import Counter
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.security import Principal
from app.models import Document, DocumentChunk, Tenant, UsageEvent
from app.schemas import UsageOut


class UsageService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        principal: Principal,
        *,
        request_id: str,
        operation: str,
        status_code: int,
        latency_ms: int,
        input_tokens: int = 0,
        output_tokens: int = 0,
        embedding_tokens: int = 0,
        chunks_retrieved: int = 0,
        provider: str | None = None,
        error_code: str | None = None,
        flags: list[str] | None = None,
        quota_identity: str | None = None,
    ) -> None:
        quota_identity = f"query:{quota_identity}" if quota_identity else None
        event = None
        if quota_identity:
            event = self.session.execute(
                select(UsageEvent).where(
                    UsageEvent.tenant_id == principal.tenant.id,
                    UsageEvent.quota_identity == quota_identity,
                )
            ).scalar_one_or_none()
        if event is None:
            event = UsageEvent(
                tenant_id=principal.tenant.id,
                api_key_id=principal.api_key.id if principal.api_key else None,
                request_id=request_id,
                quota_identity=quota_identity,
                operation=operation,
            )
            self.session.add(event)
        event.status_code = status_code
        event.latency_ms = latency_ms
        event.input_tokens = input_tokens
        event.output_tokens = output_tokens
        event.embedding_tokens = embedding_tokens
        event.chunks_retrieved = chunks_retrieved
        event.provider = provider
        event.error_code = error_code
        event.flags = flags

    def reserve_query(
        self, principal: Principal, *, request_id: str, quota_identity: str
    ) -> None:
        """Atomically reserve one tenant query before any provider call.

        A tenant row lock serializes quota decisions across workers. The
        request identity is unique per tenant, preventing retry races from
        consuming provider work twice. Failed provider calls roll back this
        uncommitted reservation and therefore do not consume the quota.
        """
        tenant = self.session.execute(
            select(Tenant).where(Tenant.id == principal.tenant.id).with_for_update()
        ).scalar_one()
        quota_identity = f"query:{quota_identity}"
        duplicate = self.session.execute(
            select(UsageEvent.id).where(
                UsageEvent.tenant_id == tenant.id,
                UsageEvent.quota_identity == quota_identity,
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="This request identity was already used.")

        now = datetime.now(UTC)
        month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
        used = int(
            self.session.execute(
                select(func.count(UsageEvent.id)).where(
                    UsageEvent.tenant_id == tenant.id,
                    UsageEvent.operation == "query",
                    UsageEvent.created_at >= month_start,
                )
            ).scalar_one()
        )
        if tenant.monthly_query_quota > 0 and used >= tenant.monthly_query_quota:
            raise HTTPException(status_code=429, detail="Monthly query quota exhausted.")
        self.session.add(
            UsageEvent(
                tenant_id=tenant.id,
                api_key_id=principal.api_key.id if principal.api_key else None,
                request_id=request_id,
                quota_identity=quota_identity,
                operation="query",
                status_code=102,
                latency_ms=0,
                flags=["reserved"],
            )
        )
        self.session.flush()

    def report(self, principal: Principal, *, days: int = 30) -> UsageOut:
        since = datetime.now(UTC) - timedelta(days=days)
        rows = (
            self.session.execute(
                select(UsageEvent).where(
                    UsageEvent.tenant_id == principal.tenant.id,
                    UsageEvent.created_at >= since,
                )
            )
            .scalars()
            .all()
        )
        by_operation = Counter(row.operation for row in rows)
        documents = int(
            self.session.execute(
                select(func.count(Document.id)).where(Document.tenant_id == principal.tenant.id)
            ).scalar_one()
        )
        chunks = int(
            self.session.execute(
                select(func.count(DocumentChunk.id)).where(
                    DocumentChunk.tenant_id == principal.tenant.id
                )
            ).scalar_one()
        )
        return UsageOut(
            tenant=principal.tenant.slug,
            requests=len(rows),
            documents=documents,
            chunks=chunks,
            embedding_tokens=sum(row.embedding_tokens for row in rows),
            input_tokens=sum(row.input_tokens for row in rows),
            output_tokens=sum(row.output_tokens for row in rows),
            by_operation=dict(by_operation),
        )
