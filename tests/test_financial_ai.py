import os

os.environ.setdefault("BOURSAMIND_RAG_ENVIRONMENT", "test")
os.environ.setdefault("BOURSAMIND_RAG_DATABASE_URL", "sqlite:///./var/test_boursamind_rag.db")
os.environ.setdefault("BOURSAMIND_RAG_BOOTSTRAP_API_KEY", "test-boursamind-rag-key")
os.environ.setdefault("BOURSAMIND_RAG_ADMIN_KEY", "test-admin-key")
os.environ.setdefault("BOURSAMIND_RAG_EMBEDDING_DIM", "128")

import pytest
from fastapi.testclient import TestClient

from app.auth.security import ensure_bootstrap_tenant
from app.database import SessionLocal, init_db
from app.main import app

API_KEY = "test-boursamind-rag-key"
HEADERS = {"X-BoursaMind-RAG-Key": API_KEY, "X-BoursaMind-User-Id": "alice"}
BOB_HEADERS = {"X-BoursaMind-RAG-Key": API_KEY, "X-BoursaMind-User-Id": "bob"}


@pytest.fixture(autouse=True)
def clean_database():
    init_db(drop=True)
    with SessionLocal() as session:
        ensure_bootstrap_tenant(session)
        session.commit()
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_investor_memory_is_user_scoped_and_mutable(client):
    created = client.post(
        "/v1/financial/memory",
        headers=HEADERS,
        json={
            "memory_type": "THESIS",
            "ticker": "SIAME",
            "source_event": "decision-room:1",
            "summary": "Alice considered SIAME support near 4.100 as her own analysis.",
            "evidence": [{"type": "USER_ANALYSIS", "source": "Decision Room"}],
            "confidence": 70,
            "importance": 80,
        },
    )
    assert created.status_code == 201
    memory_id = created.json()["id"]

    assert client.get("/v1/financial/memory?ticker=SIAME", headers=HEADERS).json()[0][
        "summary"
    ].startswith("Alice")
    assert client.get("/v1/financial/memory?ticker=SIAME", headers=BOB_HEADERS).json() == []

    edited = client.patch(
        f"/v1/financial/memory/{memory_id}",
        headers=HEADERS,
        json={"summary": "Corrected user-visible SIAME thesis.", "importance": 90},
    )
    assert edited.status_code == 200
    assert edited.json()["is_user_edited"] is True
    blocked = client.delete(f"/v1/financial/memory/{memory_id}", headers=BOB_HEADERS)
    assert blocked.status_code == 404


