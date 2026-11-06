"""RAG migration graph, PostgreSQL history, and data-preservation checks."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import Column, create_engine, inspect, text
from sqlalchemy.orm import Session

from alembic import command
from app.auth.security import ensure_bootstrap_tenant
from app.database import engine as application_engine
from app.main import app
from app.models import ApiKey, Conversation, Document, Tenant, VisibilityOperation

ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.getenv("TEST_RAG_DATABASE_URL", "")
requires_postgres = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgresql"),
    reason="Set TEST_RAG_DATABASE_URL to a disposable PostgreSQL DSN.",
)


def alembic_config(dsn: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", dsn)
    return config


def drop_all(dsn: str) -> None:
    engine = create_engine(dsn)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    engine.dispose()


@pytest.fixture()
def migration_database() -> str:
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("TEST_RAG_DATABASE_URL not set")
    drop_all(TEST_DATABASE_URL)
    yield TEST_DATABASE_URL
    drop_all(TEST_DATABASE_URL)


def test_rag_migration_graph_is_linear_complete_and_singular() -> None:
    script = ScriptDirectory.from_config(alembic_config("sqlite://"))
    revisions = list(script.walk_revisions())
    ids = [revision.revision for revision in revisions]

    assert script.get_heads() == ["0007_constraint_names"]
    assert script.get_bases() == ["0001_initial_schema"]
    assert len(ids) == len(set(ids)) == 7
    assert all(not isinstance(revision.down_revision, tuple) for revision in revisions)
    assert all(
        revision.down_revision is None or revision.down_revision in ids for revision in revisions
    )
    assert all(Path(revision.path).stem.startswith(revision.revision) for revision in revisions)


@requires_postgres
def test_clean_database_reaches_head_and_matches_critical_orm_contract(
    migration_database: str,
) -> None:
    command.upgrade(alembic_config(migration_database), "head")
    engine = create_engine(migration_database)
    inspector = inspect(engine)

    assert "conversations" in inspector.get_table_names()
    chunk_columns = {column["name"]: column for column in inspector.get_columns("document_chunks")}
    assert chunk_columns["embedding_dimension"]["nullable"] is False
    assert str(chunk_columns["embedding"]["type"]).upper() == "JSONB"
    check_names = {
        constraint["name"] for constraint in inspector.get_check_constraints("document_chunks")
    }
    assert "ck_document_chunks_embedding_dimension_positive" in check_names
    index_names = {index["name"] for index in inspector.get_indexes("document_chunks")}
    assert {
        "ix_chunks_tenant_collection",
        "ix_chunks_metadata",
        "ix_document_chunks_embedding_space",
        "ix_document_chunks_document_id",
    } <= index_names

    with Session(engine) as session:
        ensure_bootstrap_tenant(session)
        session.commit()
        ensure_bootstrap_tenant(session)
        session.commit()
        assert session.query(Tenant).count() == 1
        assert session.query(ApiKey).count() == 1

    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0007_constraint_names"
        )
    engine.dispose()


@requires_postgres
def test_rag_api_and_diagnostics_start_against_migrated_schema(
    migration_database: str,
) -> None:
    assert application_engine.url.render_as_string(hide_password=False) == migration_database
    command.upgrade(alembic_config(migration_database), "head")

    with TestClient(app) as client:
        assert client.get("/v1/health").json()["status"] == "ok"
        headers = {"X-BoursaMind-RAG-Key": os.environ["BOURSAMIND_RAG_BOOTSTRAP_API_KEY"]}
        assert client.get("/v1/status", headers=headers).status_code == 200
        diagnostics = client.get("/v1/diagnostics", headers=headers)
        assert diagnostics.status_code == 200
        assert diagnostics.json()["pending_documents"] == 0


@requires_postgres
def test_query_quota_is_atomic_across_independent_postgres_sessions(
    migration_database: str,
) -> None:
    from app.models import UsageEvent

    command.upgrade(alembic_config(migration_database), "head")
    engine = create_engine(migration_database)
    with Session(engine) as session:
        tenant = ensure_bootstrap_tenant(session)
        tenant.monthly_query_quota = 1
        session.commit()

    headers = {"X-BoursaMind-RAG-Key": os.environ["BOURSAMIND_RAG_BOOTSTRAP_API_KEY"]}
    with TestClient(app) as client:

        def query(index: int):
            return client.post(
                "/v1/query",
                headers={**headers, "X-Request-ID": f"postgres-quota-{index}"},
                json={"query": "No indexed evidence is expected."},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(query, range(2)))

    assert sorted(response.status_code for response in responses) == [200, 429]
    with Session(engine) as session:
        assert session.query(UsageEvent).filter_by(operation="query").count() == 1
    engine.dispose()


@requires_postgres
def test_concurrent_visibility_revocations_serialize_and_delete_final_owner(
    migration_database: str,
) -> None:
    command.upgrade(alembic_config(migration_database), "head")
    headers = {"X-BoursaMind-RAG-Key": os.environ["BOURSAMIND_RAG_BOOTSTRAP_API_KEY"]}
    with TestClient(app) as client:
        created = client.post(
            "/v1/documents",
            headers=headers,
            json={
                "external_id": "concurrent-shared",
                "title": "Concurrent visibility",
                "text": "Concurrent owner removal must retire the final shared evidence.",
                "owner_keys": ["owner-a", "owner-b"],
            },
        )
        assert created.status_code == 201, created.text

        def revoke(owner: str):
            return client.post(
                "/v1/documents/visibility",
                headers=headers,
                json={
                    "operation_id": f"concurrent-remove-{owner}",
                    "external_id": "concurrent-shared",
                    "owner_key": owner,
                    "visible": False,
                },
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(revoke, ["owner-a", "owner-b"]))

    assert [response.status_code for response in responses] == [200, 200]
    assert sorted(response.json()["remaining_owners"] for response in responses) == [0, 1]
    assert sum(response.json()["document_deleted"] for response in responses) == 1
    engine = create_engine(migration_database)
    with Session(engine) as session:
        assert session.query(Document).filter_by(external_id="concurrent-shared").count() == 0
        assert (
            session.query(VisibilityOperation)
            .filter(VisibilityOperation.operation_id.like("concurrent-remove-%"))
            .count()
            == 2
        )
    engine.dispose()


@requires_postgres
def test_prior_rag_revision_upgrades_and_preserves_tenant_content_and_lineage(
    migration_database: str,
) -> None:
    config = alembic_config(migration_database)
    command.upgrade(config, "0002_external_events")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        tenant_id = connection.execute(
            text(
                "INSERT INTO tenants (slug, name) VALUES ('historical', 'Historical') RETURNING id"
            )
        ).scalar_one()
        workspace_id = connection.execute(
            text(
                "INSERT INTO workspaces (tenant_id, slug, name) "
                "VALUES (:tenant, 'default', 'Default') RETURNING id"
            ),
            {"tenant": tenant_id},
        ).scalar_one()
        collection_id = connection.execute(
            text(
                "INSERT INTO collections (tenant_id, workspace_id, slug, name) "
                "VALUES (:tenant, :workspace, 'default', 'Default') RETURNING id"
            ),
            {"tenant": tenant_id, "workspace": workspace_id},
        ).scalar_one()
        document_id = connection.execute(
            text(
                "INSERT INTO documents "
                "(tenant_id, workspace_id, collection_id, external_id, title, content_hash, text) "
                "VALUES (:tenant, :workspace, :collection, 'legacy-doc', 'Legacy document', "
                "'legacy-hash', 'preserve this text') RETURNING id"
            ),
            {
                "tenant": tenant_id,
                "workspace": workspace_id,
                "collection": collection_id,
            },
        ).scalar_one()
        chunk_id = connection.execute(
            text(
                "INSERT INTO document_chunks "
                "(tenant_id, workspace_id, collection_id, document_id, chunk_index, content, "
                "content_hash, embedding, embedding_model) VALUES "
                "(:tenant, :workspace, :collection, :document, 0, 'preserve this chunk', "
                "'chunk-hash', '[0.1, 0.2, 0.3]'::jsonb, 'legacy-model') RETURNING id"
            ),
            {
                "tenant": tenant_id,
                "workspace": workspace_id,
                "collection": collection_id,
                "document": document_id,
            },
        ).scalar_one()
        job_id = connection.execute(
            text(
                "INSERT INTO ingestion_jobs "
                "(tenant_id, workspace_id, collection_id, document_id, status) "
                "VALUES (:tenant, :workspace, :collection, :document, 'SUCCESS') RETURNING id"
            ),
            {
                "tenant": tenant_id,
                "workspace": workspace_id,
                "collection": collection_id,
                "document": document_id,
            },
        ).scalar_one()

    command.upgrade(config, "head")
    with engine.begin() as connection:
        chunk = connection.execute(
            text(
                "SELECT content, embedding_model, embedding_dimension "
                "FROM document_chunks WHERE id=:id"
            ),
            {"id": chunk_id},
        ).one()
        assert tuple(chunk) == ("preserve this chunk", "legacy-model", 3)
        job = connection.execute(
            text("SELECT attempt, retry_of_job_id FROM ingestion_jobs WHERE id=:id"),
            {"id": job_id},
        ).one()
        assert tuple(job) == (1, None)
        connection.execute(
            text(
                "INSERT INTO conversations (tenant_id, workspace_id, external_id) "
                "VALUES (:tenant, :workspace, 'migration-check')"
            ),
            {"tenant": tenant_id, "workspace": workspace_id},
        )

    with Session(engine) as session:
        conversation = (
            session.query(Conversation)
            .filter_by(tenant_id=tenant_id, external_id="migration-check")
            .one()
        )
        assert conversation.turns == []

    command.downgrade(config, "-1")
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0006_shared_document_visibility"
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM document_chunks WHERE id=:id"), {"id": chunk_id}
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM conversations WHERE external_id='migration-check'")
            ).scalar_one()
            == 1
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0007_constraint_names"
        )
    engine.dispose()


@requires_postgres
def test_concurrent_rag_upgrades_serialize(migration_database: str) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "BOURSAMIND_RAG_DATABASE_URL": migration_database,
            "BOURSAMIND_RAG_ENVIRONMENT": "test",
        }
    )
    logs = [tempfile.TemporaryFile(mode="w+t") for _ in range(2)]
    try:
        processes = [
            subprocess.Popen(  # noqa: S603 - fixed interpreter/module arguments
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for log in logs
        ]
        for process, log in zip(processes, logs, strict=True):
            return_code = process.wait(timeout=120)
            if return_code:
                log.seek(0)
                pytest.fail(log.read())
    finally:
        for log in logs:
            log.close()

    engine = create_engine(migration_database)
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0007_constraint_names"
        )
    engine.dispose()


@requires_postgres
def test_all_rag_columns_match_migrated_types_and_nullability(migration_database: str) -> None:
    from app.database import Base

    command.upgrade(alembic_config(migration_database), "head")
    engine = create_engine(migration_database)
    inspector = inspect(engine)
    comparison = MigrationContext.configure(dialect=engine.dialect).impl
    for table in Base.metadata.sorted_tables:
        columns = {column["name"]: column for column in inspector.get_columns(table.name)}
        for column in table.columns:
            actual = columns[column.name]
            assert actual["nullable"] == column.nullable, f"{table.name}.{column.name}"
            assert not comparison.compare_type(Column(column.name, actual["type"]), column), (
                f"{table.name}.{column.name}"
            )
    engine.dispose()


@requires_postgres
def test_failed_upgrade_rolls_back_schema_and_preserves_data(migration_database: str) -> None:
    from sqlalchemy.exc import DBAPIError

    config = alembic_config(migration_database)
    command.upgrade(config, "0003_ingestion_job_retries")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO tenants (slug, name) VALUES ('failure', 'Preserved')"))
        # An incompatible object forces the new migration to fail after its
        # CREATE TABLE, proving PostgreSQL rolls back preceding DDL as well.
        connection.execute(text("ALTER TABLE document_chunks ADD COLUMN embedding_dimension TEXT"))
    with pytest.raises(DBAPIError):
        command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0003_ingestion_job_retries"
        )
        assert (
            connection.execute(text("SELECT name FROM tenants WHERE slug='failure'")).scalar_one()
            == "Preserved"
        )
        assert "conversations" not in inspect(connection).get_table_names()
    # Repair only the deliberately injected test defect, then prove recovery.
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE document_chunks DROP COLUMN embedding_dimension"))
    command.upgrade(config, "head")
    engine.dispose()
