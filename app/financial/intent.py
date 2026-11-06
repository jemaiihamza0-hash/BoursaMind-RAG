"""Small deterministic intent classifier for financial context routing."""

from __future__ import annotations

import re

from app.retrieval.scoring import fold


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    """Match intent terms as words/phrases, never inside an issuer name.

    The listed ticker ``SMART`` used to match the technical indicator ``SMA``
    and therefore lost company, snapshot, fundamentals and liquidity tools.
    Word boundaries keep deterministic routing stable for the complete market
    while still accepting multi-word intent phrases.
    """

    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None
        for term in terms
    )


def classify_financial_intent(question: str, explicit: str | None = None) -> str:
    if explicit:
        normalized = explicit.strip().upper()
        if normalized:
            return normalized
    text = fold(question)
    if (
        _contains_any(text, ("portfolio", "portefeuille"))
        and _contains_any(text, ("these", "thesis", "decision", "memoire"))
        and _contains_any(text, ("evolution", "indicateur", "market", "marche"))
    ):
        return "COMBINED_FINANCIAL_CONTEXT"
    if _contains_any(text, ("portfolio", "portefeuille", "position", "exposure", "cash")):
        return "PORTFOLIO_ANALYSIS"
    if _contains_any(text, ("paper", "simulation", "ordre", "execution")):
        return "PAPER_TRADE_CONTEXT"
    if _contains_any(text, ("decision", "these", "thesis", "reconsider")):
        return "DECISION_REVIEW"
    if _contains_any(text, ("rsi", "macd", "support", "resistance", "sma", "graphique")):
        return "TECHNICAL_ANALYSIS"
    if _contains_any(text, ("ratio", "ratios", "bilan", "resultat", "fundamental")):
        return "FUNDAMENTAL_ANALYSIS"
    if _contains_any(text, ("regulation", "cmf", "bct", "loi")):
        return "REGULATION_QUESTION"
    if _contains_any(text, ("publication", "document", "rapport", "prospectus")):
        return "DOCUMENT_QUESTION"
    if _contains_any(text, ("compare", "versus", "vs")):
        return "COMPARISON"
    if _contains_any(
        text,
        ("market", "markets", "price", "prices", "volume", "liquidity", "bvmt"),
    ):
        return "MARKET_ANALYSIS"
    return "COMPANY_ANALYSIS"
