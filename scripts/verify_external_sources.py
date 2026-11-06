from __future__ import annotations

import json
from collections import Counter
from typing import Any

from fastapi.testclient import TestClient

from app.auth.security import ensure_bootstrap_tenant
from app.config import get_settings
from app.database import SessionLocal, init_db
from app.main import app
from app.models import ExternalEvent


def summarize_response(value: Any) -> Any:
    if isinstance(value, list):
        return {"count": len(value), "first": sanitize(value[0]) if value else None}
    if isinstance(value, dict):
        redacted = sanitize(value)
        if "events" in redacted and isinstance(redacted["events"], list):
            redacted["events"] = {
                "count": len(redacted["events"]),
                "first": redacted["events"][0] if redacted["events"] else None,
            }
        return redacted
    return value


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: sanitize(item)
            for key, item in value.items()
            if "token" not in key.casefold() and "secret" not in key.casefold()
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value[:3]]
    return value


def call(client: TestClient, path: str, headers: dict[str, str]) -> dict[str, Any]:
    response = client.get(path, headers=headers)
    try:
        body = response.json()
    except ValueError:
        body = response.text[:500]
    return {
        "path": path,
        "status_code": response.status_code,
        "body": summarize_response(body),
        "token_in_response": _configured_token_in(json.dumps(body, default=str)),
    }


def _configured_token_in(text: str) -> bool:
    token = get_settings().meta_page_access_token.strip()
    return bool(token and token in text)


def db_summary() -> dict[str, Any]:
    with SessionLocal() as session:
        rows = (
            session.query(ExternalEvent)
            .order_by(ExternalEvent.collected_at.desc(), ExternalEvent.id.desc())
            .limit(100)
            .all()
        )
        counts = Counter(row.source for row in rows)
        samples = []
        token_in_raw_data = False
        for row in rows[:12]:
            raw_text = json.dumps(row.raw_data or {}, default=str)
            token_in_raw_data = token_in_raw_data or _configured_token_in(raw_text)
            samples.append(
                {
                    "id": row.id,
                    "tenant_id": row.tenant_id,
                    "source": row.source,
                    "event_type": row.event_type,
                    "external_id": row.external_id,
                    "metric": row.metric,
                    "indicator": row.indicator,
                    "value": row.value,
                    "observed_at": row.observed_at.isoformat() if row.observed_at else None,
                    "published_at": row.published_at.isoformat() if row.published_at else None,
                }
            )
        return {
            "counts": dict(counts),
            "samples": samples,
            "token_in_raw_data": token_in_raw_data,
        }


def main() -> None:
    settings = get_settings()
    init_db()
    with SessionLocal() as session:
        ensure_bootstrap_tenant(session)
        session.commit()

    headers = {settings.api_key_header: settings.bootstrap_api_key}
    paths = [
        "/v1/external/news?query=Tunisair&timespan=7d&maxrecords=10",
        "/v1/external/news?query=Tunisair&timespan=7d&maxrecords=10",
        "/v1/external/ins",
        "/v1/external/ins",
        "/v1/external/meta/page",
        "/v1/external/meta/posts?limit=25",
        "/v1/data-sources/status",
    ]
    with TestClient(app) as client:
        results = [call(client, path, headers) for path in paths]
    print(json.dumps({"requests": results, "database": db_summary()}, indent=2, default=str))


if __name__ == "__main__":
    main()
