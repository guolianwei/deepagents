"""Integration tests for Phase 6 (FastAPI Routing Assembly).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Generator

# Add parent directory to sys.path to allow importing from a folder containing hyphens
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

from server import app, get_db


@pytest.fixture
def client(db_conn: sqlite3.Connection) -> Generator[TestClient, None, None]:
    """Provides a TestClient with overridden get_db dependency to point to the in-memory test database."""
    app.dependency_overrides[get_db] = lambda: db_conn
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_auth_registration_and_login_flow(client: TestClient) -> None:
    """Test user registration and successful/failed logins."""
    # 1. Register new user
    reg_response = client.post(
        "/api/v1/auth/register",
        json={"username": "bob_test", "password": "securepassword123"}
    )
    assert reg_response.status_code == 201
    reg_data = reg_response.json()
    assert reg_data["username"] == "bob_test"
    assert "id" in reg_data
    
    # 2. Registering duplicate username should fail
    reg_duplicate = client.post(
        "/api/v1/auth/register",
        json={"username": "bob_test", "password": "differentpassword"}
    )
    assert reg_duplicate.status_code == 400
    assert "already registered" in reg_duplicate.json()["detail"]
    
    # 3. Successful login
    login_ok = client.post(
        "/api/v1/auth/login",
        json={"username": "bob_test", "password": "securepassword123"}
    )
    assert login_ok.status_code == 200
    login_data = login_ok.json()
    assert login_data["token_type"] == "bearer"
    assert "access_token" in login_data
    
    # 4. Failed login
    login_fail = client.post(
        "/api/v1/auth/login",
        json={"username": "bob_test", "password": "wrongpassword"}
    )
    assert login_fail.status_code == 400
    assert "Incorrect username" in login_fail.json()["detail"]


def test_authenticated_assistant_and_thread_flow(client: TestClient) -> None:
    """Test registering assistants, creating threads, and sending chat messages."""
    # 1. Register and login to obtain JWT token
    client.post("/api/v1/auth/register", json={"username": "alice", "password": "password123"})
    token_response = client.post("/api/v1/auth/login", json={"username": "alice", "password": "password123"})
    token = token_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # 2. Register an Assistant (Authenticated)
    assistant_payload = {
        "id": "my-assistant",
        "name": "Custom Assistant",
        "model": "anthropic:claude-3-5-sonnet",
        "image": "python:3.12-slim",
        "base_dir": "/workspace",
        "config": {}
    }
    ass_response = client.post("/api/v1/assistants", json=assistant_payload, headers=headers)
    assert ass_response.status_code == 201
    assert ass_response.json()["id"] == "my-assistant"
    
    # 3. Get Registered Assistants List
    list_ass = client.get("/api/v1/assistants", headers=headers)
    assert list_ass.status_code == 200
    assert len(list_ass.json()) == 1
    assert list_ass.json()[0]["id"] == "my-assistant"
    
    # 4. Start a Thread (Authenticated)
    thread_payload = {"assistant_id": "my-assistant", "name": "Test Conversation"}
    th_response = client.post("/api/v1/threads", json=thread_payload, headers=headers)
    assert th_response.status_code == 201
    thread_data = th_response.json()
    assert "thread_id" in thread_data
    assert thread_data["name"] == "Test Conversation"
    
    # 5. List Threads
    list_th = client.get("/api/v1/threads", headers=headers)
    assert list_th.status_code == 200
    assert len(list_th.json()) == 1
    assert list_th.json()[0]["id"] == thread_data["thread_id"]
    
    # 6. Send Chat Message and Trigger Sandbox Simulation (Authenticated)
    chat_response = client.post(
        f"/api/v1/threads/{thread_data['thread_id']}/chat",
        json={"message": "Hello Assistant!"},
        headers=headers
    )
    assert chat_response.status_code == 200
    chat_data = chat_response.json()
    assert "Hi! I am the assistant" in chat_data["response"]
    assert "sim-" in chat_data["container_id"]
    assert chat_data["thread_id"] == thread_data["thread_id"]