def test_financial_query_separates_documents_structured_context_and_memory(client):
    client.post(
        "/v1/documents",
        headers=HEADERS,
        json={
            "title": "SIAME official publication",
            "text": (
                "SIAME official BVMT publication reports management discussion "
                "for fiscal year 2024."
            ),
            "document_type": "OFFICIAL_PUBLICATION",
            "metadata": {
                "domain": "boursamind",
                "ticker": "SIAME",
                "source": "BVMT",
                "is_official": True,
                "publication_date": "2025-04-30",
            },
            "index_now": True,
        },
    )
    client.post(
        "/v1/financial/memory",
        headers=HEADERS,
        json={
            "memory_type": "TRADE",
            "ticker": "SIAME",
            "source_event": "paper-trade:buy:1",
            "summary": "Alice simulated a SIAME buy after a strong move.",
            "evidence": [{"type": "SIMULATION_RESULT"}],
        },
    )

    response = client.post(
        "/v1/financial/query",
        headers=HEADERS,
        json={
            "question": "Should I reconsider SIAME after my paper trade?",
            "ticker": "SIAME",
            "structured_context": {
                "market": {"as_of": "2026-08-13", "close": 4.12, "source": "BVMT"},
                "paper_trading": {"simulation": True, "open_orders": 0},
            },
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["intent"] == "PAPER_TRADE_CONTEXT"
    assert "OFFICIAL_FACT" in body["evidence_types"]
    assert "SIMULATION_RESULT" in body["evidence_types"]
    assert "USER_MEMORY" in body["evidence_types"]
    assert body["context"]["user_memory"][0]["user_id"] == "alice"
    assert "Intent:" not in body["answer"]
    assert "Official market data" not in body["answer"]
    assert "### Analyse de SIAME" in body["answer"]
    assert "Cours de clôture" in body["answer"]
    assert "4,120 TND" in body["answer"]
    assert "ne constitue ni une recommandation" in body["answer"]


def test_market_only_analysis_is_clear_about_its_limits(client):
    response = client.post(
        "/v1/financial/query",
        headers=HEADERS,
        json={
            "question": "Analyse BIAT",
            "ticker": "BIAT",
            "structured_context": {
                "company": {"ticker": "BIAT", "name": "BIAT", "sector": "BANQUES"},
                "market": {
                    "session_date": "2026-09-28",
                    "close": 158.810,
                    "previous_close": 158.850,
                    "variation_pct": -0.03,
                    "volume": 96,
                    "avg_volume_20": 3830.8,
                    "volume_ratio": 0.03,
                    "turnover": 15247.010,
                    "source": "bulletin:2026-09-28",
                },
                "market_history": {
                    "period_performance_pct": 2.4,
                    "ma20": 157.2,
                    "ma50": 154.8,
                    "points": [
                        {"session_date": "2026-07-01", "close": 155.1},
                        {"session_date": "2026-09-28", "close": 158.81},
                    ],
                },
                "liquidity": {
                    "range": "6M",
                    "score": 28.0,
                    "label_fr": "Faible",
                    "average_daily_turnover": 608400.0,
                },
            },
        },
    )

    assert response.status_code == 200
    answer = response.json()["answer"]
    assert "### Analyse de BIAT" in answer
    assert "158,810 TND" in answer
    assert "96 titres" in answer
    assert "3,0 %" in answer
    assert "2,40 %" in answer
    assert "28,0/100 — Faible" in answer
    assert "La participation est très faible" in answer
    assert "À vérifier avant toute décision" in answer
    assert "produit net bancaire" in answer
    assert "Aucune publication officielle" in answer
    assert "relevance threshold" not in answer
    assert "COMPANY_ANALYSIS" not in answer


def test_smart_ticker_does_not_collide_with_sma_indicator():
    from app.financial.intent import classify_financial_intent
    from app.financial.tools import select_tools

    intent = classify_financial_intent("Analyse financière complète de SMART")

    assert intent == "COMPANY_ANALYSIS"
    assert {tool.name for tool in select_tools(intent)} == {
        "get_company",
        "get_market_snapshot",
        "get_market_history",
        "get_technical_indicators",
        "get_financial_ratios",
        "get_liquidity",
    }


def test_sma_as_a_separate_word_still_selects_technical_analysis():
    from app.financial.intent import classify_financial_intent

    assert classify_financial_intent("Affiche la SMA de SMART") == "TECHNICAL_ANALYSIS"


def test_financial_query_has_no_evidence_behavior(client):
    response = client.post(
        "/v1/financial/query",
        headers=HEADERS,
        json={"question": "Guarantee which Tunisian stock will rise tomorrow."},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["abstained"] is True
    assert "Désolé, je ne trouve pas actuellement assez de données vérifiables" in body["answer"]


def test_other_tenant_key_cannot_forge_platform_user_scope(client):
    tenant = client.post("/v1/tenants", headers={"X-BoursaMind-RAG-Admin-Key": "test-admin-key"},
                         json={"slug": "attacker", "name": "Other tenant"})
    assert tenant.status_code == 201
    response = client.get("/v1/financial/memory", headers={
        "X-BoursaMind-RAG-Key": tenant.json()["api_key"], "X-BoursaMind-User-Id": "alice"})
    assert response.status_code == 403


def test_nonofficial_document_is_not_promoted_to_official_evidence(client):
    created = client.post("/v1/documents", headers=HEADERS, json={
        "title": "SIAME analyst opinion",
        "text": "SIAME analyst opinion discusses management outlook and activity in 2024.",
        "metadata": {"domain": "boursamind", "ticker": "SIAME", "is_official": False},
    })
    assert created.status_code == 201
    response = client.post("/v1/financial/query", headers=HEADERS, json={
        "question": "What is the SIAME analyst opinion on management outlook?", "ticker": "SIAME",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["context"]["documents"]
    assert "DOCUMENT_EVIDENCE" in body["evidence_types"]
    assert "OFFICIAL_DOCUMENT" not in body["evidence_types"]
    assert "Official document evidence" not in body["answer"]


@pytest.mark.parametrize("payload", [None, [], "invalid", {"unexpected": True}])
def test_malformed_structured_provider_degrades_without_breaking_evidence(monkeypatch, payload):
    import httpx

    from app.config import get_settings
    from app.financial.tools import fetch_live_tool_calls

    settings = get_settings()
    monkeypatch.setattr(settings, "boursamind_api_url", "http://backend.test")
    monkeypatch.setattr(settings, "boursamind_service_key", "test-bridge-key")
    monkeypatch.setattr("app.financial.tools.httpx.post", lambda *_a, **_kw: httpx.Response(
        200, json=payload, request=httpx.Request("POST", "http://backend.test")
    ))
    calls = fetch_live_tool_calls("DOCUMENT_QUESTION", user_id="alice", ticker="SIAME", supplied={})
    assert calls
    assert all(call.status == "NOT_AVAILABLE" for call in calls)
    assert all(call.limitation for call in calls)


def test_structured_provider_rate_limit_is_visible_without_leaking_response(monkeypatch, caplog):
    import httpx

    from app.config import get_settings
    from app.financial.tools import fetch_live_tool_calls

    settings = get_settings()
    monkeypatch.setattr(settings, "boursamind_api_url", "http://backend.test")
    monkeypatch.setattr(settings, "boursamind_service_key", "test-bridge-key")
    response = httpx.Response(
        429,
        text="private upstream detail",
        request=httpx.Request("POST", "http://backend.test"),
    )
    monkeypatch.setattr(
        "app.financial.tools.httpx.post",
        lambda *_a, **_kw: response,
    )

    with caplog.at_level("WARNING", logger="app.financial.tools"):
        calls = fetch_live_tool_calls(
            "DOCUMENT_QUESTION", user_id="alice", ticker="SIAME", supplied={}
        )

    assert calls and all(call.status == "NOT_AVAILABLE" for call in calls)
    assert "private upstream detail" not in caplog.text
    records = [record for record in caplog.records if record.message.endswith("bridge failed")]
    assert records
    assert all(record.upstream_status_code == 429 for record in records)
