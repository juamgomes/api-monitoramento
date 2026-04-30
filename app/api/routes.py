from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_session
from app.dependencies import require_api_key
from app.models import AutomationEvent, MonitoredServer, ServerSnapshot
from app.schemas import (
    AutomationDashboardSummaryRead,
    AutomationEventRead,
    AutomationStatusRead,
    DashboardRead,
    ServerCreate,
    ServerRead,
    ServerSnapshotRead,
    ServerUpdate,
)
from app.services.collector import build_dashboard, collect_and_store
from app.services.crypto import cipher
from app.services.automation import build_automation_dashboard_summary


health_router = APIRouter(tags=["health"])
api_router = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])


def append_unique_string(items: list[str], value: str | None) -> list[str]:
    normalized_items = [item.strip() for item in items if item and item.strip()]
    if value is None or not value.strip():
        return normalized_items
    normalized_value = value.strip()
    if normalized_value in normalized_items:
        return normalized_items
    return [*normalized_items, normalized_value]


def normalize_automation_configuration(server: MonitoredServer) -> None:
    if server.automation_target_container is not None:
        server.automation_target_container = server.automation_target_container.strip() or None
    if server.automation_trigger_pattern is not None:
        server.automation_trigger_pattern = server.automation_trigger_pattern.strip() or None
    if server.automation_command is not None:
        server.automation_command = server.automation_command.strip() or None

    if not server.automation_enabled:
        return

    if not server.monitor_container_logs:
        raise HTTPException(status_code=422, detail="automation_enabled exige monitor_container_logs=true.")
    if not server.automation_target_container:
        raise HTTPException(status_code=422, detail="automation_target_container e obrigatorio quando automation_enabled=true.")
    if not server.automation_trigger_pattern:
        raise HTTPException(status_code=422, detail="automation_trigger_pattern e obrigatorio quando automation_enabled=true.")
    if not server.automation_command:
        raise HTTPException(status_code=422, detail="automation_command e obrigatorio quando automation_enabled=true.")

    server.log_monitored_containers = append_unique_string(
        server.log_monitored_containers or [],
        server.automation_target_container,
    )
    server.log_error_patterns = append_unique_string(
        server.log_error_patterns or [],
        server.automation_trigger_pattern,
    )


def get_automation_status_metadata(server: MonitoredServer) -> tuple[bool, bool, str, str | None]:
    configured = bool(
        server.monitor_container_logs
        and server.automation_target_container
        and server.automation_trigger_pattern
        and server.automation_command
    )
    if not server.automation_enabled:
        return configured, False, "paused", "Automacao pausada manualmente."
    if not configured:
        return False, False, "misconfigured", "Automacao habilitada, mas com configuracao incompleta."
    return True, True, "active", None


def serialize_automation_status(server: MonitoredServer) -> AutomationStatusRead:
    configured, active, status_name, status_reason = get_automation_status_metadata(server)
    return AutomationStatusRead(
        server_id=server.id,
        server_name=server.name,
        automation_enabled=server.automation_enabled,
        automation_configured=configured,
        automation_active=active,
        automation_status=status_name,
        automation_status_reason=status_reason,
        monitor_container_logs=server.monitor_container_logs,
        automation_target_container=server.automation_target_container,
        automation_trigger_pattern=server.automation_trigger_pattern,
        automation_command=server.automation_command,
        automation_cooldown_seconds=server.automation_cooldown_seconds,
        last_checked_at=server.last_checked_at,
        last_automation_at=server.last_automation_at,
        last_automation_status=server.last_automation_status,
        updated_at=server.updated_at,
    )


