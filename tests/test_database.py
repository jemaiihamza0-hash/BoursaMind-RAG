from sqlalchemy import create_engine, inspect

from app.database import _upgrade_development_sqlite_schema


def test_existing_sqlite_usage_events_table_is_upgraded_without_data_loss(tmp_path):
    database = create_engine(f"sqlite:///{tmp_path / 'rag.db'}")
    with database.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE usage_events (
                id INTEGER PRIMARY KEY,
                tenant_id INTEGER NOT NULL,
                request_id VARCHAR(128) NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            "INSERT INTO usage_events (id, tenant_id, request_id) VALUES (1, 7, 'existing')"
        )

    _upgrade_development_sqlite_schema(database)
    _upgrade_development_sqlite_schema(database)

    schema = inspect(database)
    columns = {column["name"] for column in schema.get_columns("usage_events")}
    indexes = {index["name"] for index in schema.get_indexes("usage_events")}
    with database.connect() as connection:
        existing = connection.exec_driver_sql(
            "SELECT tenant_id, request_id FROM usage_events WHERE id = 1"
        ).one()

    assert "quota_identity" in columns
    assert "uq_usage_event_quota_identity" in indexes
    assert existing == (7, "existing")
