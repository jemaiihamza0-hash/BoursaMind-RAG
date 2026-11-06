"""Controlled structured financial tool layer.

The LLM never computes authoritative balances or P/L here. These tool records
describe which BoursaMind service should be called, and the orchestrator either
uses already supplied structured context or reports that the upstream tool is
not configured.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings
from app.financial.schemas import FinancialToolCallOut

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FinancialTool:
    name: str
    section: str
    evidence_type: str
    description: str


TOOLS: tuple[FinancialTool, ...] = (
    FinancialTool(
        "get_company",
        "company",
        "OFFICIAL_MARKET_DATA",
        "Company identity and listing.",
    ),
    FinancialTool(
        "get_market_snapshot",
        "market",
        "OFFICIAL_MARKET_DATA",
        "Latest available market session.",
    ),
    FinancialTool(
        "get_market_history",
        "market_history",
        "OFFICIAL_MARKET_DATA",
        "OHLCV and turnover history.",
    ),
    FinancialTool(
        "get_technical_indicators",
        "technicals",
        "DERIVED_METRIC",
        "RSI/MACD/SMA etc.",
    ),
    FinancialTool(
        "get_financial_ratios",
        "fundamentals",
        "DERIVED_METRIC",
        "Ratios from BoursaMind.",
    ),
    FinancialTool(
        "get_liquidity",
        "liquidity",
        "DERIVED_METRIC",
        "Liquidity and turnover context.",
    ),
    FinancialTool(
        "get_portfolio",
        "portfolio",
        "USER_PORTFOLIO_DATA",
        "Authoritative portfolio state.",
    ),
    FinancialTool(
        "get_watchlist",
        "watchlist",
        "USER_WATCHLIST_DATA",
        "User watchlist state.",
    ),
    FinancialTool(
        "get_decision_room",
        "decision_history",
        "USER_DECISION_CONTEXT",
        "User decision theses.",
    ),
    FinancialTool(
        "get_chart_context",
        "chart",
        "USER_CHART_CONTEXT",
        "Structured chart indicators/drawings.",
    ),
    FinancialTool(
        "get_paper_account",
        "paper_trading",
        "PAPER_SIMULATION",
        "Paper trading state.",
    ),
)

INTENT_TOOLS: dict[str, tuple[str, ...]] = {
    "MARKET_ANALYSIS": (
        "get_company",
        "get_market_snapshot",
        "get_market_history",
        "get_liquidity",
    ),
    "COMPANY_ANALYSIS": (
        "get_company",
        "get_market_snapshot",
        "get_market_history",
        "get_technical_indicators",
        "get_financial_ratios",
        "get_liquidity",
    ),
    "FUNDAMENTAL_ANALYSIS": ("get_company", "get_financial_ratios"),
    "TECHNICAL_ANALYSIS": ("get_market_history", "get_technical_indicators", "get_chart_context"),
    "DOCUMENT_QUESTION": ("get_company",),
    "PORTFOLIO_ANALYSIS": ("get_portfolio", "get_watchlist", "get_liquidity"),
    "RISK_ANALYSIS": ("get_portfolio", "get_liquidity", "get_paper_account"),
    "DECISION_REVIEW": ("get_decision_room", "get_portfolio", "get_chart_context"),
    "PAPER_TRADE_CONTEXT": ("get_paper_account", "get_market_snapshot"),
    "REGULATION_QUESTION": (),
    "COMPARISON": ("get_company", "get_market_snapshot", "get_financial_ratios"),
    "COMBINED_FINANCIAL_CONTEXT": (
        "get_company",
        "get_market_snapshot",
        "get_market_history",
        "get_technical_indicators",
        "get_liquidity",
        "get_portfolio",
        "get_watchlist",
        "get_decision_room",
        "get_chart_context",
        "get_paper_account",
    ),
    "GENERAL_FINANCE": (),
}


def tool_manifest() -> list[dict[str, str]]:
    return [
        {
            "name": tool.name,
            "section": tool.section,
            "evidence_type": tool.evidence_type,
            "description": tool.description,
        }
        for tool in TOOLS
    ]


def select_tools(intent: str) -> list[FinancialTool]:
    names = set(INTENT_TOOLS.get(intent, ()))
    return [tool for tool in TOOLS if tool.name in names]


def build_tool_calls(intent: str, structured_context: dict[str, Any]) -> list[FinancialToolCallOut]:
    calls: list[FinancialToolCallOut] = []
    for tool in select_tools(intent):
        section_data = structured_context.get(tool.section)
        if section_data:
            calls.append(
                FinancialToolCallOut(
                    name=tool.name,
                    status="SUPPLIED",
                    evidence_type=tool.evidence_type,  # type: ignore[arg-type]
                    source="BoursaMind structured context",
                    data=section_data,
                )
            )
        else:
            calls.append(
                FinancialToolCallOut(
                    name=tool.name,
                    status="NOT_AVAILABLE",
                    evidence_type=tool.evidence_type,  # type: ignore[arg-type]
                    source="BoursaMind service API",
                    limitation=(
                        "No structured context was supplied and live BoursaMind tool proxy "
                        "is not configured in this standalone test run."
                    ),
                )
            )
    return calls


def fetch_live_tool_calls(
    intent: str, *, user_id: str, ticker: str | None, supplied: dict[str, Any]
) -> list[FinancialToolCallOut]:
    """Fetch only allowlisted context from BoursaMind's service bridge.

    Client-provided context remains an explicit compatibility input, but it is
    never mistaken for live data.  The bridge receives the authenticated user
    scope from the RAG API, not an LLM-selected user id.
    """
    settings = get_settings()
    calls = build_tool_calls(intent, supplied)
    if not settings.boursamind_api_url.strip() or not settings.boursamind_service_key.strip():
        return calls
    for call in calls:
        if call.status == "SUPPLIED":
            continue
        try:
            response = httpx.post(
                settings.boursamind_api_url.rstrip("/") + "/api/v1/ai/financial-tools/execute",
                headers={
                    "X-BoursaMind-RAG-Service-Key": settings.boursamind_service_key,
                    "X-BoursaMind-User-Id": user_id,
                },
                json={"tool": call.name, "parameters": {"ticker": ticker} if ticker else {}},
                timeout=settings.boursamind_api_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Invalid structured tool response")
            data = payload.get("data")
            if isinstance(data, dict) and data.get("found") is False:
                call.limitation = (
                    payload.get("limitations")
                    or "No authoritative BoursaMind structured data was found for this scope."
                )
                continue
            if data is not None:
                call.status = "LIVE"
                call.source = str(payload.get("source") or "BoursaMind API")
                if payload.get("evidence_type"):
                    call.evidence_type = payload["evidence_type"]
                call.data = data
                call.limitation = payload.get("limitations")
        except (httpx.HTTPError, ValueError) as exc:
            # Financial answers remain available from documents/memory when a
            # structured provider is down; do not leak transport details.
            status_code = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None
                else None
            )
            logger.warning(
                "BoursaMind financial tool bridge failed",
                extra={
                    "financial_tool": call.name,
                    "failure_type": type(exc).__name__,
                    "upstream_status_code": status_code,
                },
            )
            call.limitation = "Authoritative BoursaMind structured data is temporarily unavailable."
    return calls
