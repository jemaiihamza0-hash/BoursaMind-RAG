from app.retrieval import scoring


def meta(period: str, frequency: str, *, version: int = 1, **extra):
    return {
        "reporting_period": period,
        "reporting_frequency": frequency,
        "document_version": version,
        **extra,
    }


def test_latest_annual_prefers_latest_reporting_period_not_filing_age():
    intent = scoring.temporal_intent("latest annual revenue and net income")
    candidates = [meta("2024", "ANNUAL"), meta("2026", "ANNUAL")]
    latest = max(scoring.period_rank(row, intent) for row in candidates)
    assert scoring.temporal_relevance(candidates[1], intent, latest) == 1.0
    assert scoring.temporal_relevance(candidates[0], intent, latest) < 1.0


def test_latest_quarter_ignores_newer_annual_document():
    intent = scoring.temporal_intent("latest quarterly revenue")
    annual = meta("2026", "ANNUAL")
    quarter = meta("2025-Q4", "QUARTERLY")
    latest = scoring.period_rank(quarter, intent)
    assert scoring.period_rank(annual, intent) is None
    assert scoring.temporal_relevance(quarter, intent, latest) == 1.0


def test_latest_interim_understands_half_year_periods():
    intent = scoring.temporal_intent("derniers résultats semestriels")
    old = meta("2025-H1", "SEMI_ANNUAL")
    new = meta("2026-H1", "SEMI_ANNUAL")
    latest = max(scoring.period_rank(row, intent) for row in (old, new))
    assert scoring.temporal_relevance(new, intent, latest) == 1.0
    assert scoring.temporal_relevance(old, intent, latest) < 1.0


def test_historical_comparison_does_not_apply_latest_boost():
    intent = scoring.temporal_intent("compare revenue in 2024 and 2025")
    assert intent.latest is False
    assert scoring.temporal_relevance(meta("2025", "ANNUAL"), intent, None) == 0.0


def test_revised_document_version_wins_same_period_and_superseded_is_penalized():
    intent = scoring.temporal_intent("latest annual results")
    original = meta("2026", "ANNUAL", version=1, is_superseded=True)
    revised = meta("2026", "ANNUAL", version=2)
    latest = max(scoring.period_rank(row, intent) for row in (original, revised))
    assert scoring.temporal_relevance(revised, intent, latest) == 1.0
    assert scoring.temporal_relevance(original, intent, latest) < 1.0
    assert scoring.is_superseded(original) is True
