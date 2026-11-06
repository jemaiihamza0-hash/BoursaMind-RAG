"""Tenant, workspace, and collection management."""

from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import create_api_key, ensure_default_workspace_and_collection
from app.models import Collection, Tenant, Workspace
from app.schemas import (
    CollectionCreate,
    CollectionOut,
    TenantCreate,
    TenantCreatedOut,
    TenantOut,
    WorkspaceCreate,
    WorkspaceOut,
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return slug or "default"


def tenant_to_out(tenant: Tenant) -> TenantOut:
    return TenantOut(
        id=tenant.id,
        slug=tenant.slug,
        name=tenant.name,
        status=tenant.status,
        contact_email=tenant.contact_email,
        monthly_query_quota=tenant.monthly_query_quota,
        monthly_document_quota=tenant.monthly_document_quota,
        created_at=tenant.created_at,
    )


class TenantService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_tenant(self, payload: TenantCreate) -> TenantCreatedOut:
        existing = (
            self.session.execute(select(Tenant).where(Tenant.slug == payload.slug))
            .scalars()
            .first()
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="Tenant slug already exists.")
        tenant = Tenant(**payload.model_dump())
        self.session.add(tenant)
        self.session.flush()
        ensure_default_workspace_and_collection(self.session, tenant)
        raw_key, key = create_api_key(self.session, tenant, name="Initial API key")
        self.session.commit()
        return TenantCreatedOut(
            tenant=tenant_to_out(tenant),
            api_key=raw_key,
            api_key_prefix=key.prefix,
        )

    def create_workspace(self, tenant: Tenant, payload: WorkspaceCreate) -> WorkspaceOut:
        slug = payload.slug or slugify(payload.name)
        existing = (
            self.session.execute(
                select(Workspace).where(
                    Workspace.tenant_id == tenant.id,
                    Workspace.slug == slug,
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="Workspace slug already exists.")
        workspace = Workspace(
            tenant_id=tenant.id,
            slug=slug,
            name=payload.name,
            settings=payload.settings,
        )
        self.session.add(workspace)
        self.session.commit()
        self.session.refresh(workspace)
        return WorkspaceOut.model_validate(workspace)

    def list_workspaces(self, tenant: Tenant) -> list[WorkspaceOut]:
        rows = (
            self.session.execute(
                select(Workspace).where(Workspace.tenant_id == tenant.id).order_by(Workspace.id)
            )
            .scalars()
            .all()
        )
        return [WorkspaceOut.model_validate(row) for row in rows]

    def create_collection(self, tenant: Tenant, payload: CollectionCreate) -> CollectionOut:
        workspace = self.get_workspace(tenant, payload.workspace_id)
        slug = payload.slug or slugify(payload.name)
        existing = (
            self.session.execute(
                select(Collection).where(
                    Collection.tenant_id == tenant.id,
                    Collection.workspace_id == workspace.id,
                    Collection.slug == slug,
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="Collection slug already exists.")
        collection = Collection(
            tenant_id=tenant.id,
            workspace_id=workspace.id,
            slug=slug,
            name=payload.name,
            description=payload.description,
            metadata_schema=payload.metadata_schema,
        )
        self.session.add(collection)
        self.session.commit()
        self.session.refresh(collection)
        return CollectionOut.model_validate(collection)

    def list_collections(
        self, tenant: Tenant, workspace_id: int | None = None
    ) -> list[CollectionOut]:
        query = select(Collection).where(Collection.tenant_id == tenant.id)
        if workspace_id is not None:
            query = query.where(Collection.workspace_id == workspace_id)
        rows = self.session.execute(query.order_by(Collection.id)).scalars().all()
        return [CollectionOut.model_validate(row) for row in rows]

    def get_workspace(self, tenant: Tenant, workspace_id: int | None) -> Workspace:
        if workspace_id is None:
            workspace, _collection = ensure_default_workspace_and_collection(self.session, tenant)
            return workspace
        workspace = self.session.get(Workspace, workspace_id)
        if workspace is None or workspace.tenant_id != tenant.id:
            raise HTTPException(status_code=404, detail="Workspace not found.")
        return workspace

    def get_collection(
        self,
        tenant: Tenant,
        *,
        workspace_id: int | None,
        collection_id: int | None,
    ) -> tuple[Workspace, Collection]:
        if collection_id is None:
            workspace = self.get_workspace(tenant, workspace_id)
            default = (
                self.session.execute(
                    select(Collection).where(
                        Collection.tenant_id == tenant.id,
                        Collection.workspace_id == workspace.id,
                        Collection.slug == "default",
                    )
                )
                .scalars()
                .first()
            )
            if default is None:
                default = Collection(
                    tenant_id=tenant.id,
                    workspace_id=workspace.id,
                    slug="default",
                    name="Default Collection",
                )
                self.session.add(default)
                self.session.flush()
            return workspace, default

        collection = self.session.get(Collection, collection_id)
        if collection is None or collection.tenant_id != tenant.id:
            raise HTTPException(status_code=404, detail="Collection not found.")
        if workspace_id is not None and collection.workspace_id != workspace_id:
            raise HTTPException(status_code=422, detail="Collection does not belong to workspace.")
        workspace = self.session.get(Workspace, collection.workspace_id)
        if workspace is None or workspace.tenant_id != tenant.id:
            raise HTTPException(status_code=404, detail="Workspace not found.")
        return workspace, collection
