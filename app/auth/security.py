"""API-key authentication primitives."""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ApiKey, Collection, Tenant, TenantStatus, Workspace


def hash_secret(secret: str) -> str:
    return sha256(secret.encode("utf-8")).hexdigest()


def key_prefix(secret: str) -> str:
    # The textual prefix of generated keys contains only environment + slug for
    # long tenant names, so every key for that tenant used to collide. A digest
    # fingerprint is unique per credential and does not disclose its secret.
    return hash_secret(secret)[:18]


def generate_api_key(*, slug: str, environment: str = "live") -> str:
    token = secrets.token_urlsafe(32)
    return f"bmr_{environment}_{slug}_{token}"


@dataclass(frozen=True)
class Principal:
    tenant: Tenant
    api_key: ApiKey | None = None


def create_api_key(
    session: Session,
    tenant: Tenant,
    *,
    raw_key: str | None = None,
    name: str = "Default API key",
    scopes: list[str] | None = None,
) -> tuple[str, ApiKey]:
    secret = raw_key or generate_api_key(slug=tenant.slug)
    record = ApiKey(
        tenant_id=tenant.id,
        name=name,
        prefix=key_prefix(secret),
        key_hash=hash_secret(secret),
        scopes=scopes,
    )
    session.add(record)
    session.flush()
    return secret, record


def authenticate_api_key(session: Session, raw_key: str) -> Principal | None:
    prefix = key_prefix(raw_key)
    records = (
        session.execute(select(ApiKey).where(ApiKey.prefix.in_([prefix, raw_key[:18]])))
        .scalars()
        .all()
    )
    digest = hash_secret(raw_key)
    for record in records:
        if record.revoked_at is not None:
            continue
        if not hmac.compare_digest(record.key_hash, digest):
            continue
        tenant = session.get(Tenant, record.tenant_id)
        if tenant is None or tenant.status != TenantStatus.ACTIVE:
            return None
        record.last_used_at = datetime.now(UTC)
        session.flush()
        return Principal(tenant=tenant, api_key=record)
    return None


def ensure_default_workspace_and_collection(
    session: Session, tenant: Tenant
) -> tuple[Workspace, Collection]:
    settings = get_settings()
    workspace = (
        session.execute(
            select(Workspace).where(
                Workspace.tenant_id == tenant.id,
                Workspace.slug == "default",
            )
        )
        .scalars()
        .first()
    )
    if workspace is None:
        workspace = Workspace(
            tenant_id=tenant.id,
            slug="default",
            name=settings.default_workspace_name,
        )
        session.add(workspace)
        session.flush()

    collection = (
        session.execute(
            select(Collection).where(
                Collection.tenant_id == tenant.id,
                Collection.workspace_id == workspace.id,
                Collection.slug == "default",
            )
        )
        .scalars()
        .first()
    )
    if collection is None:
        collection = Collection(
            tenant_id=tenant.id,
            workspace_id=workspace.id,
            slug="default",
            name=settings.default_collection_name,
        )
        session.add(collection)
        session.flush()
    return workspace, collection


def ensure_bootstrap_tenant(session: Session) -> Tenant:
    settings = get_settings()
    tenant = (
        session.execute(
            select(Tenant).where(Tenant.slug == settings.bootstrap_tenant_slug)
        )
        .scalars()
        .first()
    )
    if tenant is None:
        tenant = Tenant(
            slug=settings.bootstrap_tenant_slug,
            name=settings.bootstrap_tenant_name,
            status=TenantStatus.ACTIVE,
        )
        session.add(tenant)
        session.flush()

    ensure_default_workspace_and_collection(session, tenant)

    if settings.bootstrap_api_key:
        digest = hash_secret(settings.bootstrap_api_key)
        existing = (
            session.execute(select(ApiKey).where(ApiKey.key_hash == digest))
            .scalars()
            .first()
        )
        if existing is None:
            create_api_key(
                session,
                tenant,
                raw_key=settings.bootstrap_api_key,
                name="Bootstrap service key",
                scopes=["documents:write", "query:read", "usage:read"],
            )
    return tenant
