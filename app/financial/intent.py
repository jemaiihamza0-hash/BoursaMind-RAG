"""Small deterministic intent classifier for financial context routing."""

from __future__ import annotations

from app.retrieval.scoring import fold


def classify_financial_intent(question: str, explicit: str | None = None) -> str:
    if explicit:
        normalized = explicit.strip().upper()
        if normalized:
            return normalized
    text = fold(question)
    if (
        any(term in text for term in ("portfolio", "portefeuille"))
        and any(term in text for term in ("these", "thesis", "decision", "memoire"))
        and any(term in text for term in ("evolution", "indicateur", "market", "marche"))
    ):
        return "COMBINED_FINANCIAL_CONTEXT"
    if any(term in text for term in ("portfolio", "portefeuille", "position", "exposure", "cash")):
        return "PORTFOLIO_ANALYSIS"
    if any(term in text for term in ("paper", "simulation", "ordre", "execution")):
        return "PAPER_TRADE_CONTEXT"
    if any(term in text for term in ("decision", "these", "thesis", "reconsider")):
        return "DECISION_REVIEW"
    if any(term in text for term in ("rsi", "macd", "support", "resistance", "sma", "graphique")):
        return "TECHNICAL_ANALYSIS"
    if any(term in text for term in ("ratio", "bilan", "resultat", "fundamental")):
        return "FUNDAMENTAL_ANALYSIS"
    if any(term in text for term in ("regulation", "cmf", "bct", "loi")):
        return "REGULATION_QUESTION"
    if any(term in text for term in ("publication", "document", "rapport", "prospectus")):
        return "DOCUMENT_QUESTION"
    if any(term in text for term in ("compare", "versus", "vs")):
        return "COMPARISON"
    if any(term in text for term in ("market", "price", "volume", "liquidity", "bvmt")):
        return "MARKET_ANALYSIS"
    return "COMPANY_ANALYSIS"
