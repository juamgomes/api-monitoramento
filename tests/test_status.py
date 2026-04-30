from types import SimpleNamespace

from app.services.collector import CollectedContainer, CollectedLogAlert, CollectedMetrics, evaluate_status


def build_server(**overrides):
    base = {
        "root_disk_path": "/",
        "critical_disk_percent": 90,
        "warning_disk_percent": 80,
        "critical_memory_percent": 90,
        "warning_memory_percent": 80,
        "critical_load_per_core": 1.0,
        "warning_load_per_core": 0.7,
        "monitor_docker": True,
        "monitor_container_logs": False,
        "expected_containers": [],
        "log_monitored_containers": [],
        "log_error_patterns": ["error"],
        "watch_all_containers": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_evaluate_status_warns_for_high_memory():
    server = build_server()
    metrics = CollectedMetrics(memory_percent=85.0, disk_percent=40.0, load_per_core=0.2)
    status, alerts = evaluate_status(server, metrics, [], [])

    assert status == "warning"
    assert alerts


def test_evaluate_status_is_critical_when_expected_container_is_missing():
    server = build_server(expected_containers=["api"], watch_all_containers=False)
    metrics = CollectedMetrics(memory_percent=10.0, disk_percent=10.0, load_per_core=0.1)

    status, alerts = evaluate_status(server, metrics, [], [])

    assert status == "critical"
    assert "ausentes" in alerts[0]


def test_evaluate_status_is_critical_when_running_container_is_unhealthy():
    server = build_server()
    metrics = CollectedMetrics(memory_percent=10.0, disk_percent=10.0, load_per_core=0.1)
    containers = [
        CollectedContainer(
            container_id="1",
            name="api",
            image="api:latest",
            state="running",
            status="Up 2 minutes (unhealthy)",
            health="unhealthy",
            is_running=True,
            is_healthy=False,
        ),
    ]

    status, alerts = evaluate_status(server, metrics, containers, [])

    assert status == "critical"
    assert "saude" in alerts[0]


def test_evaluate_status_is_critical_when_docker_collection_fails():
    server = build_server()
    metrics = CollectedMetrics(
        memory_percent=10.0,
        disk_percent=10.0,
        load_per_core=0.1,
        docker_error="Timeout ao executar comando remoto: docker ps -a --format '{{json .}}'",
    )

    status, alerts = evaluate_status(server, metrics, [], [])

    assert status == "critical"
    assert "Falha ao consultar containers Docker" in alerts[0]


def test_evaluate_status_warns_when_container_logs_have_errors():
    server = build_server(monitor_container_logs=True, log_monitored_containers=["api"])
    metrics = CollectedMetrics(memory_percent=10.0, disk_percent=10.0, load_per_core=0.1)
    log_alerts = [
        CollectedLogAlert(
            container_name="api",
            match_count=3,
            matched_patterns=["error", "exception"],
            excerpt_lines=["ERROR database unavailable"],
        ),
    ]

    status, alerts = evaluate_status(server, metrics, [], log_alerts)

    assert status == "warning"
    assert "Erros encontrados nos logs do container api" in alerts[0]
