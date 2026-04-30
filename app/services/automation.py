from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.constants import DEFAULT_AUTOMATION_OUTPUT_MAX_CHARS
from app.models import AutomationEvent, MonitoredServer, ServerSnapshot
from app.services.ssh import RemoteExecutionError, open_connection, run_command

if TYPE_CHECKING:
    from app.services.collector import CollectedLogAlert


@dataclass(slots=True)
class AutomationCandidate:
    container_name: str
    container_id: str | None
    trigger_pattern: str
    trigger_signature: str
    match_count: int
    matched_patterns: list[str]
    excerpt_lines: list[str]


def compile_trigger_regex(pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(pattern), re.IGNORECASE)


def extract_trigger_lines(log_alert: CollectedLogAlert, trigger_pattern: str) -> list[str]:
    regex = compile_trigger_regex(trigger_pattern)
    source_lines = getattr(log_alert, "matched_lines", None) or log_alert.excerpt_lines
    return [line for line in source_lines if regex.search(line)]


def build_trigger_signature(
    server_id: int,
    container_name: str,
    trigger_pattern: str,
    excerpt_lines: list[str],
) -> str:
    joined = "\n".join(excerpt_lines[-10:])
    raw = f"{server_id}|{container_name}|{trigger_pattern}|{joined}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def select_automation_candidate(
    server: MonitoredServer,
    log_alerts: list[CollectedLogAlert],
) -> AutomationCandidate | None:
    if not server.automation_enabled:
        return None
    if not server.automation_target_container or not server.automation_trigger_pattern or not server.automation_command:
        return None

    for log_alert in log_alerts:
        if log_alert.container_name != server.automation_target_container:
            continue
        if log_alert.collection_error:
            continue

        trigger_lines = extract_trigger_lines(log_alert, server.automation_trigger_pattern)
        if not trigger_lines:
            continue

        excerpt_lines = trigger_lines[-10:]
        return AutomationCandidate(
            container_name=log_alert.container_name,
            container_id=log_alert.container_id,
            trigger_pattern=server.automation_trigger_pattern,
            trigger_signature=build_trigger_signature(
                server.id,
                log_alert.container_name,
                server.automation_trigger_pattern,
                excerpt_lines,
            ),
            match_count=len(trigger_lines),
            matched_patterns=log_alert.matched_patterns,
            excerpt_lines=excerpt_lines,
        )

    return None


def truncate_output(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) <= DEFAULT_AUTOMATION_OUTPUT_MAX_CHARS:
        return normalized
    return f"{normalized[:DEFAULT_AUTOMATION_OUTPUT_MAX_CHARS]}... [truncado]"


def describe_automation_event(event: AutomationEvent) -> str:
    if event.action_status == "executed":
        return (
            f"Automacao executada para o container {event.container_name} apos detectar "
            f"'{event.trigger_pattern}'."
        )
    if event.action_status == "failed":
        return (
            f"Automacao falhou para o container {event.container_name} apos detectar "
            f"'{event.trigger_pattern}': {event.error_message or 'sem detalhes'}"
        )
    if event.action_status == "skipped_cooldown":
        until = event.cooldown_until.isoformat() if event.cooldown_until else "indefinido"
        return (
            f"Automacao ignorada para o container {event.container_name} porque o cooldown ainda "
            f"estava ativo ate {until}."
        )
    return f"Automacao registrada para o container {event.container_name}."


async def trim_automation_history(session: AsyncSession, server_id: int) -> None:
    settings = get_settings()
    keep = settings.automation_history_limit_per_server
    if keep <= 0:
        return

    rows = await session.scalars(
        select(AutomationEvent.id)
        .where(AutomationEvent.server_id == server_id)
        .order_by(AutomationEvent.detected_at.desc())
        .offset(keep),
    )
    to_delete = list(rows)
    if to_delete:
        await session.execute(delete(AutomationEvent).where(AutomationEvent.id.in_(to_delete)))


async def execute_automation_command(server: MonitoredServer, command: str) -> str | None:
    settings = get_settings()
    async with await open_connection(server) as connection:
        output = await run_command(
            connection,
            command,
            timeout=settings.automation_command_timeout_seconds,
        )
    return truncate_output(output)


