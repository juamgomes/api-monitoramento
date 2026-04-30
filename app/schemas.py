from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.constants import DEFAULT_LOG_ERROR_PATTERNS, DEFAULT_LOG_TAIL_LINES
from app.constants import DEFAULT_AUTOMATION_COOLDOWN_SECONDS, DEFAULT_AUTOMATION_TRIGGER_PATTERN


class ServerBase(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    host: str = Field(min_length=2, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=120)
    ssh_auth_mode: str = Field(default="password", pattern="^(password|private_key)$")
    monitor_docker: bool = True
    watch_all_containers: bool = True
    expected_containers: list[str] = Field(default_factory=list)
    monitor_container_logs: bool = False
    log_monitored_containers: list[str] = Field(default_factory=list)
    log_tail_lines: int = Field(default=DEFAULT_LOG_TAIL_LINES, ge=1, le=5000)
    log_error_patterns: list[str] = Field(default_factory=lambda: DEFAULT_LOG_ERROR_PATTERNS.copy())
    automation_enabled: bool = False
    automation_target_container: str | None = Field(default=None, min_length=1, max_length=255)
    automation_trigger_pattern: str | None = Field(
        default=DEFAULT_AUTOMATION_TRIGGER_PATTERN,
        min_length=1,
        max_length=255,
    )
    automation_command: str | None = Field(default=None, min_length=1)
    automation_cooldown_seconds: int = Field(default=DEFAULT_AUTOMATION_COOLDOWN_SECONDS, ge=0, le=86400)
    enabled: bool = True
    root_disk_path: str = Field(default="/", min_length=1, max_length=255)
    warning_disk_percent: int = Field(default=80, ge=1, le=100)
    critical_disk_percent: int = Field(default=90, ge=1, le=100)
    warning_memory_percent: int = Field(default=80, ge=1, le=100)
    critical_memory_percent: int = Field(default=90, ge=1, le=100)
    warning_load_per_core: float = Field(default=0.7, ge=0)
    critical_load_per_core: float = Field(default=1.0, ge=0)

    @field_validator("expected_containers", mode="before")
    @classmethod
    def normalize_containers(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = value.split(",")
            return [item.strip() for item in raw_items if item.strip()]
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("log_monitored_containers", "log_error_patterns", mode="before")
    @classmethod
    def normalize_string_lists(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = value.split(",")
            return [item.strip() for item in raw_items if item.strip()]
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("critical_disk_percent")
    @classmethod
    def validate_disk_thresholds(cls, value: int, info) -> int:
        warning = info.data.get("warning_disk_percent")
        if warning is not None and value < warning:
            raise ValueError("critical_disk_percent deve ser maior ou igual ao warning_disk_percent")
        return value

    @field_validator("critical_memory_percent")
    @classmethod
    def validate_memory_thresholds(cls, value: int, info) -> int:
        warning = info.data.get("warning_memory_percent")
        if warning is not None and value < warning:
            raise ValueError("critical_memory_percent deve ser maior ou igual ao warning_memory_percent")
        return value

    @field_validator("critical_load_per_core")
    @classmethod
    def validate_load_thresholds(cls, value: float, info) -> float:
        warning = info.data.get("warning_load_per_core")
        if warning is not None and value < warning:
            raise ValueError("critical_load_per_core deve ser maior ou igual ao warning_load_per_core")
        return value


class ServerCreate(ServerBase):
    ssh_password: str | None = None
    ssh_private_key: str | None = None
    ssh_passphrase: str | None = None

    @model_validator(mode="after")
    def validate_auth_payload(self) -> "ServerCreate":
        if self.ssh_auth_mode == "password" and not self.ssh_password:
            raise ValueError("ssh_password e obrigatorio quando ssh_auth_mode=password")
        if self.ssh_auth_mode == "private_key" and not self.ssh_private_key:
            raise ValueError("ssh_private_key e obrigatorio quando ssh_auth_mode=private_key")
        if self.monitor_container_logs and not self.monitor_docker:
            raise ValueError("monitor_container_logs exige monitor_docker=true")
        if self.monitor_container_logs and not self.log_monitored_containers:
            raise ValueError("log_monitored_containers deve ter ao menos um container quando monitor_container_logs=true")
        if self.monitor_container_logs and not self.log_error_patterns:
            raise ValueError("log_error_patterns deve ter ao menos um padrao quando monitor_container_logs=true")
        return self


class ServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    host: str | None = Field(default=None, min_length=2, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, min_length=1, max_length=120)
    ssh_auth_mode: str | None = Field(default=None, pattern="^(password|private_key)$")
    ssh_password: str | None = None
    ssh_private_key: str | None = None
    ssh_passphrase: str | None = None
    monitor_docker: bool | None = None
    watch_all_containers: bool | None = None
    expected_containers: list[str] | None = None
    monitor_container_logs: bool | None = None
    log_monitored_containers: list[str] | None = None
    log_tail_lines: int | None = Field(default=None, ge=1, le=5000)
    log_error_patterns: list[str] | None = None
    automation_enabled: bool | None = None
    automation_target_container: str | None = Field(default=None, min_length=1, max_length=255)
    automation_trigger_pattern: str | None = Field(default=None, min_length=1, max_length=255)
    automation_command: str | None = Field(default=None, min_length=1)
    automation_cooldown_seconds: int | None = Field(default=None, ge=0, le=86400)
    enabled: bool | None = None
    root_disk_path: str | None = Field(default=None, min_length=1, max_length=255)
    warning_disk_percent: int | None = Field(default=None, ge=1, le=100)
    critical_disk_percent: int | None = Field(default=None, ge=1, le=100)
    warning_memory_percent: int | None = Field(default=None, ge=1, le=100)
    critical_memory_percent: int | None = Field(default=None, ge=1, le=100)
    warning_load_per_core: float | None = Field(default=None, ge=0)
    critical_load_per_core: float | None = Field(default=None, ge=0)

    @field_validator("expected_containers", "log_monitored_containers", "log_error_patterns", mode="before")
    @classmethod
    def normalize_optional_containers(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            raw_items = value.split(",")
            return [item.strip() for item in raw_items if item.strip()]
        return [str(item).strip() for item in value if str(item).strip()]


class ContainerSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    container_id: str | None
    name: str
    image: str | None
    state: str | None
    status: str | None
    health: str | None
    is_running: bool
    is_healthy: bool


class ContainerLogAlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    container_name: str
    container_id: str | None
    severity: str
    match_count: int
    matched_patterns: list[str]
    excerpt_lines: list[str]
    collection_error: str | None


class AutomationEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    server_id: int
    server_name: str | None = None
    snapshot_id: int | None
    detected_at: datetime
    executed_at: datetime | None
    container_name: str
    container_id: str | None
    trigger_pattern: str
    trigger_signature: str
    command: str
    action_status: str
    match_count: int
    matched_patterns: list[str]
    excerpt_lines: list[str]
    command_output: str | None
    error_message: str | None
    cooldown_until: datetime | None


class ServerSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    collected_at: datetime
    status: str
    alerts: list[str]
    error_message: str | None
    cpu_cores: int | None
    load_1: float | None
    load_5: float | None
    load_15: float | None
    load_per_core: float | None
    memory_total_bytes: int | None
    memory_used_bytes: int | None
    memory_percent: float | None
    disk_path: str
    disk_total_bytes: int | None
    disk_used_bytes: int | None
    disk_percent: float | None
    uptime_seconds: float | None
    containers_total: int
    containers_running: int
    containers_unhealthy: int
    log_alerts_total: int
    raw_payload: dict[str, Any] | None
    containers: list[ContainerSnapshotRead] = Field(default_factory=list)
    log_alerts: list[ContainerLogAlertRead] = Field(default_factory=list)
    automation_events: list[AutomationEventRead] = Field(default_factory=list)


class ServerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    host: str
    port: int
    username: str
    ssh_auth_mode: str
    monitor_docker: bool
    watch_all_containers: bool
    expected_containers: list[str]
    monitor_container_logs: bool
    log_monitored_containers: list[str]
    log_tail_lines: int
    log_error_patterns: list[str]
    automation_enabled: bool
    automation_target_container: str | None
    automation_trigger_pattern: str | None
    automation_command: str | None
    automation_cooldown_seconds: int
    automation_configured: bool
    automation_active: bool
    automation_status: Literal["active", "paused", "misconfigured"]
    automation_status_reason: str | None
    enabled: bool
    root_disk_path: str
    warning_disk_percent: int
    critical_disk_percent: int
    warning_memory_percent: int
    critical_memory_percent: int
    warning_load_per_core: float
    critical_load_per_core: float
    has_password: bool
    has_private_key: bool
    has_passphrase: bool
    last_checked_at: datetime | None
    last_status: str
    last_error: str | None
    last_automation_at: datetime | None
    last_automation_status: str | None
    created_at: datetime
    updated_at: datetime


class AutomationStatusRead(BaseModel):
    server_id: int
    server_name: str
    automation_enabled: bool
    automation_configured: bool
    automation_active: bool
    automation_status: Literal["active", "paused", "misconfigured"]
    automation_status_reason: str | None
    monitor_container_logs: bool
    automation_target_container: str | None
    automation_trigger_pattern: str | None
    automation_command: str | None
    automation_cooldown_seconds: int
    last_checked_at: datetime | None
    last_automation_at: datetime | None
    last_automation_status: str | None
    updated_at: datetime


class AutomationDashboardSummaryRead(BaseModel):
    total_events: int
    executed_events: int
    failed_events: int
    skipped_events: int
    recent_events: list[AutomationEventRead] = Field(default_factory=list)


class DashboardRead(BaseModel):
    generated_at: datetime
    total_servers: int
    healthy_servers: int
    warning_servers: int
    critical_servers: int
    unknown_servers: int
    automation_summary: AutomationDashboardSummaryRead
    servers: list[ServerRead]
