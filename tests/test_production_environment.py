from app.config import Settings


def _safe(**overrides) -> Settings:
    values = {
        "environment": "production",
        "debug": False,
        "database_url": "postgresql+psycopg2://rag_app:strong@rag-postgres/rag",
        "vector_backend": "database",
        "create_tables_on_startup": False,
        "bootstrap_api_key": "b" * 40,
        "admin_key": "a" * 40,
        "boursamind_service_key": "s" * 40,
        "boursamind_api_url": "http://backend:8000",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_safe_production_environment_has_no_issues() -> None:
    assert _safe().production_config_issues() == []


def test_production_rejects_placeholders_development_credentials_and_unbounded_pool() -> None:
    cases = (
        {"bootstrap_api_key": "REQUIRED_SECRET_REPLACE_WITH_KEY"},
        {"admin_key": "short"},
        {"boursamind_service_key": "REPLACE_WITH_SERVICE_KEY_123456789"},
        {"database_url": "postgresql+psycopg2://boursamind_rag:boursamind_rag@localhost/rag"},
        {"database_pool_size": 0},
        {"database_pool_timeout_seconds": 0},
    )
    for values in cases:
        assert _safe(**values).production_config_issues(), values


def test_single_host_loopback_topology_is_supported() -> None:
    settings = _safe(
        database_url="postgresql+psycopg2://rag_app:strong@127.0.0.1:5432/boursamind_rag",
        boursamind_api_url="http://127.0.0.1:8000",
    )

    assert settings.production_config_issues() == []


def test_rag_preflight_refuses_backend_database() -> None:
    settings = _safe(
        database_url="postgresql+psycopg2://rag_app:strong@127.0.0.1:5432/bvmt_copilot"
    )

    assert any("BoursaMind database" in issue for issue in settings.production_config_issues())


def test_rag_systemd_preflight_requires_production(monkeypatch) -> None:
    from app import production_preflight

    monkeypatch.setattr(production_preflight, "get_settings", lambda: _safe())
    assert production_preflight.preflight() == []

    monkeypatch.setattr(
        production_preflight,
        "get_settings",
        lambda: _safe(environment="development"),
    )
    assert any("ENVIRONMENT" in issue for issue in production_preflight.preflight())


def test_openai_provider_requires_key_but_deterministic_mode_does_not() -> None:
    deterministic = _safe(embedding_provider="deterministic", llm_provider="deterministic")
    assert deterministic.production_config_issues() == []
    assert any(
        "OPENAI_API_KEY" in issue
        for issue in _safe(
            embedding_provider="openai", openai_api_key=""
        ).production_config_issues()
    )


def test_production_rejects_candidate_pool_smaller_than_top_k() -> None:
    settings = _safe(retrieval_candidate_pool=49, max_top_k=50)

    assert "RAG retrieval candidate pool must be at least max_top_k" in (
        settings.production_config_issues()
    )