async def process_automation_for_snapshot(
    session: AsyncSession,
    server: MonitoredServer,
    snapshot: ServerSnapshot,
    log_alerts: list[CollectedLogAlert],
) -> list[AutomationEvent]:
    candidate = select_automation_candidate(server, log_alerts)
    if candidate is None:
        return []

    existing_signature = await session.scalar(
        select(AutomationEvent)
        .where(
            AutomationEvent.server_id == server.id,
            AutomationEvent.trigger_signature == candidate.trigger_signature,
        )
        .order_by(AutomationEvent.detected_at.desc()),
    )
    if existing_signature is not None:
        return []

    detected_at = snapshot.collected_at or datetime.utcnow()
    cooldown_until: datetime | None = None
    cooldown_seconds = max(server.automation_cooldown_seconds, 0)

    event = AutomationEvent(
        server_id=server.id,
        snapshot_id=snapshot.id,
        detected_at=detected_at,
        container_name=candidate.container_name,
        container_id=candidate.container_id,
        trigger_pattern=candidate.trigger_pattern,
        trigger_signature=candidate.trigger_signature,
        command=server.automation_command or "",
        match_count=candidate.match_count,
        matched_patterns=candidate.matched_patterns,
        excerpt_lines=candidate.excerpt_lines,
    )

    if cooldown_seconds > 0:
        recent_event = await session.scalar(
            select(AutomationEvent)
            .where(
                AutomationEvent.server_id == server.id,
                AutomationEvent.action_status.in_(("executed", "failed")),
            )
            .order_by(AutomationEvent.detected_at.desc()),
        )
        if recent_event is not None and recent_event.detected_at + timedelta(seconds=cooldown_seconds) > detected_at:
            cooldown_until = recent_event.detected_at + timedelta(seconds=cooldown_seconds)
            event.action_status = "skipped_cooldown"
            event.cooldown_until = cooldown_until
            event.error_message = (
                "Cooldown ativo por causa de uma tentativa recente de automacao."
            )
            server.last_automation_at = detected_at
            server.last_automation_status = event.action_status
            session.add(event)
            await session.flush()
            await trim_automation_history(session, server.id)
            return [event]

    try:
        event.command_output = await execute_automation_command(server, event.command)
        event.executed_at = datetime.utcnow()
        event.action_status = "executed"
        if cooldown_seconds > 0:
            event.cooldown_until = event.executed_at + timedelta(seconds=cooldown_seconds)
    except RemoteExecutionError as exc:
        event.executed_at = datetime.utcnow()
        event.action_status = "failed"
        event.error_message = str(exc)
        if cooldown_seconds > 0:
            event.cooldown_until = event.executed_at + timedelta(seconds=cooldown_seconds)

    server.last_automation_at = event.executed_at or detected_at
    server.last_automation_status = event.action_status
    session.add(event)
    await session.flush()
    await trim_automation_history(session, server.id)
    return [event]


async def build_automation_dashboard_summary(
    session: AsyncSession,
    recent_limit: int = 10,
) -> dict[str, object]:
    total_events = await session.scalar(select(func.count()).select_from(AutomationEvent)) or 0
    executed_events = await session.scalar(
        select(func.count()).select_from(AutomationEvent).where(AutomationEvent.action_status == "executed"),
    ) or 0
    failed_events = await session.scalar(
        select(func.count()).select_from(AutomationEvent).where(AutomationEvent.action_status == "failed"),
    ) or 0
    skipped_events = await session.scalar(
        select(func.count()).select_from(AutomationEvent).where(AutomationEvent.action_status == "skipped_cooldown"),
    ) or 0
    recent_rows = await session.scalars(
        select(AutomationEvent)
        .options(selectinload(AutomationEvent.server))
        .order_by(AutomationEvent.detected_at.desc())
        .limit(recent_limit),
    )
    return {
        "total_events": total_events,
        "executed_events": executed_events,
        "failed_events": failed_events,
        "skipped_events": skipped_events,
        "recent_events": list(recent_rows),
    }
