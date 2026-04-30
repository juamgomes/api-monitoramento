from collections.abc import AsyncGenerator
from pathlib import Path
from sqlite3 import Connection as SQLite3Connection

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.constants import DEFAULT_LOG_ERROR_PATTERNS, DEFAULT_LOG_TAIL_LINES
from app.constants import DEFAULT_AUTOMATION_COOLDOWN_SECONDS, DEFAULT_AUTOMATION_TRIGGER_PATTERN
from app.config import DEFAULT_DATABASE_PATH, get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
DEFAULT_DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

if settings.database_url.startswith("sqlite"):
    database_target = settings.database_url.replace("sqlite+aiosqlite:///", "", 1)
    Path(database_target).parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(settings.database_url, future=True, echo=False)

if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        if isinstance(dbapi_connection, SQLite3Connection):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    import app.models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        if settings.database_url.startswith("sqlite"):
            await connection.run_sync(_apply_sqlite_schema_updates)


def _apply_sqlite_schema_updates(connection) -> None:
    monitored_server_columns = {
        row[1]
        for row in connection.exec_driver_sql("PRAGMA table_info(monitored_servers)")
    }
    server_snapshot_columns = {
        row[1]
        for row in connection.exec_driver_sql("PRAGMA table_info(server_snapshots)")
    }

    default_patterns_json = str(DEFAULT_LOG_ERROR_PATTERNS).replace("'", '"')
    monitored_server_patches = {
        "monitor_container_logs": "ALTER TABLE monitored_servers ADD COLUMN monitor_container_logs BOOLEAN NOT NULL DEFAULT 0",
        "log_monitored_containers": "ALTER TABLE monitored_servers ADD COLUMN log_monitored_containers JSON NOT NULL DEFAULT '[]'",
        "log_tail_lines": f"ALTER TABLE monitored_servers ADD COLUMN log_tail_lines INTEGER NOT NULL DEFAULT {DEFAULT_LOG_TAIL_LINES}",
        "log_error_patterns": (
            "ALTER TABLE monitored_servers ADD COLUMN log_error_patterns JSON NOT NULL DEFAULT "
            f"'{default_patterns_json}'"
        ),
        "automation_enabled": "ALTER TABLE monitored_servers ADD COLUMN automation_enabled BOOLEAN NOT NULL DEFAULT 0",
        "automation_target_container": "ALTER TABLE monitored_servers ADD COLUMN automation_target_container VARCHAR(255)",
        "automation_trigger_pattern": (
            "ALTER TABLE monitored_servers ADD COLUMN automation_trigger_pattern VARCHAR(255) "
            f"DEFAULT '{DEFAULT_AUTOMATION_TRIGGER_PATTERN}'"
        ),
        "automation_command": "ALTER TABLE monitored_servers ADD COLUMN automation_command TEXT",
        "automation_cooldown_seconds": (
            "ALTER TABLE monitored_servers ADD COLUMN automation_cooldown_seconds INTEGER NOT NULL DEFAULT "
            f"{DEFAULT_AUTOMATION_COOLDOWN_SECONDS}"
        ),
        "last_automation_at": "ALTER TABLE monitored_servers ADD COLUMN last_automation_at DATETIME",
        "last_automation_status": "ALTER TABLE monitored_servers ADD COLUMN last_automation_status VARCHAR(30)",
    }
    server_snapshot_patches = {
        "log_alerts_total": "ALTER TABLE server_snapshots ADD COLUMN log_alerts_total INTEGER NOT NULL DEFAULT 0",
    }

    for column_name, statement in monitored_server_patches.items():
        if column_name not in monitored_server_columns:
            connection.exec_driver_sql(statement)

    for column_name, statement in server_snapshot_patches.items():
        if column_name not in server_snapshot_columns:
            connection.exec_driver_sql(statement)

    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS container_log_alerts (
            id INTEGER NOT NULL PRIMARY KEY,
            snapshot_id INTEGER NOT NULL,
            container_name VARCHAR(255) NOT NULL,
            container_id VARCHAR(120),
            severity VARCHAR(20) NOT NULL DEFAULT 'warning',
            match_count INTEGER NOT NULL DEFAULT 0,
            matched_patterns JSON NOT NULL DEFAULT '[]',
            excerpt_lines JSON NOT NULL DEFAULT '[]',
            collection_error TEXT,
            FOREIGN KEY(snapshot_id) REFERENCES server_snapshots (id) ON DELETE CASCADE
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_container_log_alerts_snapshot_id ON container_log_alerts (snapshot_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_container_log_alerts_container_name ON container_log_alerts (container_name)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS automation_events (
            id INTEGER NOT NULL PRIMARY KEY,
            server_id INTEGER NOT NULL,
            snapshot_id INTEGER,
            detected_at DATETIME NOT NULL,
            executed_at DATETIME,
            container_name VARCHAR(255) NOT NULL,
            container_id VARCHAR(120),
            trigger_pattern VARCHAR(255) NOT NULL,
            trigger_signature VARCHAR(128) NOT NULL,
            command TEXT NOT NULL,
            action_status VARCHAR(30) NOT NULL DEFAULT 'executed',
            match_count INTEGER NOT NULL DEFAULT 0,
            matched_patterns JSON NOT NULL DEFAULT '[]',
            excerpt_lines JSON NOT NULL DEFAULT '[]',
            command_output TEXT,
            error_message TEXT,
            cooldown_until DATETIME,
            FOREIGN KEY(server_id) REFERENCES monitored_servers (id) ON DELETE CASCADE,
            FOREIGN KEY(snapshot_id) REFERENCES server_snapshots (id) ON DELETE SET NULL
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_automation_events_server_id ON automation_events (server_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_automation_events_snapshot_id ON automation_events (snapshot_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_automation_events_detected_at ON automation_events (detected_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_automation_events_container_name ON automation_events (container_name)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_automation_events_trigger_signature ON automation_events (trigger_signature)"
    )
