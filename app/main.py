"""Standalone FastAPI application for BoursaMind-RAG."""

import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.responses import JSONResponse

from app.api.v1 import router as v1_router
from app.auth.security import ensure_bootstrap_tenant
from app.config import get_settings
from app.database import SessionLocal, init_db
from app.observability import (
    configure_logging,
    correlation_id_context,
    log_event,
    metrics,
    request_id_context,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    issues = settings.production_config_issues() if settings.is_production else []
    if issues:
        raise RuntimeError("Insecure RAG production configuration: " + "; ".join(issues))
    log_event(
        logging.getLogger(__name__),
        logging.INFO,
        "service_starting",
        environment=settings.environment,
    )
    if settings.create_tables_on_startup:
        init_db()
    # Bootstrap is idempotent and runs only after the deployment migration gate.
    # In production create_tables_on_startup is false, so a missing/incompatible
    # schema fails here instead of being silently synthesized from ORM metadata.
    with SessionLocal() as session:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext('boursamind-rag-bootstrap'))")
            )
        ensure_bootstrap_tenant(session)
        session.commit()
    log_event(
        logging.getLogger(__name__),
        logging.INFO,
        "service_started",
        environment=settings.environment,
    )
    try:
        yield
    finally:
        log_event(logging.getLogger(__name__), logging.INFO, "service_shutdown_completed")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging("DEBUG" if settings.debug else "INFO")
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        # Tracebacks may contain provider secrets or private document text.
        # Development verbosity belongs in server logs, never HTTP responses.
        debug=False,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.environment == "development" else [],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=[
            "Authorization",
            "X-Request-ID",
            "X-Correlation-ID",
            settings.api_key_header,
            settings.compatibility_api_key_header,
        ],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            {
                "detail": [
                    {"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()
                ]
            },
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def server_error(request, exc):
        route = getattr(request.scope.get("route"), "path", None) or "unmatched"
        metrics.increment("unhandled_exceptions_total", route=route, error_type=type(exc).__name__)
        # Provider exceptions can embed documents or credentials in their text.
        # Correlation, route and exception type are sufficient for safe triage.
        log_event(
            logging.getLogger("app.error"),
            logging.ERROR,
            "unhandled_request_error",
            method=request.method,
            route=route,
            error_type=type(exc).__name__,
        )
        return JSONResponse(
            {
                "detail": "Internal server error",
                "request_id": getattr(request.state, "request_id", None),
            },
            status_code=500,
        )

    @app.middleware("http")
    async def request_observability(request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request_id):
            request_id = uuid.uuid4().hex
        correlation_id = request.headers.get("x-correlation-id") or request_id
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", correlation_id):
            correlation_id = request_id
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id
        request_token = request_id_context.set(request_id)
        correlation_token = correlation_id_context.set(correlation_id)
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Correlation-ID"] = correlation_id
            return response
        finally:
            duration = time.perf_counter() - started
            route = getattr(request.scope.get("route"), "path", None) or "unmatched"
            status_class = f"{status_code // 100}xx"
            metrics.increment(
                "http_requests_total", method=request.method, route=route, status_class=status_class
            )
            metrics.observe(
                "http_request_duration_seconds", duration, method=request.method, route=route
            )
            if not route.startswith("/v1/health"):
                log_event(
                    logging.getLogger("app.http"),
                    logging.WARNING if status_code >= 400 or duration >= 2 else logging.INFO,
                    "http_request_completed",
                    method=request.method,
                    route=route,
                    status_code=status_code,
                    duration_ms=round(duration * 1000, 2),
                )
            request_id_context.reset(request_token)
            correlation_id_context.reset(correlation_token)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response

    app.include_router(v1_router)

    @app.get("/")
    def root() -> dict:
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "health": "/v1/health",
        }

    return app


app = create_app()
