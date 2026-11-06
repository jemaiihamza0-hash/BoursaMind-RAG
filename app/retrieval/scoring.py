"""Generic retrieval scoring, reranking, and citation-support helpers."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")
_STOPWORDS: frozenset[str] = frozenset(
    """
    a an and are as at be been being by for from had has have he her hers him his
    i in into is it its me my of on or our ours she that the their them they this
    those to was we were what when where which who whom will with you your
    au aux avec ce ces dans de des du elle en et eux il je la le les leur lui ma
    mais me mes moi mon ne nos notre nous on ou par pas pour qu que qui sa se ses
    son sur ta te tes toi ton tu un une vos votre vous
    """.split()
)


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def tokenize(text: str) -> list[str]:
    return [token for token in _TOKEN_RE.findall(fold(text)) if token not in _STOPWORDS]


BM25_K1 = 1.5
BM25_B = 0.75
BM25_SATURATION = 3.0


@dataclass
class Bm25Index:
    documents: list[list[str]]
    _df: Counter = field(default_factory=Counter, init=False)
    _avg_len: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        for tokens in self.documents:
            self._df.update(set(tokens))
        lengths = [len(document) for document in self.documents]
        self._avg_len = (sum(lengths) / len(lengths)) if lengths else 0.0

    def score(self, query_tokens: list[str], index: int) -> float:
        document = self.documents[index]
        if not document or not query_tokens:
            return 0.0
        total = 0.0
        n = len(self.documents)
        frequencies = Counter(document)
        length = len(document)
        for term in set(query_tokens):
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            df = self._df.get(term, 0)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            denominator = frequency + BM25_K1 * (
                1 - BM25_B + BM25_B * (length / self._avg_len if self._avg_len else 1)
            )
            total += idf * (frequency * (BM25_K1 + 1)) / denominator
        return total

    def absolute_scores(self, query_tokens: list[str]) -> list[float]:
        return [
            raw / (raw + BM25_SATURATION)
            for raw in (self.score(query_tokens, i) for i in range(len(self.documents)))
        ]


def term_coverage(query_tokens: list[str], passage_tokens: set[str]) -> float:
    unique = set(query_tokens)
    if not unique:
        return 0.0
    return len(unique & passage_tokens) / len(unique)


def recency_factor(publication_date: date | None, *, today: date | None = None) -> float:
    if publication_date is None:
        return 0.0
    reference = today or datetime.now(UTC).date()
    age_days = max(0, (reference - publication_date).days)
    return 0.5 ** (age_days / 180)


@dataclass(frozen=True)
class TemporalIntent:
    latest: bool = False
    frequency: str | None = None


def temporal_intent(query: str) -> TemporalIntent:
    text = fold(query)
    latest = any(
        token in text
        for token in ("latest", "most recent", "newest", "dernier", "derniere", "recent")
    )
    frequency = None
    if any(token in text for token in ("quarter", "trimestre", "trimestriel")):
        frequency = "QUARTERLY"
    elif any(token in text for token in ("half year", "half-year", "interim", "semestr")):
        frequency = "INTERIM"
    elif any(token in text for token in ("annual", "fiscal year", "annuel", "exercice")):
        frequency = "ANNUAL"
    return TemporalIntent(latest=latest, frequency=frequency)


def _period_parts(metadata: dict[str, Any]) -> tuple[int, int] | None:
    raw = str(metadata.get("reporting_period") or metadata.get("period") or "").upper()
    match = re.search(r"(?P<year>20\d{2})(?:[- ]?(?P<kind>Q|H|S)(?P<part>[1-4]))?", raw)
    if match:
        year = int(match.group("year"))
        kind = match.group("kind")
        part = int(match.group("part") or 4)
        if kind in {"H", "S"}:
            part = 2 if part == 1 else 4
        return year, part
    fiscal_year = metadata.get("fiscal_year")
    try:
        year = int(fiscal_year)
    except (TypeError, ValueError):
        return None
    quarter = metadata.get("fiscal_quarter")
    try:
        return year, int(quarter) if quarter else 4
    except (TypeError, ValueError):
        return year, 4


def frequency_matches(metadata: dict[str, Any], intent: TemporalIntent) -> bool:
    if intent.frequency is None:
        return True
    frequency = str(metadata.get("reporting_frequency") or "").upper()
    period = str(metadata.get("reporting_period") or metadata.get("period") or "").upper()
    if intent.frequency == "ANNUAL":
        return frequency == "ANNUAL" or bool(re.fullmatch(r"20\d{2}", period))
    if intent.frequency == "QUARTERLY":
        return frequency == "QUARTERLY" or bool(re.search(r"(?:^|-)Q[1-4]$", period))
    return frequency in {"SEMI_ANNUAL", "HALF_YEAR", "INTERIM", "YTD"} or bool(
        re.search(r"(?:^|-)[HS][12]$", period)
    )


def period_rank(metadata: dict[str, Any], intent: TemporalIntent) -> tuple[int, int, int] | None:
    if not frequency_matches(metadata, intent):
        return None
    parts = _period_parts(metadata)
    if parts is None:
        return None
    try:
        version = int(metadata.get("document_version") or metadata.get("version") or 1)
    except (TypeError, ValueError):
        version = 1
    return parts[0], parts[1], version


def temporal_relevance(
    metadata: dict[str, Any], intent: TemporalIntent, latest_rank: tuple[int, int, int] | None
) -> float:
    if not intent.latest or latest_rank is None:
        return 0.0
    rank = period_rank(metadata, intent)
    if rank is None:
        return 0.0
    latest_ordinal = latest_rank[0] * 4 + latest_rank[1]
    ordinal = rank[0] * 4 + rank[1]
    distance = max(0, latest_ordinal - ordinal)
    score = 0.5 ** (distance / 4)
    if rank[:2] == latest_rank[:2] and rank[2] < latest_rank[2]:
        score *= 0.75 ** (latest_rank[2] - rank[2])
    return score


def is_superseded(metadata: dict[str, Any]) -> bool:
    return bool(
        metadata.get("is_superseded")
        or metadata.get("superseded")
        or metadata.get("superseded_by")
    )


def metadata_match(filters: dict[str, Any], metadata: dict[str, Any]) -> float:
    if not filters:
        return 0.5
    matched = 0
    for key, expected in filters.items():
        actual = metadata.get(key)
        if isinstance(expected, list):
            matched += int(actual in expected)
        elif isinstance(expected, dict) and "$in" in expected:
            matched += int(actual in expected["$in"])
        else:
            matched += int(actual == expected)
    return matched / len(filters)


def metadata_allows(filters: dict[str, Any], metadata: dict[str, Any]) -> bool:
    if not filters:
        return True
    for key, expected in filters.items():
        actual = metadata.get(key)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


def source_trust(metadata: dict[str, Any], *, is_official: bool) -> float:
    raw = metadata.get("source_trust_score", metadata.get("trust_score"))
    try:
        value = float(raw) if raw is not None else 0.5
    except (TypeError, ValueError):
        value = 0.5
    if is_official:
        return max(value, 0.9)
    return max(0.0, min(1.0, value))


@dataclass
class RerankSignals:
    vector_similarity: float
    lexical_score: float
    coverage: float
    metadata_match: float
    trust: float
    recency: float
    temporal_relevance: float
    topical_relevance: float
    score: float


def rerank(
    *,
    vector_similarity: float,
    lexical_score: float,
    coverage: float,
    metadata_match_score: float,
    trust: float,
    recency: float,
    temporal_relevance: float = 0.0,
    superseded: bool = False,
) -> RerankSignals:
    topical = (
        0.34 * max(0.0, vector_similarity)
        + 0.31 * lexical_score
        + 0.25 * coverage
        + 0.10 * metadata_match_score
    )
    boost = 1.0 + 0.08 * trust + 0.04 * recency + 0.22 * temporal_relevance
    if superseded:
        boost *= 0.75
    return RerankSignals(
        vector_similarity=vector_similarity,
        lexical_score=lexical_score,
        coverage=coverage,
        metadata_match=metadata_match_score,
        trust=trust,
        recency=recency,
        temporal_relevance=temporal_relevance,
        topical_relevance=topical,
        score=topical * boost,
    )


def citation_support(claim: str, passage: str) -> float:
    claim_tokens = set(tokenize(claim))
    if not claim_tokens:
        return 0.0
    passage_tokens = set(tokenize(passage))
    return len(claim_tokens & passage_tokens) / len(claim_tokens)