def serialize_automation_event(event: AutomationEvent) -> AutomationEventRead:
    return AutomationEventRead.model_validate(
        {
            "id": event.id,
            "server_id": event.server_id,
            "server_name": event.server.name if event.server is not None else None,
            "snapshot_id": event.snapshot_id,
            "detected_at": event.detected_at,
            "executed_at": event.executed_at,
            "container_name": event.container_name,
            "container_id": event.container_id,
            "trigger_pattern": event.trigger_pattern,
            "trigger_signature": event.trigger_signature,
            "command": event.command,
            "action_status": event.action_status,
            "match_count": event.match_count,
            "matched_patterns": event.matched_patterns or [],
            "excerpt_lines": event.excerpt_lines or [],
            "command_output": event.command_output,
            "error_message": event.error_message,
            "cooldown_until": event.cooldown_until,
        },
    )


def serialize_server(server: MonitoredServer) -> ServerRead:
    automation_status = serialize_automation_status(server)
    return ServerRead.model_validate(
        {
            "id": server.id,
            "name": server.name,
            "host": server.host,
            "port": server.port,
            "username": server.username,
            "ssh_auth_mode": server.ssh_auth_mode,
            "monitor_docker": server.monitor_docker,
            "watch_all_containers": server.watch_all_containers,
            "expected_containers": server.expected_containers or [],
            "monitor_container_logs": server.monitor_container_logs,
            "log_monitored_containers": server.log_monitored_containers or [],
            "log_tail_lines": server.log_tail_lines,
            "log_error_patterns": server.log_error_patterns or [],
            "automation_enabled": server.automation_enabled,
            "automation_target_container": server.automation_target_container,
            "automation_trigger_pattern": server.automation_trigger_pattern,
            "automation_command": server.automation_command,
            "automation_cooldown_seconds": server.automation_cooldown_seconds,
            "automation_configured": automation_status.automation_configured,
            "automation_active": automation_status.automation_active,
            "automation_status": automation_status.automation_status,
            "automation_status_reason": automation_status.automation_status_reason,
            "enabled": server.enabled,
            "root_disk_path": server.root_disk_path,
            "warning_disk_percent": server.warning_disk_percent,
            "critical_disk_percent": server.critical_disk_percent,
            "warning_memory_percent": server.warning_memory_percent,
            "critical_memory_percent": server.critical_memory_percent,
            "warning_load_per_core": server.warning_load_per_core,
            "critical_load_per_core": server.critical_load_per_core,
            "has_password": bool(server.ssh_password_encrypted),
            "has_private_key": bool(server.ssh_private_key_encrypted),
            "has_passphrase": bool(server.ssh_passphrase_encrypted),
            "last_checked_at": server.last_checked_at,
            "last_status": server.last_status,
            "last_error": server.last_error,
            "last_automation_at": server.last_automation_at,
            "last_automation_status": server.last_automation_status,
            "created_at": server.created_at,
            "updated_at": server.updated_at,
        },
    )


def validate_thresholds(server: MonitoredServer) -> None:
    if server.critical_disk_percent < server.warning_disk_percent:
        raise HTTPException(status_code=422, detail="critical_disk_percent deve ser maior ou igual ao warning_disk_percent.")
    if server.critical_memory_percent < server.warning_memory_percent:
        raise HTTPException(status_code=422, detail="critical_memory_percent deve ser maior ou igual ao warning_memory_percent.")
    if server.critical_load_per_core < server.warning_load_per_core:
        raise HTTPException(status_code=422, detail="critical_load_per_core deve ser maior ou igual ao warning_load_per_core.")


def validate_auth_configuration(server: MonitoredServer) -> None:
    if server.ssh_auth_mode == "password" and not server.ssh_password_encrypted:
        raise HTTPException(status_code=422, detail="ssh_password e obrigatorio quando ssh_auth_mode=password.")
    if server.ssh_auth_mode == "private_key" and not server.ssh_private_key_encrypted:
        raise HTTPException(status_code=422, detail="ssh_private_key e obrigatorio quando ssh_auth_mode=private_key.")


