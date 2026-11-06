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
) -> RerankSignals:
    topical = (
        0.34 * max(0.0, vector_similarity)
        + 0.31 * lexical_score
        + 0.25 * coverage
        + 0.10 * metadata_match_score
    )
    boost = 1.0 + 0.08 * trust + 0.04 * recency
    return RerankSignals(
        vector_similarity=vector_similarity,
        lexical_score=lexical_score,
        coverage=coverage,
        metadata_match=metadata_match_score,
        trust=trust,
        recency=recency,
        topical_relevance=topical,
        score=topical * boost,
    )


def citation_support(claim: str, passage: str) -> float:
    claim_tokens = set(tokenize(claim))
    if not claim_tokens:
        return 0.0
    passage_tokens = set(tokenize(passage))
    return len(claim_tokens & passage_tokens) / len(claim_tokens)
