"""FastAPI authentication dependencies."""

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.security import Principal, authenticate_api_key
from app.config import get_settings
from app.database import get_session

DbSessionDep = Annotated[Session, Depends(get_session)]


def require_principal(
    request: Request,
    session: DbSessionDep,
    key: Annotated[str | None, Header(alias="X-BoursaMind-RAG-Key")] = None,
    compatibility_key: Annotated[str | None, Header(alias="X-BoursaMind-Key")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> Principal:
    raw = key or compatibility_key
    if raw is None and authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    if raw is None:
        raise HTTPException(status_code=401, detail="Missing BoursaMind-RAG API key.")

    principal = authenticate_api_key(session, raw)
    if principal is None:
        raise HTTPException(status_code=403, detail="Invalid or revoked API key.")
    request.state.tenant_id = principal.tenant.id
    request.state.api_key_id = principal.api_key.id if principal.api_key else None
    return principal


PrincipalDep = Annotated[Principal, Depends(require_principal)]


def require_scope(scope: str):
    """Enforce explicit key restrictions; null retains legacy full-tenant access."""
    def scoped_principal(principal: PrincipalDep) -> Principal:
        scopes = principal.api_key.scopes if principal.api_key else None
        if scopes is not None and scope not in scopes:
            raise HTTPException(status_code=403, detail="API key lacks the required scope.")
        return principal

    return scoped_principal


QueryPrincipalDep = Annotated[Principal, Depends(require_scope("query:read"))]
WritePrincipalDep = Annotated[Principal, Depends(require_scope("documents:write"))]
UsagePrincipalDep = Annotated[Principal, Depends(require_scope("usage:read"))]


def require_admin(
    admin_key: Annotated[str | None, Header(alias="X-BoursaMind-RAG-Admin-Key")] = None,
) -> None:
    settings = get_settings()
    if not settings.admin_key:
        raise HTTPException(status_code=403, detail="Admin API is disabled.")
    if not admin_key or not hmac.compare_digest(admin_key.encode(), settings.admin_key.encode()):
        raise HTTPException(status_code=403, detail="Invalid admin key.")


AdminDep = Annotated[None, Depends(require_admin)]


def require_platform_principal(
    request: Request,
    principal: QueryPrincipalDep,
) -> Principal:
    """Restrict server-owned connectors and user impersonation to the platform."""
    settings = get_settings()
    authorization = request.headers.get("Authorization", "")
    raw = (
        request.headers.get("X-BoursaMind-RAG-Key")
        or request.headers.get("X-BoursaMind-Key")
        or (authorization[7:].strip() if authorization.lower().startswith("bearer ") else "")
    )
    if (
        not settings.bootstrap_api_key
        or not hmac.compare_digest(raw.encode(), settings.bootstrap_api_key.encode())
        or principal.tenant.slug != settings.bootstrap_tenant_slug
    ):
        raise HTTPException(403, "Platform user scope requires the platform service credential.")
    return principal


PlatformPrincipalDep = Annotated[Principal, Depends(require_platform_principal)]


def require_boursamind_user(
    _principal: PlatformPrincipalDep,
    user_id: Annotated[str | None, Header(alias="X-BoursaMind-User-Id")] = None,
) -> str:
    if user_id is None or not user_id.strip():
        raise HTTPException(status_code=401, detail="Missing BoursaMind user scope.")
    if len(user_id) > 128:
        raise HTTPException(401, "Invalid BoursaMind user scope.")
    return user_id.strip()


BoursaMindUserDep = Annotated[str, Depends(require_boursamind_user)]
