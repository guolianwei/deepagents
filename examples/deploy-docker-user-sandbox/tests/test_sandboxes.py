"""Unit tests for Phase 5 (Sandbox Metadata & Docker Reaper).
"""

from __future__ import annotations

import datetime
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add parent directory to sys.path to allow importing from a folder containing hyphens
sys.path.insert(0, str(Path(__file__).parent.parent))

from reaper import reap_idle_containers
from sandboxes import list_sandboxes, register_sandbox


def test_sandbox_registration_and_listing(db_conn: sqlite3.Connection) -> None:
    """Test registering active sandboxes and listing them."""
    cache_key_1 = "user:coder:usr_111"
    container_id_1 = "container_111_abc"
    
    # 1. Register sandbox
    register_sandbox(db_conn, cache_key_1, container_id_1)
    
    # 2. Query sandboxes
    active = list_sandboxes(db_conn)
    assert len(active) == 1
    assert active[0]["cache_key"] == cache_key_1
    assert active[0]["container_id"] == container_id_1
    assert active[0]["status"] == "running"
    
    # 3. Update existing sandbox with new container ID
    new_container_id = "container_updated_222"
    register_sandbox(db_conn, cache_key_1, new_container_id)
    
    active_updated = list_sandboxes(db_conn)
    assert len(active_updated) == 1
    assert active_updated[0]["container_id"] == new_container_id


def test_reaper_cleans_idle_containers(db_conn: sqlite3.Connection) -> None:
    """Test that the reaper correctly identifies, stops, and removes containers exceeding idle TTL."""
    # 1. Setup mock docker containers
    mock_container_idle = MagicMock()
    mock_container_idle.id = "cont_idle_999"
    mock_container_idle.name = "sandbox-alice-idle"
    
    mock_container_active = MagicMock()
    mock_container_active.id = "cont_active_888"
    mock_container_active.name = "sandbox-bob-active"
    
    mock_docker_client = MagicMock()
    mock_docker_client.containers.list.return_value = [mock_container_idle, mock_container_active]
    
    # 2. Add records in database
    # Idle sandbox (active 2 hours ago)
    two_hours_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)).isoformat()
    db_conn.execute(
        "INSERT INTO sandboxes (cache_key, container_id, status, last_active_at) VALUES (?, ?, 'running', ?)",
        ("user:coder:alice", "cont_idle_999", two_hours_ago)
    )
    # Active sandbox (active 10 seconds ago)
    just_now = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=10)).isoformat()
    db_conn.execute(
        "INSERT INTO sandboxes (cache_key, container_id, status, last_active_at) VALUES (?, ?, 'running', ?)",
        ("user:coder:bob", "cont_active_888", just_now)
    )
    db_conn.commit()
    
    # 3. Execute reaping with 1-hour idle threshold (3600 seconds)
    reaped = reap_idle_containers(db_conn, max_idle_seconds=3600, docker_client=mock_docker_client)
    
    # 4. Verify outcomes
    assert "cont_idle_999" in reaped
    assert "cont_active_888" not in reaped
    
    # Idle container should have been stopped and removed
    mock_container_idle.stop.assert_called_once_with(timeout=5)
    mock_container_idle.remove.assert_called_once_with(force=True)
    
    # Active container should NOT have been touched
    mock_container_active.stop.assert_not_called()
    mock_container_active.remove.assert_not_called()
    
    # Database status of reaped container should be 'stopped'
    row_idle = db_conn.execute("SELECT status FROM sandboxes WHERE container_id = 'cont_idle_999'").fetchone()
    row_active = db_conn.execute("SELECT status FROM sandboxes WHERE container_id = 'cont_active_888'").fetchone()
    
    assert row_idle["status"] == "stopped"
    assert row_active["status"] == "running"
