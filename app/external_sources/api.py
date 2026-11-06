"""Authenticated development endpoints for external data collection."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.auth.dependencies import DbSessionDep, PlatformPrincipalDep, QueryPrincipalDep
from app.config import get_settings
from app.external_sources.clients import (
    ExternalSourceError,
    GdeltClient,
    InsClient,
    MetaGraphClient,
)
from app.external_sources.collectors import (
    collect_ins,
    collect_meta_posts,
    collect_news,
)
from app.external_sources.models import ExternalEventOut, MetaPageOut, MetaPostsOut
from app.models import ExternalEvent

logger = logging.getLogger(__name__)
router = APIRouter()


def _clients() -> tuple[InsClient, GdeltClient]:
    settings = get_settings()
    options = {
        "timeout": settings.external_http_timeout_seconds,
        "max_retries": settings.external_http_max_retries,
    }
    return (
        InsClient(settings.ins_base_url, **options),
        GdeltClient(settings.gdelt_base_url, **options),
    )


def _provider_error(provider: str, exc: ExternalSourceError) -> HTTPException:
    logger.warning("External provider %s unavailable (%s)", provider, type(exc).__name__)
    status_code = 503 if exc.status == "not_configured" else 502
    return HTTPException(
        status_code=status_code,
        detail={
            "provider": provider,
            "status": exc.status,
            "error": "External provider unavailable",
        },
    )


def _meta_client() -> MetaGraphClient:
    settings = get_settings()
    return MetaGraphClient(
        settings.meta_graph_base_url,
        version=settings.meta_graph_version,
        page_id=settings.meta_page_id,
        page_access_token=settings.meta_page_access_token,
        granted_permissions=settings.meta_permissions,
        timeout=settings.external_http_timeout_seconds,
        max_retries=settings.external_http_max_retries,
    )


def _meta_error(exc: ExternalSourceError) -> HTTPException:
    status_code = {
        "not_configured": 503,
        "authentication_error": 401,
        "permission_missing": 403,
        "invalid_response": 502,
        "rate_limited": 429,
        "provider_unavailable": 502,
    }.get(exc.status, 502)
    detail: dict[str, Any] = {"status": exc.status, "provider": "meta"}
    if exc.required_permission:
        detail["required_permission"] = exc.required_permission
    return HTTPException(status_code=status_code, detail=detail)


@router.get("/data-sources/status")
def data_sources_status(_principal: PlatformPrincipalDep) -> dict[str, Any]:
    settings = get_settings()
    ins, news = _clients()
    checks: dict[str, Any] = {}

    ins_params = {"resource_id": settings.ins_resource_id, "limit": settings.ins_limit}
    if not settings.ins_query_path or not settings.ins_indicator or not settings.ins_resource_id:
        checks["ins"] = "not_configured"
    else:
        try:
            payload = ins.fetch(settings.ins_query_path, params=ins_params)
            if ins.normalize(payload, indicator=settings.ins_indicator):
                checks["ins"] = "available"
            else:
                checks["ins"] = "invalid_response"
        except ExternalSourceError as exc:
            checks["ins"] = exc.status
        except (TypeError, ValueError):
            checks["ins"] = "invalid_response"

    try:
        news.search("Tunisia", timespan="1d", maxrecords=1)
        checks["gdelt"] = "available"
    except ExternalSourceError as exc:
        checks["gdelt"] = exc.status

    meta = _meta_client()
    if not meta.configured:
        checks["meta"] = {
            "configured": False,
            "status": "not_configured",
            "capabilities": meta.capabilities(),
            "permissions": meta.permission_statuses(),
        }
    else:
        try:
            page = meta.get_page_info()
            checks["meta"] = {
                "configured": True,
                "status": "available",
                "page_id": page["id"],
                "page_name": page["name"],
                "capabilities": meta.capabilities(page_metadata_verified=True),
                "permissions": meta.permission_statuses(),
            }
        except ExternalSourceError as exc:
            checks["meta"] = {
                "configured": True,
                "status": exc.status,
                "capabilities": meta.capabilities(),
                "permissions": meta.permission_statuses(),
            }
    return checks


@router.get("/external/meta/page", response_model=MetaPageOut)
def external_meta_page(_principal: PlatformPrincipalDep) -> MetaPageOut:
    client = _meta_client()
    try:
        page = client.get_page_info()
    except ExternalSourceError as exc:
        raise _meta_error(exc) from exc
    return MetaPageOut(
        page_id=str(page["id"]),
        name=str(page["name"]),
        category=page.get("category"),
        capabilities=client.capabilities(page_metadata_verified=True),
        permissions=client.permission_statuses(),
    )


@router.get("/external/meta/posts", response_model=MetaPostsOut)
def external_meta_posts(
    principal: PlatformPrincipalDep,
    session: DbSessionDep,
    limit: int = Query(default=25, ge=1, le=100),
) -> MetaPostsOut:
    client = _meta_client()
    try:
        events = collect_meta_posts(session, principal.tenant.id, client, limit=limit)
    except ExternalSourceError as exc:
        if exc.status == "permission_missing":
            return MetaPostsOut(
                status=exc.status,
                required_permission=exc.required_permission or MetaGraphClient.POSTS_PERMISSION,
            )
        raise _meta_error(exc) from exc
    return MetaPostsOut(status="available", events=events)


@router.get("/external/news", response_model=list[ExternalEventOut])
def external_news(
    principal: QueryPrincipalDep,
    session: DbSessionDep,
    query: str = Query(min_length=1, max_length=300),
    company: str | None = Query(default=None, max_length=255),
    timespan: str | None = Query(default=None, pattern=r"^[1-9][0-9]*[mhdw]$"),
    maxrecords: int | None = Query(default=None, ge=1, le=250),
):
    settings = get_settings()
    _ins, client = _clients()
    try:
        return collect_news(
            session,
            principal.tenant.id,
            client,
            query,
            company=company,
            timespan=timespan or settings.gdelt_default_timespan,
            maxrecords=maxrecords or settings.gdelt_default_maxrecords,
        )
    except ExternalSourceError as exc:
        raise _provider_error("gdelt", exc) from exc


@router.get("/external/ins", response_model=list[ExternalEventOut])
def external_ins(principal: QueryPrincipalDep, session: DbSessionDep):
    settings = get_settings()
    if not settings.ins_query_path or not settings.ins_indicator:
        raise HTTPException(
            status_code=503,
            detail="Configure BOURSAMIND_RAG_INS_QUERY_PATH and BOURSAMIND_RAG_INS_INDICATOR.",
        )
    params = (
        {"resource_id": settings.ins_resource_id, "limit": settings.ins_limit}
        if settings.ins_resource_id
        else None
    )
    client, _news = _clients()
    try:
        return collect_ins(
            session,
            principal.tenant.id,
            client,
            settings.ins_query_path,
            settings.ins_indicator,
            params=params,
        )
    except ExternalSourceError as exc:
        raise _provider_error("ins", exc) from exc


@router.get("/external/events", response_model=list[ExternalEventOut])
def external_events(
    principal: QueryPrincipalDep,
    session: DbSessionDep,
    source: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=100, ge=1, le=500),
):
    query = select(ExternalEvent).where(ExternalEvent.tenant_id == principal.tenant.id)
    if source:
        query = query.where(ExternalEvent.source == source)
    return (
        session.execute(
            query.order_by(ExternalEvent.collected_at.desc(), ExternalEvent.id.desc()).limit(limit)
        )
        .scalars()
        .all()
    )
