from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants import (
    DEFAULT_AUTOMATION_COOLDOWN_SECONDS,
    DEFAULT_AUTOMATION_TRIGGER_PATTERN,
    DEFAULT_LOG_ERROR_PATTERNS,
    DEFAULT_LOG_TAIL_LINES,
)
from app.database import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class MonitoredServer(Base):
    __tablename__ = "monitored_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    host: Mapped[str] = mapped_column(String(255), index=True)
    port: Mapped[int] = mapped_column(Integer, default=22)
    username: Mapped[str] = mapped_column(String(120))
    ssh_auth_mode: Mapped[str] = mapped_column(String(20), default="password")
    ssh_password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    ssh_private_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    ssh_passphrase_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    monitor_docker: Mapped[bool] = mapped_column(Boolean, default=True)
    watch_all_containers: Mapped[bool] = mapped_column(Boolean, default=True)
    expected_containers: Mapped[list[str]] = mapped_column(JSON, default=list)
    monitor_container_logs: Mapped[bool] = mapped_column(Boolean, default=False)
    log_monitored_containers: Mapped[list[str]] = mapped_column(JSON, default=list)
    log_tail_lines: Mapped[int] = mapped_column(Integer, default=DEFAULT_LOG_TAIL_LINES)
    log_error_patterns: Mapped[list[str]] = mapped_column(JSON, default=lambda: DEFAULT_LOG_ERROR_PATTERNS.copy())
    automation_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    automation_target_container: Mapped[str | None] = mapped_column(String(255), nullable=True)
    automation_trigger_pattern: Mapped[str | None] = mapped_column(
        String(255),
        default=DEFAULT_AUTOMATION_TRIGGER_PATTERN,
        nullable=True,
    )
    automation_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    automation_cooldown_seconds: Mapped[int] = mapped_column(Integer, default=DEFAULT_AUTOMATION_COOLDOWN_SECONDS)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    root_disk_path: Mapped[str] = mapped_column(String(255), default="/")
    warning_disk_percent: Mapped[int] = mapped_column(Integer, default=80)
    critical_disk_percent: Mapped[int] = mapped_column(Integer, default=90)
    warning_memory_percent: Mapped[int] = mapped_column(Integer, default=80)
    critical_memory_percent: Mapped[int] = mapped_column(Integer, default=90)
    warning_load_per_core: Mapped[float] = mapped_column(Float, default=0.7)
    critical_load_per_core: Mapped[float] = mapped_column(Float, default=1.0)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(20), default="unknown")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_automation_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_automation_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    snapshots: Mapped[list["ServerSnapshot"]] = relationship(
        back_populates="server",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by=lambda: ServerSnapshot.collected_at.desc(),
    )
    automation_events: Mapped[list["AutomationEvent"]] = relationship(
        back_populates="server",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by=lambda: AutomationEvent.detected_at.desc(),
    )


class ServerSnapshot(Base):
    __tablename__ = "server_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("monitored_servers.id", ondelete="CASCADE"), index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    alerts: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cpu_cores: Mapped[int | None] = mapped_column(Integer, nullable=True)
    load_1: Mapped[float | None] = mapped_column(Float, nullable=True)
    load_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    load_15: Mapped[float | None] = mapped_column(Float, nullable=True)
    load_per_core: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_total_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_used_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    disk_path: Mapped[str] = mapped_column(String(255), default="/")
    disk_total_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_used_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    uptime_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    containers_total: Mapped[int] = mapped_column(Integer, default=0)
    containers_running: Mapped[int] = mapped_column(Integer, default=0)
    containers_unhealthy: Mapped[int] = mapped_column(Integer, default=0)
    log_alerts_total: Mapped[int] = mapped_column(Integer, default=0)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    server: Mapped["MonitoredServer"] = relationship(back_populates="snapshots")
    containers: Mapped[list["ContainerSnapshot"]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    log_alerts: Mapped[list["ContainerLogAlert"]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    automation_events: Mapped[list["AutomationEvent"]] = relationship(
        back_populates="snapshot",
        passive_deletes=True,
    )


class ContainerSnapshot(Base):
    __tablename__ = "container_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("server_snapshots.id", ondelete="CASCADE"), index=True)
    container_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str | None] = mapped_column(String(255), nullable=True)
    health: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_running: Mapped[bool] = mapped_column(Boolean, default=False)
    is_healthy: Mapped[bool] = mapped_column(Boolean, default=False)

    snapshot: Mapped["ServerSnapshot"] = relationship(back_populates="containers")


class ContainerLogAlert(Base):
    __tablename__ = "container_log_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("server_snapshots.id", ondelete="CASCADE"), index=True)
    container_name: Mapped[str] = mapped_column(String(255), index=True)
    container_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    match_count: Mapped[int] = mapped_column(Integer, default=0)
    matched_patterns: Mapped[list[str]] = mapped_column(JSON, default=list)
    excerpt_lines: Mapped[list[str]] = mapped_column(JSON, default=list)
    collection_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    snapshot: Mapped["ServerSnapshot"] = relationship(back_populates="log_alerts")


class AutomationEvent(Base):
    __tablename__ = "automation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("monitored_servers.id", ondelete="CASCADE"), index=True)
    snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("server_snapshots.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    container_name: Mapped[str] = mapped_column(String(255), index=True)
    container_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    trigger_pattern: Mapped[str] = mapped_column(String(255))
    trigger_signature: Mapped[str] = mapped_column(String(128), index=True)
    command: Mapped[str] = mapped_column(Text)
    action_status: Mapped[str] = mapped_column(String(30), default="executed")
    match_count: Mapped[int] = mapped_column(Integer, default=0)
    matched_patterns: Mapped[list[str]] = mapped_column(JSON, default=list)
    excerpt_lines: Mapped[list[str]] = mapped_column(JSON, default=list)
    command_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    server: Mapped["MonitoredServer"] = relationship(back_populates="automation_events")
    snapshot: Mapped[ServerSnapshot | None] = relationship(back_populates="automation_events")
