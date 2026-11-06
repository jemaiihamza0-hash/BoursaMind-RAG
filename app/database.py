"""Database setup for the standalone RAG service."""

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _connect_args(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def _ensure_sqlite_parent(url: str) -> None:
    if not url.startswith("sqlite:///"):
        return
    db_path = url.removeprefix("sqlite:///")
    if db_path in (":memory:", ""):
        return
    Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


settings = get_settings()
_ensure_sqlite_parent(settings.database_url)

_engine_options: dict = {
    "connect_args": _connect_args(settings.database_url),
    "future": True,
    "pool_pre_ping": True,
}
if settings.database_url.startswith("postgresql"):
    _engine_options.update(
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_recycle=settings.database_pool_recycle_seconds,
        connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
    )

engine = create_engine(settings.database_url, **_engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db(*, drop: bool = False) -> None:
    from app import models  # noqa: F401 - registers model metadata

    if drop:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    _upgrade_development_sqlite_schema(engine)


def _upgrade_development_sqlite_schema(bind: Engine) -> None:
    """Bring an existing development SQLite database in line with the ORM schema.

    ``create_all`` creates missing tables but deliberately does not add columns to
    existing ones. Production uses Alembic and PostgreSQL; this small compatibility
    step keeps a developer's local data usable after additive schema changes.
    """
    if bind.dialect.name != "sqlite":
        return

    with bind.begin() as connection:
        columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(usage_events)").fetchall()
        }
        if not columns:
            return
        if "quota_identity" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE usage_events ADD COLUMN quota_identity VARCHAR(180)"
            )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_event_quota_identity "
            "ON usage_events (tenant_id, quota_identity)"
        )


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
