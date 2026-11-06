"""Schemas for the BoursaMind financial AI brain layer."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EvidenceType = Literal[
    "OFFICIAL_DOCUMENT",
    "DOCUMENT_EVIDENCE",
    "OFFICIAL_MARKET_DATA",
    "OFFICIAL_FACT",
    "DERIVED_METRIC",
    "DERIVED_FINANCIAL_METRIC",
    "USER_PORTFOLIO_DATA",
    "USER_WATCHLIST_DATA",
    "USER_CHART_CONTEXT",
    "USER_DECISION_CONTEXT",
    "USER_ANALYSIS",
    "USER_MEMORY",
    "AI_INFERENCE",
    "PAPER_SIMULATION",
    "SIMULATION_RESULT",
    "UNKNOWN",
]

FINANCIAL_INTENTS = {
    "MARKET_ANALYSIS",
    "COMPANY_ANALYSIS",
    "FUNDAMENTAL_ANALYSIS",
    "TECHNICAL_ANALYSIS",
    "DOCUMENT_QUESTION",
    "PORTFOLIO_ANALYSIS",
    "RISK_ANALYSIS",
    "DECISION_REVIEW",
    "PAPER_TRADE_CONTEXT",
    "REGULATION_QUESTION",
    "COMPARISON",
    "GENERAL_FINANCE",
    "COMBINED_FINANCIAL_CONTEXT",
}

MEMORY_TYPES = {
    "PROFILE",
    "PREFERENCE",
    "THESIS",
    "DECISION",
    "TRADE",
    "MISTAKE",
    "BEHAVIOR_PATTERN",
    "WATCHLIST_INTEREST",
    "PORTFOLIO_PATTERN",
    "LEARNING",
    "RISK_PATTERN",
    "USER_ANALYSIS",
}


class InvestorMemoryCreate(BaseModel):
    memory_type: str = Field(max_length=40)
    ticker: str | None = Field(default=None, max_length=40)
    company_name: str | None = Field(default=None, max_length=255)
    source_event: str = Field(max_length=120)
    summary: str = Field(min_length=1, max_length=4000)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    confidence: int = Field(default=50, ge=0, le=100)
    importance: int = Field(default=50, ge=0, le=100)
    valid_from: datetime | None = None
    last_observed_at: datetime | None = None

    @field_validator("memory_type")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in MEMORY_TYPES:
            raise ValueError(f"Unsupported memory_type: {value}")
        return normalized

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None


class InvestorMemoryUpdate(BaseModel):
    summary: str | None = Field(default=None, min_length=1, max_length=4000)
    evidence: list[dict[str, Any]] | None = None
    confidence: int | None = Field(default=None, ge=0, le=100)
    importance: int | None = Field(default=None, ge=0, le=100)
    is_active: bool | None = None


class InvestorMemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: str
    memory_type: str
    ticker: str | None = None
    company_name: str | None = None
    source_event: str
    summary: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    confidence: int
    importance: int
    valid_from: datetime | None = None
    last_observed_at: datetime | None = None
    is_active: bool
    is_user_edited: bool
    created_at: datetime
    updated_at: datetime


class FinancialStructuredContext(BaseModel):
    company: dict[str, Any] | None = None
    market: dict[str, Any] | None = None
    market_history: dict[str, Any] | None = None
    technicals: dict[str, Any] | None = None
    fundamentals: dict[str, Any] | None = None
    liquidity: dict[str, Any] | None = None
    portfolio: dict[str, Any] | None = None
    watchlist: dict[str, Any] | None = None
    chart: dict[str, Any] | None = None
    decision_history: dict[str, Any] | None = None
    paper_trading: dict[str, Any] | None = None


class FinancialQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    ticker: str | None = Field(default=None, max_length=40)
    company_name: str | None = Field(default=None, max_length=255)
    intent: str | None = Field(default=None, max_length=40)
    structured_context: FinancialStructuredContext = Field(
        default_factory=FinancialStructuredContext
    )
    top_k: int = Field(default=5, ge=1, le=20)
    include_answer: bool = True

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None


class FinancialToolCallOut(BaseModel):
    name: str
    status: str
    evidence_type: EvidenceType
    source: str
    data: dict[str, Any] | None = None
    limitation: str | None = None


class FinancialContextOut(BaseModel):
    company: dict[str, Any] | None = None
    market: dict[str, Any] | None = None
    market_history: dict[str, Any] | None = None
    technicals: dict[str, Any] | None = None
    fundamentals: dict[str, Any] | None = None
    liquidity: dict[str, Any] | None = None
    documents: list[dict[str, Any]] = Field(default_factory=list)
    portfolio: dict[str, Any] | None = None
    watchlist: dict[str, Any] | None = None
    chart: dict[str, Any] | None = None
    decision_history: dict[str, Any] | None = None
    paper_trading: dict[str, Any] | None = None
    user_memory: list[InvestorMemoryOut] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    known_limitations: list[str] = Field(default_factory=list)


class FinancialQueryResponse(BaseModel):
    request_id: str
    intent: str
    answer: str
    context: FinancialContextOut
    citations: list[dict[str, Any]] = Field(default_factory=list)
    tools_called: list[FinancialToolCallOut] = Field(default_factory=list)
    evidence_types: list[EvidenceType] = Field(default_factory=list)
    abstained: bool = False
    abstain_reason: str | None = None
