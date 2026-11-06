from __future__ import annotations

import json
import os

os.environ.setdefault("BOURSAMIND_RAG_ENVIRONMENT", "test")
os.environ.setdefault("BOURSAMIND_RAG_DATABASE_URL", "sqlite:///./var/test_boursamind_rag.db")
os.environ.setdefault("BOURSAMIND_RAG_BOOTSTRAP_API_KEY", "test-boursamind-rag-key")
os.environ.setdefault("BOURSAMIND_RAG_ADMIN_KEY", "test-admin-key")
os.environ.setdefault("BOURSAMIND_RAG_EMBEDDING_DIM", "128")

import httpx
import pytest
from fastapi.testclient import TestClient

from app.auth.security import ensure_bootstrap_tenant
from app.config import get_settings
from app.database import SessionLocal, init_db
from app.external_sources import api as api_module
from app.external_sources.clients import ExternalSourceError, MetaGraphClient
from app.external_sources.collectors import persist_events
from app.main import app
from app.models import ExternalEvent

API_KEY = "test-boursamind-rag-key"
HEADERS = {"X-BoursaMind-RAG-Key": API_KEY}
TOKEN = "api-test-token-redacted"


@pytest.fixture(autouse=True)
def clean_database():
    get_settings.cache_clear()
    init_db(drop=True)
    with SessionLocal() as session:
        ensure_bootstrap_tenant(session)
        session.commit()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def graph_client(payload, *, permissions: set[str] | None = None) -> MetaGraphClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    return MetaGraphClient(
        "https://graph.facebook.com",
        version="v26.0",
        page_id="12345",
        page_access_token=TOKEN,
        granted_permissions=permissions or set(),
        max_retries=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def healthy_sources():
    class Ins:
        def fetch(self, _path, *, params=None):
            return {"data": [{"value": 1, "period": "2026"}]}

        def normalize(self, payload, *, indicator):
            return [{"source": "ins", "event_type": "economic_indicator", "value": 1}]

    class Weather:
        def forecast(self, **_kwargs):
            return {"current": {}}

    class News:
        def search(self, *_args, **_kwargs):
            return {"articles": []}

    return Ins(), Weather(), News()


def test_meta_page_endpoint_never_exposes_token(client, monkeypatch):
    meta = graph_client({"id": "12345", "name": "Safe Page", "category": "Finance"})
    monkeypatch.setattr(api_module, "_meta_client", lambda: meta)
    response = client.get("/v1/external/meta/page", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["page_id"] == "12345"
    assert body["connected"] is True
    assert TOKEN not in response.text
    assert "access_token" not in response.text


@pytest.mark.parametrize("path", [
    "/v1/external/meta/page", "/v1/external/meta/posts", "/v1/data-sources/status",
])
def test_other_tenant_cannot_use_platform_meta_connector(client, monkeypatch, path):
    tenant = client.post(
        "/v1/tenants", headers={"X-BoursaMind-RAG-Admin-Key": "test-admin-key"},
        json={"slug": "other-firm", "name": "Other firm"},
    )
    assert tenant.status_code == 201

    def forbidden():
        pytest.fail("Another tenant must be rejected before platform connectors run")

    monkeypatch.setattr(api_module, "_meta_client", forbidden)
    monkeypatch.setattr(api_module, "_clients", forbidden)
    response = client.get(path, headers={"X-BoursaMind-RAG-Key": tenant.json()["api_key"]})
    assert response.status_code == 403


def test_platform_bearer_scheme_is_case_insensitive(client, monkeypatch):
    meta = graph_client({"id": "12345", "name": "Safe Page", "category": "Finance"})
    monkeypatch.setattr(api_module, "_meta_client", lambda: meta)
    response = client.get("/v1/external/meta/page", headers={"Authorization": f"bearer {API_KEY}"})
    assert response.status_code == 200


def test_meta_page_endpoint_missing_configuration(client, monkeypatch):
    meta = MetaGraphClient(
        "https://graph.facebook.com",
        version="v26.0",
        page_id="",
        page_access_token="",
    )
    monkeypatch.setattr(api_module, "_meta_client", lambda: meta)
    response = client.get("/v1/external/meta/page", headers=HEADERS)
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "status": "not_configured",
        "provider": "meta",
    }


def test_status_reports_meta_not_configured(client, monkeypatch):
    meta = MetaGraphClient(
        "https://graph.facebook.com",
        version="v26.0",
        page_id="",
        page_access_token="",
    )
    monkeypatch.setattr(api_module, "_clients", healthy_sources)
    monkeypatch.setattr(api_module, "_meta_client", lambda: meta)
    response = client.get("/v1/data-sources/status", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["meta"]["status"] == "not_configured"
    assert response.json()["meta"]["configured"] is False


def test_status_reports_successful_meta_connection_without_token(client, monkeypatch):
    meta = graph_client({"id": "12345", "name": "Safe Page", "category": "Finance"})
    monkeypatch.setattr(api_module, "_clients", healthy_sources)
    monkeypatch.setattr(api_module, "_meta_client", lambda: meta)
    response = client.get("/v1/data-sources/status", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()["meta"]
    assert body["status"] == "available"
    assert body["page_name"] == "Safe Page"
    assert body["capabilities"]["page_metadata"] == "available"
    assert TOKEN not in response.text


def test_meta_posts_endpoint_reports_missing_permission(client, monkeypatch):
    meta = graph_client({"data": []})
    monkeypatch.setattr(api_module, "_meta_client", lambda: meta)
    response = client.get("/v1/external/meta/posts", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {
        "status": "permission_missing",
        "provider": "meta",
        "required_permission": "pages_read_engagement",
        "events": [],
    }


def test_meta_posts_persist_without_token_and_remain_tenant_scoped(client, monkeypatch):
    meta = graph_client(
        {
            "data": [
                {
                    "id": "12345_44",
                    "message": "Official update",
                    "created_time": "2026-08-26T12:00:00Z",
                    "permalink_url": "https://facebook.example/posts/44",
                    "access_token": TOKEN,
                }
            ]
        },
        permissions={MetaGraphClient.POSTS_PERMISSION},
    )
    monkeypatch.setattr(api_module, "_meta_client", lambda: meta)

    first = client.get("/v1/external/meta/posts", headers=HEADERS)
    second = client.get("/v1/external/meta/posts", headers=HEADERS)
    assert first.status_code == second.status_code == 200
    assert first.json()["events"][0]["id"] == second.json()["events"][0]["id"]
    assert TOKEN not in first.text

    with SessionLocal() as session:
        rows = session.query(ExternalEvent).all()
        assert len(rows) == 1
        assert rows[0].source == "meta"
        assert TOKEN not in json.dumps(rows[0].raw_data)


def test_meta_failure_does_not_break_other_source_statuses(client, monkeypatch):
    class BrokenMeta:
        configured = True

        def capabilities(self):
            return {"page_metadata": "configured_not_tested"}

        def permission_statuses(self):
            return {
                "pages_show_list": "configured",
                "business_management": "configured",
                "pages_read_engagement": "missing",
                "pages_read_user_content": "missing",
            }

        def get_page_info(self):
            raise ExternalSourceError(
                "meta authentication failed",
                status="authentication_error",
            )

    monkeypatch.setattr(api_module, "_clients", healthy_sources)
    monkeypatch.setattr(api_module, "_meta_client", lambda: BrokenMeta())
    monkeypatch.setenv("BOURSAMIND_RAG_INS_QUERY_PATH", "configured/path")
    monkeypatch.setenv("BOURSAMIND_RAG_INS_INDICATOR", "inflation")
    get_settings.cache_clear()

    response = client.get("/v1/data-sources/status", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["ins"] == "available"
    assert body["open_meteo"] == "available"
    assert body["gdelt"] == "available"
    assert body["meta"]["status"] == "authentication_error"
    get_settings.cache_clear()


def test_persistence_deduplicates_meta_posts_per_tenant():
    with SessionLocal() as session:
        tenant = ensure_bootstrap_tenant(session)
        session.commit()
        event = {
            "source": "meta",
            "event_type": "social_post",
            "description": "same post",
            "external_id": "post-duplicate",
            "raw_data": {"id": "post-duplicate"},
        }
        first = persist_events(session, tenant.id, [event])
        second = persist_events(session, tenant.id, [event])
        assert first[0].id == second[0].id
        assert session.query(ExternalEvent).count() == 1
