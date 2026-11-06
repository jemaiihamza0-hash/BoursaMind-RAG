"""Provider collectors and normalized persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.external_sources.clients import GdeltClient, InsClient, MetaGraphClient, OpenMeteoClient
from app.external_sources.models import ExternalEventCreate
from app.models import ExternalEvent


def persist_events(
    session: Session, tenant_id: int, events: list[dict[str, Any]]
) -> list[ExternalEvent]:
    persisted: list[ExternalEvent] = []
    for raw in events:
        payload = ExternalEventCreate(tenant_id=tenant_id, **raw)
        query = (
            select(ExternalEvent).where(
                ExternalEvent.tenant_id == tenant_id,
                ExternalEvent.source == payload.source,
                ExternalEvent.external_id == payload.external_id,
            )
            if payload.external_id
            else None
        )
        existing = session.execute(query).scalar_one_or_none() if query is not None else None
        if existing:
            persisted.append(existing)
            continue
        row = ExternalEvent(**payload.model_dump(), collected_at=datetime.now(UTC))
        session.add(row)
        persisted.append(row)
    session.commit()
    for row in persisted:
        session.refresh(row)
    return persisted


def collect_weather(
    session: Session,
    tenant_id: int,
    client: OpenMeteoClient,
    locations: list[dict[str, Any]],
    forecast_days: int = 3,
) -> list[ExternalEvent]:
    events = []
    for location in locations:
        payload = client.forecast(
            latitude=float(location["latitude"]),
            longitude=float(location["longitude"]),
            location=str(location["name"]),
            forecast_days=forecast_days,
        )
        events.extend(client.normalize(payload))
    return persist_events(session, tenant_id, events)


def collect_news(
    session: Session,
    tenant_id: int,
    client: GdeltClient,
    query: str,
    *,
    company: str | None = None,
    timespan: str = "7d",
    maxrecords: int = 50,
) -> list[ExternalEvent]:
    payload = client.search(query, timespan=timespan, maxrecords=maxrecords)
    return persist_events(session, tenant_id, client.normalize(payload, company=company))


def collect_ins(
    session: Session,
    tenant_id: int,
    client: InsClient,
    path: str,
    indicator: str,
    params: dict[str, Any] | None = None,
) -> list[ExternalEvent]:
    payload = client.fetch(path, params=params)
    return persist_events(session, tenant_id, client.normalize(payload, indicator=indicator))


def collect_meta_posts(
    session: Session,
    tenant_id: int,
    client: MetaGraphClient,
    *,
    limit: int = 25,
) -> list[ExternalEvent]:
    payload = client.get_page_posts(limit=limit)
    return persist_events(session, tenant_id, client.normalize_posts(payload))
