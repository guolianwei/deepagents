"""Phase 7: End-to-End User-Scope Flow Integration Tests.

Verifies user isolation and container sharing across threads using TestClient.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from typing import Generator, Any
from unittest.mock import MagicMock

# Add parent directory to sys.path to allow importing from a folder containing hyphens
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

from assistants import sandbox_factories
from server import app, get_db


class MockSandbox:
    """Mock sandbox to simulate file reads and writes per container ID."""

    # Class-level dictionary mapping container_id to a simulated filesystem dictionary
    filesystems: dict[str, dict[str, str]] = {}

    def __init__(self, container_id: str) -> None:
        """Initialize mock sandbox with container ID.

        Args:
            container_id: The ID of the simulated container.
        """
        self.id = container_id
        if self.id not in MockSandbox.filesystems:
            MockSandbox.filesystems[self.id] = {}

    def execute(self, cmd: str) -> MagicMock:
        """Simulate executing a command in the sandbox.

        Supports echo-to-file and cat commands.

        Args:
            cmd: The shell command to execute.

        Returns:
            A MagicMock representing the command execution result.
        """
        cmd = cmd.strip()

        # Match echo 'content' > filepath
        write_match = re.match(r"^echo\s+['\"](.*?)['\"]\s*>\s*(.+)$", cmd)
        if write_match:
            content, filepath = write_match.groups()
            MockSandbox.filesystems[self.id][filepath.strip()] = content
            res = MagicMock()
            res.exit_code = 0
            res.output = ""
            return res

        # Match cat filepath
        cat_match = re.match(r"^cat\s+(.+)$", cmd)
        if cat_match:
            filepath = cat_match.group(1).strip()
            res = MagicMock()
            if filepath in MockSandbox.filesystems[self.id]:
                res.exit_code = 0
                res.output = MockSandbox.filesystems[self.id][filepath]
            else:
                res.exit_code = 1
                res.output = f"cat: {filepath}: No such file or directory"
            return res

        # Fallback default response
        res = MagicMock()
        res.exit_code = 0
        res.output = f"Executed mock command: {cmd}"
        return res


@pytest.fixture
def client(db_conn: sqlite3.Connection) -> Generator[TestClient, None, None]:
    """Provides a TestClient with overridden get_db dependency to point to the in-memory test database.

    Args:
        db_conn: In-memory database fixture connection.

    Yields:
        An active FastAPI TestClient instance.
    """
    app.dependency_overrides[get_db] = lambda: db_conn
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_user_scope_sharing_and_isolation(client: TestClient) -> None:
    """Verifies that threads for the same user share a container, while different users are isolated.

    Scenario:
        1. Register and login Alice.
        2. Register and login Bob.
        3. Register the assistant 'coder'.
        4. Alice starts Thread A1, executes a write command to `/workspace/shared.txt`.
        5. Alice starts Thread A2, executes a read command on `/workspace/shared.txt`, confirming it shares container A.
        6. Bob starts Thread B1, executes a read command on `/workspace/shared.txt`, confirming Bob is in container B and cannot see Alice's file.

    Args:
        client: The test client instance.
    """
    # 1. Register and login Alice
    alice_reg = client.post("/api/v1/auth/register", json={"username": "alice", "password": "password123"}).json()
    alice_id = alice_reg["id"]
    alice_token = client.post("/api/v1/auth/login", json={"username": "alice", "password": "password123"}).json()["access_token"]
    alice_headers = {"Authorization": f"Bearer {alice_token}"}

    # 2. Register and login Bob
    bob_reg = client.post("/api/v1/auth/register", json={"username": "bob", "password": "password123"}).json()
    bob_id = bob_reg["id"]
    bob_token = client.post("/api/v1/auth/login", json={"username": "bob", "password": "password123"}).json()["access_token"]
    bob_headers = {"Authorization": f"Bearer {bob_token}"}

    # 3. Register the assistant 'coder' (using Alice's credentials)
    assistant_payload = {
        "id": "coder",
        "name": "Coder Assistant",
        "model": "anthropic:claude-3-5-sonnet",
        "image": "python:3.12-slim",
        "base_dir": "/workspace",
        "config": {}
    }
    client.post("/api/v1/assistants", json=assistant_payload, headers=alice_headers)

    # 4. Mock the Sandbox compilation and injection for coder assistant
    user_containers: dict[str, MockSandbox] = {}

    def mock_factory(ctx: Any) -> MagicMock:
        identity = ctx.server_info.user.identity
        if identity not in user_containers:
            user_containers[identity] = MockSandbox(f"container-for-{identity}")
        
        mock_sandbox_holder = MagicMock()
        mock_sandbox_holder.default = user_containers[identity]
        return mock_sandbox_holder

    sandbox_factories["coder"] = mock_factory

    # Inject mock docker client to server's global docker_client to bypass simulation mode
    import server
    old_client = server.docker_client
    server.docker_client = MagicMock()

    try:
        # 5. Alice starts Thread A1
        th_a1 = client.post("/api/v1/threads", json={"assistant_id": "coder", "name": "Alice Thread 1"}, headers=alice_headers).json()
        thread_a1_id = th_a1["thread_id"]

        # Alice writes to thread A1
        res_write = client.post(
            f"/api/v1/threads/{thread_a1_id}/chat",
            json={"message": "run: echo 'alice-secret-data' > /workspace/shared.txt"},
            headers=alice_headers
        ).json()
        assert res_write["container_id"] == f"container-for-{alice_id}"

        # 6. Alice starts Thread A2
        th_a2 = client.post("/api/v1/threads", json={"assistant_id": "coder", "name": "Alice Thread 2"}, headers=alice_headers).json()
        thread_a2_id = th_a2["thread_id"]

        # Alice reads from thread A2
        res_read = client.post(
            f"/api/v1/threads/{thread_a2_id}/chat",
            json={"message": "run: cat /workspace/shared.txt"},
            headers=alice_headers
        ).json()
        assert res_read["container_id"] == f"container-for-{alice_id}"
        assert "alice-secret-data" in res_read["response"]

        # 7. Bob starts Thread B1
        th_b1 = client.post("/api/v1/threads", json={"assistant_id": "coder", "name": "Bob Thread 1"}, headers=bob_headers).json()
        thread_b1_id = th_b1["thread_id"]

        # Bob tries to read the file in thread B1
        res_bob = client.post(
            f"/api/v1/threads/{thread_b1_id}/chat",
            json={"message": "run: cat /workspace/shared.txt"},
            headers=bob_headers
        ).json()
        assert res_bob["container_id"] == f"container-for-{bob_id}"
        assert "alice-secret-data" not in res_bob["response"]
        assert "No such file or directory" in res_bob["response"]

    finally:
        # Restore server docker client and clean up factory cache
        server.docker_client = old_client
        sandbox_factories.pop("coder", None)
        MockSandbox.filesystems.clear()
