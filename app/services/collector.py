from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.constants import DEFAULT_LOG_ERROR_PATTERNS, DEFAULT_LOG_EXCERPT_LINES
from app.models import ContainerLogAlert, ContainerSnapshot, MonitoredServer, ServerSnapshot
from app.services.automation import describe_automation_event, process_automation_for_snapshot
from app.services.ssh import RemoteExecutionError, open_connection, run_command


HEALTH_PATTERN = re.compile(r"\((healthy|unhealthy|starting)\)", re.IGNORECASE)


@dataclass(slots=True)
class CollectedContainer:
    container_id: str | None
    name: str
    image: str | None
    state: str | None
    status: str | None
    health: str | None
    is_running: bool
    is_healthy: bool


@dataclass(slots=True)
class CollectedMetrics:
    cpu_cores: int | None = None
    load_1: float | None = None
    load_5: float | None = None
    load_15: float | None = None
    load_per_core: float | None = None
    memory_total_bytes: int | None = None
    memory_used_bytes: int | None = None
    memory_percent: float | None = None
    disk_path: str = "/"
    disk_total_bytes: int | None = None
    disk_used_bytes: int | None = None
    disk_percent: float | None = None
    uptime_seconds: float | None = None
    docker_error: str | None = None
    raw_payload: dict[str, object] | None = None


@dataclass(slots=True)
class CollectedLogAlert:
    container_name: str
    container_id: str | None = None
    severity: str = "warning"
    match_count: int = 0
    matched_patterns: list[str] = field(default_factory=list)
    matched_lines: list[str] = field(default_factory=list)
    excerpt_lines: list[str] = field(default_factory=list)
    collection_error: str | None = None


def parse_health(status: str | None) -> str | None:
    if not status:
        return None
    match = HEALTH_PATTERN.search(status)
    if not match:
        return None
    return match.group(1).lower()


def normalize_container(entry: dict[str, str]) -> CollectedContainer:
    state = (entry.get("State") or "").lower() or None
    status = entry.get("Status")
    health = parse_health(status)
    is_running = state == "running"
    is_healthy = health == "healthy" if health else is_running

    return CollectedContainer(
        container_id=entry.get("ID"),
        name=entry.get("Names") or entry.get("Name") or "unknown",
        image=entry.get("Image"),
        state=state,
        status=status,
        health=health,
        is_running=is_running,
        is_healthy=is_healthy,
    )


