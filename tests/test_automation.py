import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes import (
    activate_server_automation,
    normalize_automation_configuration,
    pause_server_automation,
    serialize_automation_status,
)
from app.database import Base
from app.models import MonitoredServer
from app.services.automation import select_automation_candidate
from app.services.collector import CollectedLogAlert


def build_server(**overrides):
    base = {
        "id": 1,
        "name": "239",
        "monitor_container_logs": True,
        "automation_enabled": True,
        "automation_target_container": "cess-atualizao-25jul2024-cess-1",
        "automation_trigger_pattern": "untrusted",
        "automation_command": "sh reiniciar_certificado.sh",
        "automation_cooldown_seconds": 600,
        "log_monitored_containers": [],
        "log_error_patterns": ["error"],
        "last_checked_at": None,
        "last_automation_at": None,
        "last_automation_status": None,
        "updated_at": datetime(2026, 4, 30, 0, 0, 0),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def build_db_server(**overrides) -> MonitoredServer:
    base = {
        "name": "srv-239",
        "host": "192.168.2.239",
        "port": 22,
        "username": "root",
        "monitor_docker": True,
        "watch_all_containers": True,
        "expected_containers": [],
        "monitor_container_logs": True,
        "log_monitored_containers": ["cess-atualizao-25jul2024-cess-1"],
        "log_tail_lines": 200,
        "log_error_patterns": ["error", "untrusted"],
        "automation_enabled": True,
        "automation_target_container": "cess-atualizao-25jul2024-cess-1",
        "automation_trigger_pattern": "untrusted",
        "automation_command": "sh reiniciar_certificado.sh",
        "automation_cooldown_seconds": 600,
        "enabled": True,
        "root_disk_path": "/",
        "warning_disk_percent": 80,
        "critical_disk_percent": 90,
        "warning_memory_percent": 80,
        "critical_memory_percent": 90,
        "warning_load_per_core": 0.7,
        "critical_load_per_core": 1.0,
    }
    base.update(overrides)
    return MonitoredServer(**base)


def test_normalize_automation_configuration_adds_target_container_and_trigger_pattern():
    server = build_server()

    normalize_automation_configuration(server)

    assert server.log_monitored_containers == ["cess-atualizao-25jul2024-cess-1"]
    assert server.log_error_patterns == ["error", "untrusted"]


def test_normalize_automation_configuration_requires_log_monitoring():
    server = build_server(monitor_container_logs=False)

    with pytest.raises(HTTPException) as exc_info:
        normalize_automation_configuration(server)

    assert exc_info.value.status_code == 422
    assert "monitor_container_logs" in exc_info.value.detail


def test_select_automation_candidate_uses_full_matched_lines_for_trigger_detection():
    server = build_server()
    log_alert = CollectedLogAlert(
        container_name="cess-atualizao-25jul2024-cess-1",
        container_id="abc123",
        match_count=12,
        matched_patterns=["error", "untrusted"],
        matched_lines=[
            "2026-04-30T00:00:00Z ERROR tls chain is untrusted",
            "2026-04-30T00:00:01Z ERROR retrying request",
            "2026-04-30T00:00:02Z ERROR still failing",
        ],
        excerpt_lines=[
            "2026-04-30T00:00:01Z ERROR retrying request",
            "2026-04-30T00:00:02Z ERROR still failing",
        ],
    )

    candidate = select_automation_candidate(server, [log_alert])

    assert candidate is not None
    assert candidate.container_name == "cess-atualizao-25jul2024-cess-1"
    assert candidate.match_count == 1
    assert candidate.excerpt_lines == ["2026-04-30T00:00:00Z ERROR tls chain is untrusted"]
    assert candidate.trigger_signature


def test_select_automation_candidate_ignores_other_containers():
    server = build_server()
    log_alert = CollectedLogAlert(
        container_name="outro-container",
        matched_patterns=["untrusted"],
        matched_lines=["ERROR untrusted"],
        excerpt_lines=["ERROR untrusted"],
    )

    candidate = select_automation_candidate(server, [log_alert])

    assert candidate is None


def test_serialize_automation_status_reports_paused_and_misconfigured_states():
    paused_status = serialize_automation_status(build_server(automation_enabled=False))
    misconfigured_status = serialize_automation_status(build_server(automation_command=None))

    assert paused_status.automation_status == "paused"
    assert paused_status.automation_active is False
    assert paused_status.automation_configured is True
    assert misconfigured_status.automation_status == "misconfigured"
    assert misconfigured_status.automation_active is False
    assert misconfigured_status.automation_configured is False


def test_pause_and_activate_automation_endpoints_toggle_state():
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

            async with session_factory() as session:
                server = build_db_server()
                session.add(server)
                await session.commit()
                await session.refresh(server)

                paused = await pause_server_automation(server.id, session)
                assert paused.automation_enabled is False
                assert paused.automation_status == "paused"
                assert paused.automation_active is False

                activated = await activate_server_automation(server.id, session)
                assert activated.automation_enabled is True
                assert activated.automation_status == "active"
                assert activated.automation_active is True

                reloaded = await session.get(MonitoredServer, server.id)
                assert reloaded is not None
                assert reloaded.automation_enabled is True
        finally:
            await engine.dispose()

    asyncio.run(scenario())
