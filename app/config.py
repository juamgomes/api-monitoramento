import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = BASE_DIR / "data" / "monitoring.db"
DEFAULT_KEY_PATH = BASE_DIR / "data" / "secret.key"
DEFAULT_CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:4173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:4173",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "API Monitoramento"
    app_env: str = "development"
    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{DEFAULT_DATABASE_PATH.as_posix()}",
        alias="DATABASE_URL",
    )
    monitoring_interval_seconds: int = Field(default=60, alias="MONITORING_INTERVAL_SECONDS")
    history_limit_per_server: int = Field(default=100, alias="HISTORY_LIMIT_PER_SERVER")
    connect_timeout_seconds: int = Field(default=10, alias="CONNECT_TIMEOUT_SECONDS")
    command_timeout_seconds: int = Field(default=20, alias="COMMAND_TIMEOUT_SECONDS")
    docker_command_timeout_seconds: int = Field(default=45, alias="DOCKER_COMMAND_TIMEOUT_SECONDS")
    docker_logs_command_timeout_seconds: int = Field(default=120, alias="DOCKER_LOGS_COMMAND_TIMEOUT_SECONDS")
    docker_logs_fallback_tail_lines: int = Field(default=50, alias="DOCKER_LOGS_FALLBACK_TAIL_LINES")
    automation_command_timeout_seconds: int = Field(default=120, alias="AUTOMATION_COMMAND_TIMEOUT_SECONDS")
    automation_history_limit_per_server: int = Field(default=500, alias="AUTOMATION_HISTORY_LIMIT_PER_SERVER")
    max_concurrent_checks: int = Field(default=5, alias="MAX_CONCURRENT_CHECKS")
    api_key: SecretStr | None = Field(default=None, alias="API_KEY")
    monitoring_encryption_key: SecretStr | None = Field(default=None, alias="MONITORING_ENCRYPTION_KEY")
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: DEFAULT_CORS_ALLOWED_ORIGINS.copy(),
        alias="CORS_ALLOWED_ORIGINS",
    )
    cors_allowed_origin_regex: str | None = Field(
        default=r"https://.*\.lovable\.app",
        alias="CORS_ALLOWED_ORIGIN_REGEX",
    )
    cors_allow_credentials: bool = Field(default=False, alias="CORS_ALLOW_CREDENTIALS")

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def parse_cors_allowed_origins(cls, value: Any) -> list[str]:
        if value is None:
            return DEFAULT_CORS_ALLOWED_ORIGINS.copy()
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return []
            if normalized == "*":
                return ["*"]
            if normalized.startswith("["):
                parsed = json.loads(normalized)
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in normalized.split(",") if item.strip()]
        return [str(item).strip() for item in value if str(item).strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
