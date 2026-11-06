import json
import logging
import os

os.environ.setdefault("BOURSAMIND_RAG_ENVIRONMENT", "test")
os.environ.setdefault("BOURSAMIND_RAG_DATABASE_URL", "sqlite:///./var/test_boursamind_rag.db")
os.environ.setdefault("BOURSAMIND_RAG_BOOTSTRAP_API_KEY", "test-boursamind-rag-key")
os.environ.setdefault("BOURSAMIND_RAG_ADMIN_KEY", "test-admin-key")

from fastapi.testclient import TestClient

from app.main import app
from app.observability import JsonFormatter, Metrics, redact


def test_rag_request_correlation_and_admin_metrics():
    with TestClient(app) as client:
        response = client.get("/v1/health/live", headers={"X-Correlation-ID": "rag-flow-7"})
        assert response.headers["X-Correlation-ID"] == "rag-flow-7"
        assert response.headers["X-Request-ID"]
        assert client.get("/v1/metrics").status_code == 403
        metrics = client.get(
            "/v1/metrics", headers={"X-BoursaMind-RAG-Admin-Key": "test-admin-key"}
        )
        assert metrics.status_code == 200
        assert set(metrics.json()) == {"counters", "durations"}


def test_rag_redaction_and_structured_logging():
    assert redact({"api_key": "secret", "nested": {"cookie": "secret"}}) == {
        "api_key": "[REDACTED]",
        "nested": {"cookie": "[REDACTED]"},
    }
    record = logging.LogRecord("rag", logging.WARNING, __file__, 1, "failure", (), None)
    record.fields = {"token": "secret", "provider": "local"}
    rendered = json.loads(JsonFormatter().format(record))
    assert rendered["token"] == "[REDACTED]"
    assert rendered["provider"] == "local"


def test_rag_metrics_fail_open(monkeypatch):
    registry = Metrics()

    class BrokenLock:
        def __enter__(self):
            raise RuntimeError("metrics unavailable")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(registry, "_lock", BrokenLock())
    registry.increment("query", result="success")
    registry.observe("duration", 0.1, result="success")
