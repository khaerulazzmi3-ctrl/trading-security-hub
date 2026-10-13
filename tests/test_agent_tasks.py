"""Initial tests for the access-governance and bounded Agent Task Engine.

Run with: pytest -q
Tests use a temporary SQLite database and never connect to a broker or external AI.
"""
import importlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("ADMIN_BOOTSTRAP_TOKEN", "test-bootstrap-token-long-enough")
    # Reload so module-level configuration uses the temporary database.
    import app.main as main
    main.DB_PATH = db_path
    import app.agent_tasks as agent_tasks
    agent_tasks.DB_PATH = db_path
    main.init_db()
    agent_tasks.init_agent_task_tables()
    with TestClient(main.app) as test_client:
        yield test_client, db_path


def bootstrap_and_login(client):
    test_client, _ = client
    response = test_client.post(
        "/auth/register",
        headers={"X-Bootstrap-Token": "test-bootstrap-token-long-enough"},
        json={"username": "owner_admin", "password": "test-password-123"},
    )
    assert response.status_code == 201, response.text
    response = test_client.post(
        "/auth/login",
        json={"username": "owner_admin", "password": "test-password-123"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_bootstrap_admin_is_one_time(client):
    test_client, _ = client
    headers = {"X-Bootstrap-Token": "test-bootstrap-token-long-enough"}
    first = test_client.post(
        "/auth/register", headers=headers,
        json={"username": "owner_admin", "password": "test-password-123"},
    )
    second = test_client.post(
        "/auth/register", headers=headers,
        json={"username": "another_admin", "password": "test-password-123"},
    )
    assert first.status_code == 201
    assert second.status_code == 403


def test_agent_task_lifecycle_and_owner_scope(client):
    test_client, _ = client
    headers = bootstrap_and_login(client)
    created = test_client.post(
        "/agent/tasks", headers=headers,
        json={"objective": "Check application configuration", "kind": "system_check"},
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    assert created.json()["status"] == "queued"

    detail = test_client.get(f"/agent/tasks/{task_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["events"][0]["event_type"] == "queued"

    ran = test_client.post(
        f"/agent/tasks/{task_id}/run", headers=headers,
        json={"confirm_run": True},
    )
    assert ran.status_code == 200, ran.text
    assert ran.json()["status"] == "completed"
    assert ran.json()["result"]["live_trading_enabled"] is False

    rerun = test_client.post(
        f"/agent/tasks/{task_id}/run", headers=headers,
        json={"confirm_run": True},
    )
    assert rerun.status_code == 409


def test_agent_task_requires_authentication(client):
    test_client, _ = client
    response = test_client.post(
        "/agent/tasks",
        json={"objective": "Check application configuration"},
    )
    assert response.status_code == 401
