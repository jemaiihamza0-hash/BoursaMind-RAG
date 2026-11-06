"""User-isolated investor memory service."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.security import Principal
from app.financial.schemas import InvestorMemoryCreate, InvestorMemoryOut, InvestorMemoryUpdate
from app.models import InvestorMemory


class InvestorMemoryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_or_update(
        self, principal: Principal, user_id: str, payload: InvestorMemoryCreate
    ) -> InvestorMemoryOut:
        row = (
            self.session.execute(
                select(InvestorMemory).where(
                    InvestorMemory.tenant_id == principal.tenant.id,
                    InvestorMemory.user_id == user_id,
                    InvestorMemory.memory_type == payload.memory_type,
                    InvestorMemory.ticker == payload.ticker,
                    InvestorMemory.source_event == payload.source_event,
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            row = InvestorMemory(
                tenant_id=principal.tenant.id,
                user_id=user_id,
                memory_type=payload.memory_type,
                ticker=payload.ticker,
                company_name=payload.company_name,
                source_event=payload.source_event,
                summary=payload.summary,
                evidence=payload.evidence,
                confidence=payload.confidence,
                importance=payload.importance,
                valid_from=payload.valid_from,
                last_observed_at=payload.last_observed_at,
            )
            self.session.add(row)
        elif not row.is_user_edited:
            row.company_name = payload.company_name or row.company_name
            row.summary = payload.summary
            row.evidence = payload.evidence
            row.confidence = payload.confidence
            row.importance = payload.importance
            row.valid_from = payload.valid_from or row.valid_from
            row.last_observed_at = payload.last_observed_at or row.last_observed_at
            row.is_active = True
        self.session.commit()
        self.session.refresh(row)
        return InvestorMemoryOut.model_validate(row)

    def recall(
        self,
        principal: Principal,
        user_id: str,
        *,
        ticker: str | None = None,
        memory_type: str | None = None,
        limit: int = 8,
        include_inactive: bool = False,
    ) -> list[InvestorMemoryOut]:
        query = select(InvestorMemory).where(
            InvestorMemory.tenant_id == principal.tenant.id,
            InvestorMemory.user_id == user_id,
        )
        if not include_inactive:
            query = query.where(InvestorMemory.is_active.is_(True))
        if ticker:
            query = query.where(
                (InvestorMemory.ticker == ticker.upper()) | (InvestorMemory.ticker.is_(None))
            )
        if memory_type:
            query = query.where(InvestorMemory.memory_type == memory_type.upper())
        rows = (
            self.session.execute(
                query.order_by(
                    InvestorMemory.importance.desc(),
                    InvestorMemory.updated_at.desc(),
                ).limit(limit)
            )
            .scalars()
            .all()
        )
        return [InvestorMemoryOut.model_validate(row) for row in rows]

    def update(
        self, principal: Principal, user_id: str, memory_id: int, payload: InvestorMemoryUpdate
    ) -> InvestorMemoryOut:
        row = self._get_owned(principal, user_id, memory_id)
        if payload.summary is not None:
            row.summary = payload.summary
        if payload.evidence is not None:
            row.evidence = payload.evidence
        if payload.confidence is not None:
            row.confidence = payload.confidence
        if payload.importance is not None:
            row.importance = payload.importance
        if payload.is_active is not None:
            row.is_active = payload.is_active
        row.is_user_edited = True
        row.updated_at = datetime.now(UTC)
        self.session.commit()
        self.session.refresh(row)
        return InvestorMemoryOut.model_validate(row)

    def delete_one(self, principal: Principal, user_id: str, memory_id: int) -> None:
        row = self._get_owned(principal, user_id, memory_id)
        self.session.delete(row)
        self.session.commit()

    def delete_all(self, principal: Principal, user_id: str) -> int:
        result = self.session.execute(
            delete(InvestorMemory).where(
                InvestorMemory.tenant_id == principal.tenant.id,
                InvestorMemory.user_id == user_id,
            )
        )
        self.session.commit()
        return int(result.rowcount or 0)

    def _get_owned(self, principal: Principal, user_id: str, memory_id: int) -> InvestorMemory:
        row = self.session.get(InvestorMemory, memory_id)
        if row is None or row.tenant_id != principal.tenant.id or row.user_id != user_id:
            raise HTTPException(status_code=404, detail="Investor memory not found.")
        return row
