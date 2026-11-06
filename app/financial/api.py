"""Financial AI brain routes."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.auth.dependencies import BoursaMindUserDep, DbSessionDep, PrincipalDep, QueryPrincipalDep
from app.financial.memory import InvestorMemoryService
from app.financial.orchestrator import FinancialContextOrchestrator
from app.financial.schemas import (
    FinancialQueryRequest,
    FinancialQueryResponse,
    InvestorMemoryCreate,
    InvestorMemoryOut,
    InvestorMemoryUpdate,
)
from app.financial.tools import tool_manifest

router = APIRouter(prefix="/financial", tags=["financial-ai"])


@router.get("/tools")
def list_financial_tools(_principal: QueryPrincipalDep) -> dict:
    return {"tools": tool_manifest()}


@router.post("/query", response_model=FinancialQueryResponse)
def financial_query(
    payload: FinancialQueryRequest,
    principal: PrincipalDep,
    user_id: BoursaMindUserDep,
    session: DbSessionDep,
) -> FinancialQueryResponse:
    return FinancialContextOrchestrator(session).answer(principal, user_id, payload)


@router.post("/memory", response_model=InvestorMemoryOut, status_code=201)
def create_memory(
    payload: InvestorMemoryCreate,
    principal: PrincipalDep,
    user_id: BoursaMindUserDep,
    session: DbSessionDep,
) -> InvestorMemoryOut:
    return InvestorMemoryService(session).create_or_update(principal, user_id, payload)


@router.get("/memory", response_model=list[InvestorMemoryOut])
def list_memory(
    principal: PrincipalDep,
    user_id: BoursaMindUserDep,
    session: DbSessionDep,
    ticker: str | None = Query(default=None),
    memory_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    include_inactive: bool = Query(default=False),
) -> list[InvestorMemoryOut]:
    return InvestorMemoryService(session).recall(
        principal,
        user_id,
        ticker=ticker,
        memory_type=memory_type,
        limit=limit,
        include_inactive=include_inactive,
    )


@router.patch("/memory/{memory_id}", response_model=InvestorMemoryOut)
def update_memory(
    memory_id: int,
    payload: InvestorMemoryUpdate,
    principal: PrincipalDep,
    user_id: BoursaMindUserDep,
    session: DbSessionDep,
) -> InvestorMemoryOut:
    return InvestorMemoryService(session).update(principal, user_id, memory_id, payload)


@router.delete("/memory/{memory_id}", status_code=204)
def delete_memory(
    memory_id: int,
    principal: PrincipalDep,
    user_id: BoursaMindUserDep,
    session: DbSessionDep,
) -> None:
    InvestorMemoryService(session).delete_one(principal, user_id, memory_id)


@router.delete("/memory")
def reset_memory(
    principal: PrincipalDep,
    user_id: BoursaMindUserDep,
    session: DbSessionDep,
) -> dict:
    return {"deleted": InvestorMemoryService(session).delete_all(principal, user_id)}
