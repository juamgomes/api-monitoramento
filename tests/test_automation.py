from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import normalize_automation_configuration
from app.services.automation import select_automation_candidate
from app.services.collector import CollectedLogAlert


def build_server(**overrides):
    base = {
        "id": 1,
        "monitor_container_logs": True,
        "automation_enabled": True,
        "automation_target_container": "cess-atualizao-25jul2024-cess-1",
        "automation_trigger_pattern": "untrusted",
        "automation_command": "sh reiniciar_certificado.sh",
        "automation_cooldown_seconds": 600,
        "log_monitored_containers": [],
        "log_error_patterns": ["error"],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


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
