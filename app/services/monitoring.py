from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import MonitoredServer
from app.services.collector import collect_and_store


class MonitoringCoordinator:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._semaphore = asyncio.Semaphore(self._settings.max_concurrent_checks)

    async def start(self) -> None:
        if self._task is None:
            self._stop_event.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            await self.collect_enabled_servers()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._settings.monitoring_interval_seconds,
                )
            except TimeoutError:
                continue

    async def collect_enabled_servers(self) -> None:
        async with SessionLocal() as session:
            rows = await session.scalars(
                select(MonitoredServer.id).where(MonitoredServer.enabled.is_(True)).order_by(MonitoredServer.name),
            )
            server_ids = list(rows)

        await asyncio.gather(*(self._collect_with_limit(server_id) for server_id in server_ids))

    async def collect_now(self, server_id: int) -> None:
        await self._collect_with_limit(server_id)

    async def _collect_with_limit(self, server_id: int) -> None:
        async with self._semaphore:
            async with SessionLocal() as session:
                server = await session.get(MonitoredServer, server_id)
                if server is None:
                    return
                await collect_and_store(session, server)
