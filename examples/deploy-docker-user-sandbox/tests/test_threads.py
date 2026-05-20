"""Unit tests for Phase 3 (Thread Ownership).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Add parent directory to sys.path to allow importing from a folder containing hyphens
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi import HTTPException

from assistants import create_assistant
from auth import register_user
from threads import create_thread, list_threads, load_thread_for_user


def test_thread_creation_and_ownership(db_conn: sqlite3.Connection) -> None:
    """Test standard thread creation, listing, and ownership validation."""
    # 1. Register users and assistant
    user_a = register_user(db_conn, "user_a", "password_a")
    user_b = register_user(db_conn, "user_b", "password_b")
    
    create_assistant(db_conn, "coder", "Coding Assistant", "gpt-4", "python:3.12-slim", "/workspace", {})
    
    # 2. Create threads for User A
    thread_1 = create_thread(db_conn, user_a["id"], "coder", "Thread 1")
    thread_2 = create_thread(db_conn, user_a["id"], "coder", "Thread 2")
    
    assert thread_1["thread_id"].startswith("thd_")
    assert thread_1["user_id"] == user_a["id"]
    
    # 3. List threads for User A & User B
    threads_a = list_threads(db_conn, user_a["id"])
    threads_b = list_threads(db_conn, user_b["id"])
    
    assert len(threads_a) == 2
    assert len(threads_b) == 0
    assert threads_a[0]["id"] == thread_1["thread_id"]
    
    # 4. Load thread successfully for owner
    loaded_1 = load_thread_for_user(db_conn, thread_1["thread_id"], user_a["id"])
    assert loaded_1["id"] == thread_1["thread_id"]
    
    # 5. Load thread fails with 403 Forbidden for non-owner
    with pytest.raises(HTTPException) as exc_info:
        load_thread_for_user(db_conn, thread_1["thread_id"], user_b["id"])
    assert exc_info.value.status_code == 403
    assert "Forbidden" in exc_info.value.detail
    
    # 6. Load thread fails with 404 Not Found for non-existent thread
    with pytest.raises(HTTPException) as exc_info:
        load_thread_for_user(db_conn, "non_existent_thread_id", user_a["id"])
    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail.lower()


def test_thread_creation_missing_assistant(db_conn: sqlite3.Connection) -> None:
    """Test that creating a thread binds to a valid assistant."""
    user = register_user(db_conn, "user_c", "password_c")
    
    # Attempting to start a thread with a missing assistant ID should raise 404
    with pytest.raises(HTTPException) as exc_info:
        create_thread(db_conn, user["id"], "missing-assistant-id", "Thread Fail")
    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail.lower()