def validate_container_log_configuration(server: MonitoredServer) -> None:
    if server.monitor_container_logs and not server.monitor_docker:
        raise HTTPException(status_code=422, detail="monitor_container_logs exige monitor_docker=true.")
    if server.monitor_container_logs and not (server.log_monitored_containers or []):
        raise HTTPException(
            status_code=422,
            detail="log_monitored_containers deve ter ao menos um container quando monitor_container_logs=true.",
        )
    if server.monitor_container_logs and not (server.log_error_patterns or []):
        raise HTTPException(
            status_code=422,
            detail="log_error_patterns deve ter ao menos um padrao quando monitor_container_logs=true.",
        )


@health_router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@health_router.get("/")
async def root() -> dict[str, str]:
    return {"message": "API de monitoramento ativa. Consulte /docs para testar os endpoints."}


@api_router.get("/dashboard", response_model=DashboardRead, tags=["dashboard"])
async def get_dashboard(session: AsyncSession = Depends(get_session)) -> DashboardRead:
    servers = list(await session.scalars(select(MonitoredServer).order_by(MonitoredServer.name)))
    counters = await build_dashboard(session)
    automation_summary_raw = await build_automation_dashboard_summary(session)
    return DashboardRead(
        generated_at=datetime.utcnow(),
        servers=[serialize_server(server) for server in servers],
        automation_summary=AutomationDashboardSummaryRead(
            total_events=automation_summary_raw["total_events"],
            executed_events=automation_summary_raw["executed_events"],
            failed_events=automation_summary_raw["failed_events"],
            skipped_events=automation_summary_raw["skipped_events"],
            recent_events=[
                serialize_automation_event(event)
                for event in automation_summary_raw["recent_events"]
            ],
        ),
        **counters,
    )


@api_router.get("/servers", response_model=list[ServerRead], tags=["servers"])
async def list_servers(session: AsyncSession = Depends(get_session)) -> list[ServerRead]:
    rows = await session.scalars(select(MonitoredServer).order_by(MonitoredServer.name))
    return [serialize_server(server) for server in rows]


@api_router.post("/servers", response_model=ServerRead, status_code=status.HTTP_201_CREATED, tags=["servers"])
async def create_server(payload: ServerCreate, session: AsyncSession = Depends(get_session)) -> ServerRead:
    server = MonitoredServer(
        **payload.model_dump(
            exclude={"ssh_password", "ssh_private_key", "ssh_passphrase"},
        ),
        ssh_password_encrypted=cipher.encrypt(payload.ssh_password),
        ssh_private_key_encrypted=cipher.encrypt(payload.ssh_private_key),
        ssh_passphrase_encrypted=cipher.encrypt(payload.ssh_passphrase),
    )
    validate_thresholds(server)
    validate_auth_configuration(server)
    normalize_automation_configuration(server)
    validate_container_log_configuration(server)
    session.add(server)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Ja existe um servidor com esse nome.") from exc
    await session.refresh(server)
    return serialize_server(server)


