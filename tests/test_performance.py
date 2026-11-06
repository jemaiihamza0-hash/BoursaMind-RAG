"""Deterministic regression protections for retrieval hot paths."""

from __future__ import annotations

from app.config import Settings
from app.embeddings import providers


def test_cosine_similarity_reuses_precomputed_query_norm(monkeypatch):
    original_vector_norm = providers.vector_norm
    calls: list[list[float]] = []

    def counted_vector_norm(vector):
        calls.append(vector)
        return original_vector_norm(vector)

    monkeypatch.setattr(providers, "vector_norm", counted_vector_norm)
    query = [3.0, 4.0]
    candidate = [6.0, 8.0]

    assert providers.cosine_similarity(query, candidate, norm_a=5.0) == 1.0
    assert calls == [candidate]


def test_candidate_pool_defaults_leave_headroom_for_top_k():
    settings = Settings(_env_file=None)

    assert settings.retrieval_candidate_pool == 600
    assert settings.retrieval_candidate_pool >= settings.max_top_k