def compile_log_patterns(patterns: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    compiled_patterns: list[tuple[str, re.Pattern[str]]] = []

    for pattern in patterns or DEFAULT_LOG_ERROR_PATTERNS:
        normalized = pattern.strip()
        if not normalized:
            continue
        try:
            regex = re.compile(normalized, re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(normalized), re.IGNORECASE)
        compiled_patterns.append((normalized, regex))

    return compiled_patterns


def find_log_alert(
    container: CollectedContainer,
    raw_logs: str,
    compiled_patterns: list[tuple[str, re.Pattern[str]]],
) -> CollectedLogAlert | None:
    matched_patterns: list[str] = []
    matched_lines: list[str] = []
    seen_patterns: set[str] = set()

    for raw_line in raw_logs.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        matched_in_line = [pattern for pattern, regex in compiled_patterns if regex.search(line)]
        if not matched_in_line:
            continue

        matched_lines.append(line)
        for pattern in matched_in_line:
            if pattern not in seen_patterns:
                seen_patterns.add(pattern)
                matched_patterns.append(pattern)

    if not matched_lines:
        return None

    return CollectedLogAlert(
        container_name=container.name,
        container_id=container.container_id,
        severity="warning",
        match_count=len(matched_lines),
        matched_patterns=matched_patterns,
        matched_lines=matched_lines,
        excerpt_lines=matched_lines[-DEFAULT_LOG_EXCERPT_LINES:],
    )


def build_docker_logs_command(identifier: str, tail_lines: int) -> str:
    return f"docker logs --tail {tail_lines} {shlex.quote(identifier)} 2>&1"


async def fetch_container_logs_with_retry(
    connection,
    identifier: str,
    tail_lines: int,
) -> str:
    settings = get_settings()
    primary_command = build_docker_logs_command(identifier, tail_lines)

    try:
        return await run_command(
            connection,
            primary_command,
            timeout=settings.docker_logs_command_timeout_seconds,
        )
    except RemoteExecutionError as exc:
        if "Timeout ao executar comando remoto" not in str(exc):
            raise

        fallback_tail_lines = settings.docker_logs_fallback_tail_lines
        fallback_tail_lines = min(fallback_tail_lines, max(tail_lines - 1, 0))
        if fallback_tail_lines < 1:
            raise

        fallback_command = build_docker_logs_command(identifier, fallback_tail_lines)
        try:
            return await run_command(
                connection,
                fallback_command,
                timeout=settings.docker_logs_command_timeout_seconds,
            )
        except RemoteExecutionError as fallback_exc:
            raise RemoteExecutionError(
                f"{fallback_exc} (retry automatico com tail reduzido de {tail_lines} para {fallback_tail_lines} tambem falhou)"
            ) from fallback_exc


async def collect_container_log_alerts(
    connection,
    server: MonitoredServer,
    containers: list[CollectedContainer],
) -> list[CollectedLogAlert]:
    if not server.monitor_container_logs or not server.log_monitored_containers:
        return []

    container_index = {container.name: container for container in containers}
    compiled_patterns = compile_log_patterns(server.log_error_patterns or DEFAULT_LOG_ERROR_PATTERNS)
    collected_alerts: list[CollectedLogAlert] = []

    for container_name in server.log_monitored_containers:
        container = container_index.get(container_name)
        if container is None:
            collected_alerts.append(
                CollectedLogAlert(
                    container_name=container_name,
                    severity="warning",
                    collection_error="Container configurado para monitoramento de logs nao foi encontrado no host.",
                )
            )
            continue

        identifier = container.container_id or container.name
        try:
            raw_logs = await fetch_container_logs_with_retry(
                connection,
                identifier,
                server.log_tail_lines,
            )
        except RemoteExecutionError as exc:
            collected_alerts.append(
                CollectedLogAlert(
                    container_name=container.name,
                    container_id=container.container_id,
                    severity="warning",
                    collection_error=str(exc),
                )
            )
            continue

        detected_alert = find_log_alert(container, raw_logs, compiled_patterns)
        if detected_alert is not None:
            collected_alerts.append(detected_alert)

    return collected_alerts


async def collect_remote_state(
    server: MonitoredServer,
) -> tuple[CollectedMetrics, list[CollectedContainer], list[CollectedLogAlert]]:
    settings = get_settings()
    disk_path = shlex.quote(server.root_disk_path)
    disk_command = "df -B1 {path} | awk 'NR==2 {{print $2 \" \" $3 \" \" $5}}'".format(path=disk_path)
    commands = {
        "cpu_cores": "nproc",
        "loadavg": "cat /proc/loadavg",
        "memory": "free -b | awk 'NR==2 {print $2 \" \" $3}'",
        "disk": disk_command,
        "uptime": "cat /proc/uptime | awk '{print $1}'",
        "docker_ready": "command -v docker >/dev/null 2>&1 && echo 1 || echo 0",
    }

    async with await open_connection(server) as connection:
        cpu_raw = await run_command(connection, commands["cpu_cores"])
        load_raw = await run_command(connection, commands["loadavg"])
        memory_raw = await run_command(connection, commands["memory"])
        disk_raw = await run_command(connection, commands["disk"])
        uptime_raw = await run_command(connection, commands["uptime"])
        docker_ready_raw = await run_command(connection, commands["docker_ready"])

        containers: list[CollectedContainer] = []
        log_alerts: list[CollectedLogAlert] = []
        docker_error: str | None = None
        docker_rows = ""
        if server.monitor_docker:
            if docker_ready_raw != "1":
                docker_error = "CLI do Docker nao encontrada no servidor remoto."
            else:
                try:
                    docker_rows = await run_command(
                        connection,
                        "docker ps -a --format '{{json .}}'",
                        timeout=settings.docker_command_timeout_seconds,
                    )
                    if docker_rows:
                        containers = [
                            normalize_container(json.loads(row))
                            for row in docker_rows.splitlines()
                            if row.strip()
                        ]
                except (RemoteExecutionError, json.JSONDecodeError, ValueError) as exc:
                    docker_error = str(exc)

        if server.monitor_docker and docker_error is None:
            log_alerts = await collect_container_log_alerts(connection, server, containers)

    load_parts = load_raw.split()
    memory_total_str, memory_used_str = memory_raw.split()
    disk_total_str, disk_used_str, disk_percent_str = disk_raw.split()
    cpu_cores = int(cpu_raw)
    load_1 = float(load_parts[0])
    load_5 = float(load_parts[1])
    load_15 = float(load_parts[2])
    memory_total = int(memory_total_str)
    memory_used = int(memory_used_str)
    disk_total = int(disk_total_str)
    disk_used = int(disk_used_str)

    metrics = CollectedMetrics(
        cpu_cores=cpu_cores,
        load_1=load_1,
        load_5=load_5,
        load_15=load_15,
        load_per_core=(load_1 / cpu_cores) if cpu_cores else None,
        memory_total_bytes=memory_total,
        memory_used_bytes=memory_used,
        memory_percent=round((memory_used / memory_total) * 100, 2) if memory_total else None,
        disk_path=server.root_disk_path,
        disk_total_bytes=disk_total,
        disk_used_bytes=disk_used,
        disk_percent=float(disk_percent_str.replace("%", "")),
        uptime_seconds=float(uptime_raw),
        docker_error=docker_error,
        raw_payload={
            "loadavg": load_raw,
            "memory": memory_raw,
            "disk": disk_raw,
            "uptime": uptime_raw,
            "docker_ready": docker_ready_raw,
            "docker_rows": docker_rows,
            "docker_error": docker_error,
            "log_monitored_containers": server.log_monitored_containers or [],
        },
    )
    return metrics, containers, log_alerts


def evaluate_status(
    server: MonitoredServer,
    metrics: CollectedMetrics,
    containers: list[CollectedContainer],
    log_alerts: list[CollectedLogAlert],
) -> tuple[str, list[str]]:
    status = "healthy"
    alerts: list[str] = []

    def promote(next_status: str) -> None:
        nonlocal status
        order = {"healthy": 0, "warning": 1, "critical": 2}
        if order[next_status] > order[status]:
            status = next_status

    if metrics.disk_percent is not None:
        if metrics.disk_percent >= server.critical_disk_percent:
            promote("critical")
            alerts.append(
                f"Disco em {metrics.disk_percent:.1f}% no caminho {server.root_disk_path}.",
            )
        elif metrics.disk_percent >= server.warning_disk_percent:
            promote("warning")
            alerts.append(
                f"Disco em {metrics.disk_percent:.1f}% no caminho {server.root_disk_path}.",
            )

    if metrics.memory_percent is not None:
        if metrics.memory_percent >= server.critical_memory_percent:
            promote("critical")
            alerts.append(f"Memoria em {metrics.memory_percent:.1f}% de uso.")
        elif metrics.memory_percent >= server.warning_memory_percent:
            promote("warning")
            alerts.append(f"Memoria em {metrics.memory_percent:.1f}% de uso.")

    if metrics.load_per_core is not None:
        if metrics.load_per_core >= server.critical_load_per_core:
            promote("critical")
            alerts.append(f"Load por core em {metrics.load_per_core:.2f}.")
        elif metrics.load_per_core >= server.warning_load_per_core:
            promote("warning")
            alerts.append(f"Load por core em {metrics.load_per_core:.2f}.")

    if server.monitor_docker:
        if metrics.docker_error:
            promote("critical")
            alerts.append(f"Falha ao consultar containers Docker: {metrics.docker_error}")
            return status, alerts

        expected = set(server.expected_containers or [])
        available = {container.name for container in containers}

        if expected:
            missing = sorted(expected - available)
            if missing:
                promote("critical")
                alerts.append(f"Containers esperados ausentes: {', '.join(missing)}.")

        containers_to_check = containers
        if expected and not server.watch_all_containers:
            containers_to_check = [container for container in containers if container.name in expected]

        unhealthy = [container for container in containers_to_check if not container.is_healthy]
        stopped = [container for container in containers_to_check if not container.is_running]
        starting = [
            container
            for container in containers_to_check
            if container.health == "starting" and container.is_running
        ]

        if stopped:
            promote("critical")
            alerts.append(
                f"Containers parados: {', '.join(sorted(container.name for container in stopped))}.",
            )
        elif unhealthy:
            promote("critical")
            alerts.append(
                f"Containers sem saude: {', '.join(sorted(container.name for container in unhealthy))}.",
            )
        elif starting:
            promote("warning")
            alerts.append(
                f"Containers iniciando healthcheck: {', '.join(sorted(container.name for container in starting))}.",
            )

    if server.monitor_container_logs:
        for log_alert in log_alerts:
            promote("warning")
            if log_alert.collection_error:
                alerts.append(
                    f"Falha ao analisar logs do container {log_alert.container_name}: {log_alert.collection_error}",
                )
            else:
                alerts.append(
                    f"Erros encontrados nos logs do container {log_alert.container_name}: {log_alert.match_count} linhas suspeitas.",
                )

    return status, alerts


async def trim_history(session: AsyncSession, server_id: int) -> None:
    settings = get_settings()
    keep = settings.history_limit_per_server
    if keep <= 0:
        return

    rows = await session.scalars(
        select(ServerSnapshot.id)
        .where(ServerSnapshot.server_id == server_id)
        .order_by(ServerSnapshot.collected_at.desc())
        .offset(keep),
    )
    to_delete = list(rows)
    if to_delete:
        await session.execute(delete(ServerSnapshot).where(ServerSnapshot.id.in_(to_delete)))


async def collect_and_store(session: AsyncSession, server: MonitoredServer) -> ServerSnapshot:
    collected_at = datetime.utcnow()
    try:
        metrics, containers, log_alerts = await collect_remote_state(server)
        status, alerts = evaluate_status(server, metrics, containers, log_alerts)
        snapshot = ServerSnapshot(
            server_id=server.id,
            collected_at=collected_at,
            status=status,
            alerts=alerts,
            error_message=None,
            cpu_cores=metrics.cpu_cores,
            load_1=metrics.load_1,
            load_5=metrics.load_5,
            load_15=metrics.load_15,
            load_per_core=metrics.load_per_core,
            memory_total_bytes=metrics.memory_total_bytes,
            memory_used_bytes=metrics.memory_used_bytes,
            memory_percent=metrics.memory_percent,
            disk_path=metrics.disk_path,
            disk_total_bytes=metrics.disk_total_bytes,
            disk_used_bytes=metrics.disk_used_bytes,
            disk_percent=metrics.disk_percent,
            uptime_seconds=metrics.uptime_seconds,
            containers_total=len(containers),
            containers_running=sum(1 for item in containers if item.is_running),
            containers_unhealthy=sum(1 for item in containers if not item.is_healthy),
            log_alerts_total=len(log_alerts),
            raw_payload=metrics.raw_payload,
            containers=[
                ContainerSnapshot(
                    container_id=item.container_id,
                    name=item.name,
                    image=item.image,
                    state=item.state,
                    status=item.status,
                    health=item.health,
                    is_running=item.is_running,
                    is_healthy=item.is_healthy,
                )
                for item in containers
            ],
            log_alerts=[
                ContainerLogAlert(
                    container_name=item.container_name,
                    container_id=item.container_id,
                    severity=item.severity,
                    match_count=item.match_count,
                    matched_patterns=item.matched_patterns,
                    excerpt_lines=item.excerpt_lines,
                    collection_error=item.collection_error,
                )
                for item in log_alerts
            ],
        )
        server.last_status = status
        server.last_error = "; ".join(alerts) if alerts else None
        session.add(snapshot)
        session.add(server)
        await session.flush()

        automation_events = await process_automation_for_snapshot(session, server, snapshot, log_alerts)
        if automation_events:
            automation_alerts = [describe_automation_event(event) for event in automation_events]
            snapshot.alerts = [*(snapshot.alerts or []), *automation_alerts]
            raw_payload = dict(snapshot.raw_payload or {})
            raw_payload["automation_events"] = [
                {
                    "id": event.id,
                    "action_status": event.action_status,
                    "detected_at": event.detected_at.isoformat(),
                    "executed_at": event.executed_at.isoformat() if event.executed_at else None,
                    "container_name": event.container_name,
                    "trigger_pattern": event.trigger_pattern,
                    "command": event.command,
                    "cooldown_until": event.cooldown_until.isoformat() if event.cooldown_until else None,
                }
                for event in automation_events
            ]
            snapshot.raw_payload = raw_payload
            server.last_error = "; ".join(snapshot.alerts) if snapshot.alerts else None
    except (RemoteExecutionError, ValueError, json.JSONDecodeError) as exc:
        snapshot = ServerSnapshot(
            server_id=server.id,
            collected_at=collected_at,
            status="critical",
            alerts=[str(exc)],
            error_message=str(exc),
            disk_path=server.root_disk_path,
            raw_payload={"error": str(exc)},
        )
        server.last_status = "critical"
        server.last_error = str(exc)

    server.last_checked_at = collected_at
    session.add(snapshot)
    session.add(server)
    await session.flush()
    await trim_history(session, server.id)
    await session.commit()
    await session.refresh(snapshot)

    loaded = await session.scalar(
        select(ServerSnapshot)
        .options(
            selectinload(ServerSnapshot.containers),
            selectinload(ServerSnapshot.log_alerts),
            selectinload(ServerSnapshot.automation_events),
        )
        .where(ServerSnapshot.id == snapshot.id),
    )
    if loaded is None:
        raise RuntimeError("Snapshot nao encontrado apos persistencia.")
    return loaded


async def collect_server_by_id(session: AsyncSession, server_id: int) -> ServerSnapshot:
    server = await session.get(MonitoredServer, server_id)
    if server is None:
        raise ValueError("Servidor nao encontrado.")
    return await collect_and_store(session, server)


async def build_dashboard(session: AsyncSession) -> dict[str, int]:
    total_servers = await session.scalar(select(func.count()).select_from(MonitoredServer)) or 0
    healthy_servers = await session.scalar(
        select(func.count()).select_from(MonitoredServer).where(MonitoredServer.last_status == "healthy"),
    ) or 0
    warning_servers = await session.scalar(
        select(func.count()).select_from(MonitoredServer).where(MonitoredServer.last_status == "warning"),
    ) or 0
    critical_servers = await session.scalar(
        select(func.count()).select_from(MonitoredServer).where(MonitoredServer.last_status == "critical"),
    ) or 0
    unknown_servers = total_servers - healthy_servers - warning_servers - critical_servers
    return {
        "total_servers": total_servers,
        "healthy_servers": healthy_servers,
        "warning_servers": warning_servers,
        "critical_servers": critical_servers,
        "unknown_servers": unknown_servers,
    }