@api_router.get("/servers/{server_id}", response_model=ServerRead, tags=["servers"])
async def get_server(server_id: int, session: AsyncSession = Depends(get_session)) -> ServerRead:
    server = await session.get(MonitoredServer, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Servidor nao encontrado.")
    return serialize_server(server)


@api_router.patch("/servers/{server_id}", response_model=ServerRead, tags=["servers"])
async def update_server(
    server_id: int,
    payload: ServerUpdate,
    session: AsyncSession = Depends(get_session),
) -> ServerRead:
    server = await session.get(MonitoredServer, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Servidor nao encontrado.")

    data = payload.model_dump(exclude_unset=True)
    if "ssh_password" in data:
        server.ssh_password_encrypted = cipher.encrypt(data.pop("ssh_password"))
    if "ssh_private_key" in data:
        server.ssh_private_key_encrypted = cipher.encrypt(data.pop("ssh_private_key"))
    if "ssh_passphrase" in data:
        server.ssh_passphrase_encrypted = cipher.encrypt(data.pop("ssh_passphrase"))

    for field, value in data.items():
        setattr(server, field, value)

    validate_thresholds(server)
    validate_auth_configuration(server)
    normalize_automation_configuration(server)
    validate_container_log_configuration(server)
    session.add(server)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Ja existe um servidor com esse nome.") from exc
    await session.refresh(server)
    return serialize_server(server)


@api_router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["servers"])
async def delete_server(server_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    server = await session.get(MonitoredServer, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Servidor nao encontrado.")
    await session.delete(server)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.post("/servers/{server_id}/collect", response_model=ServerSnapshotRead, tags=["servers"])
async def collect_server(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> ServerSnapshotRead:
    server = await session.get(MonitoredServer, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Servidor nao encontrado.")

    snapshot = await collect_and_store(session, server)
    return ServerSnapshotRead.model_validate(snapshot)


@api_router.get("/servers/{server_id}/snapshots", response_model=list[ServerSnapshotRead], tags=["servers"])
async def list_snapshots(
    server_id: int,
    limit: int = Query(default=20, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[ServerSnapshotRead]:
    server = await session.get(MonitoredServer, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Servidor nao encontrado.")

    snapshots = await session.scalars(
        select(ServerSnapshot)
        .options(
            selectinload(ServerSnapshot.containers),
            selectinload(ServerSnapshot.log_alerts),
            selectinload(ServerSnapshot.automation_events),
        )
        .where(ServerSnapshot.server_id == server_id)
        .order_by(ServerSnapshot.collected_at.desc())
        .limit(limit),
    )
    return [ServerSnapshotRead.model_validate(snapshot) for snapshot in snapshots]


@api_router.get("/automation-events", response_model=list[AutomationEventRead], tags=["automation"])
async def list_automation_events(
    limit: int = Query(default=50, ge=1, le=500),
    server_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[AutomationEventRead]:
    statement = select(AutomationEvent).options(selectinload(AutomationEvent.server))
    if server_id is not None:
        statement = statement.where(AutomationEvent.server_id == server_id)

    statement = statement.order_by(AutomationEvent.detected_at.desc()).limit(limit)
    rows = await session.scalars(statement)
    return [serialize_automation_event(event) for event in rows]


@api_router.get(
    "/servers/{server_id}/automation-events",
    response_model=list[AutomationEventRead],
    tags=["automation"],
)
async def list_server_automation_events(
    server_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[AutomationEventRead]:
    server = await session.get(MonitoredServer, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Servidor nao encontrado.")

    rows = await session.scalars(
        select(AutomationEvent)
        .options(selectinload(AutomationEvent.server))
        .where(AutomationEvent.server_id == server_id)
        .order_by(AutomationEvent.detected_at.desc())
        .limit(limit),
    )
    return [serialize_automation_event(event) for event in rows]


@api_router.get(
    "/servers/{server_id}/automation-status",
    response_model=AutomationStatusRead,
    tags=["automation"],
)
async def get_server_automation_status(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> AutomationStatusRead:
    server = await session.get(MonitoredServer, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Servidor nao encontrado.")
    return serialize_automation_status(server)


@api_router.post(
    "/servers/{server_id}/automation/activate",
    response_model=AutomationStatusRead,
    tags=["automation"],
)
async def activate_server_automation(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> AutomationStatusRead:
    server = await session.get(MonitoredServer, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Servidor nao encontrado.")

    server.automation_enabled = True
    normalize_automation_configuration(server)
    validate_container_log_configuration(server)
    session.add(server)
    await session.commit()
    await session.refresh(server)
    return serialize_automation_status(server)


@api_router.post(
    "/servers/{server_id}/automation/pause",
    response_model=AutomationStatusRead,
    tags=["automation"],
)
async def pause_server_automation(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> AutomationStatusRead:
    server = await session.get(MonitoredServer, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Servidor nao encontrado.")

    server.automation_enabled = False
    session.add(server)
    await session.commit()
    await session.refresh(server)
    return serialize_automation_status(server)
