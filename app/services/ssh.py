from __future__ import annotations

import asyncio
from dataclasses import dataclass

import asyncssh

from app.config import get_settings
from app.models import MonitoredServer
from app.services.crypto import cipher


class RemoteExecutionError(RuntimeError):
    pass


@dataclass(slots=True)
class SSHCredentials:
    password: str | None = None
    private_key: str | None = None
    passphrase: str | None = None


def extract_credentials(server: MonitoredServer) -> SSHCredentials:
    return SSHCredentials(
        password=cipher.decrypt(server.ssh_password_encrypted),
        private_key=cipher.decrypt(server.ssh_private_key_encrypted),
        passphrase=cipher.decrypt(server.ssh_passphrase_encrypted),
    )


async def open_connection(server: MonitoredServer) -> asyncssh.SSHClientConnection:
    settings = get_settings()
    credentials = extract_credentials(server)
    connection_kwargs: dict[str, object] = {
        "host": server.host,
        "port": server.port,
        "username": server.username,
        "known_hosts": None,
        "connect_timeout": settings.connect_timeout_seconds,
    }

    if server.ssh_auth_mode == "private_key":
        if not credentials.private_key:
            raise RemoteExecutionError("Chave privada nao configurada para este servidor.")
        private_key = asyncssh.import_private_key(
            credentials.private_key,
            passphrase=credentials.passphrase,
        )
        connection_kwargs["client_keys"] = [private_key]
    else:
        if not credentials.password:
            raise RemoteExecutionError("Senha SSH nao configurada para este servidor.")
        connection_kwargs["password"] = credentials.password

    try:
        return await asyncssh.connect(**connection_kwargs)
    except (asyncssh.Error, OSError) as exc:
        raise RemoteExecutionError(f"Falha ao conectar por SSH: {exc}") from exc


async def run_command(
    connection: asyncssh.SSHClientConnection,
    command: str,
    timeout: int | None = None,
) -> str:
    settings = get_settings()
    try:
        result = await asyncio.wait_for(
            connection.run(command, check=False),
            timeout=timeout or settings.command_timeout_seconds,
        )
    except TimeoutError as exc:
        raise RemoteExecutionError(f"Timeout ao executar comando remoto: {command}") from exc
    except (asyncssh.Error, OSError) as exc:
        raise RemoteExecutionError(f"Falha ao executar comando remoto: {exc}") from exc

    if result.exit_status != 0:
        stderr = result.stderr.strip() or "sem detalhes"
        raise RemoteExecutionError(
            f"Comando remoto retornou erro ({result.exit_status}): {stderr}",
        )

    return result.stdout.strip()
