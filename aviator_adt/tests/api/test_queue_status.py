"""Test Celery queue status endpoint."""

from fastapi.testclient import TestClient


def test_queue_status_endpoint(client: TestClient) -> None:
    """Test that the queue status endpoint returns valid data."""
    response = client.get("/queue-status")

    assert response.status_code == 200
    data = response.json()

    # Verify expected fields exist
    assert "broker_type" in data
    assert "broker_host" in data
    assert "broker_connection" in data
    assert "queues" in data
    assert "workers" in data
    assert "registered_tasks" in data

    # Verify types
    assert isinstance(data["queues"], dict)
    assert isinstance(data["workers"], dict)
    assert isinstance(data["registered_tasks"], list)

    # Verify worker payload shape (when any workers are online)
    for worker_info in data["workers"].values():
        assert isinstance(worker_info, dict)
        assert worker_info.get("status") == "online"
        if "active_tasks" in worker_info:
            assert isinstance(worker_info["active_tasks"], int)
        if "uptime_seconds" in worker_info:
            assert isinstance(worker_info["uptime_seconds"], int)
        if "tasks_executed" in worker_info:
            assert isinstance(worker_info["tasks_executed"], dict)
        if "autoscaler" in worker_info:
            assert isinstance(worker_info["autoscaler"], dict)
            for key in ("current", "min", "max"):
                if key in worker_info["autoscaler"]:
                    assert isinstance(worker_info["autoscaler"][key], int)


def test_queue_status_registered_tasks(client: TestClient) -> None:
    """Test that registered tasks are included in the response."""
    response = client.get("/queue-status")

    assert response.status_code == 200
    data = response.json()

    # Should have at least the process_embedding_request task
    registered_tasks = data["registered_tasks"]
    assert "aviator.celery.process_embedding_request" in registered_tasks
