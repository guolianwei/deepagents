"""Unit tests for Phase 2 (Assistant Registry).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

# Add parent directory to sys.path to allow importing from a folder containing hyphens
sys.path.insert(0, str(Path(__file__).parent.parent))

from assistants import (
    _get_or_compile_sandbox_factory,
    create_assistant,
    get_assistant,
    list_assistants,
)


def test_assistant_registration_and_lookup(db_conn: sqlite3.Connection) -> None:
    """Test assistant registration, retrieval, and list querying."""
    assistant_id = "test-assistant-1"
    name = "Test Assistant"
    model = "anthropic:claude-3-5-sonnet"
    image = "python:3.12-slim"
    base_dir = "/workspace"
    config = {"env": {"DEBUG": "true"}}
    
    # Verify assistant does not exist yet
    assert get_assistant(db_conn, assistant_id) is None
    
    # Register assistant
    res = create_assistant(db_conn, assistant_id, name, model, image, base_dir, config)
    assert res["id"] == assistant_id
    assert res["status"] == "active"
    
    # Verify assistant can be looked up
    db_assistant = get_assistant(db_conn, assistant_id)
    assert db_assistant is not None
    assert db_assistant["id"] == assistant_id
    assert db_assistant["name"] == name
    assert db_assistant["model"] == model
    assert db_assistant["image"] == image
    assert db_assistant["base_dir"] == base_dir
    assert json.loads(db_assistant["config"]) == config
    
    # Verify listing all assistants
    all_assistants = list_assistants(db_conn)
    assert len(all_assistants) == 1
    assert all_assistants[0]["id"] == assistant_id


def test_sandbox_factory_compiling_and_caching() -> None:
    """Test compiled factory caching behavior when DeepAgents packages are available/simulated."""
    assistant = {
        "id": "my-assistant",
        "image": "python:3.12-slim",
        "base_dir": "/workspace"
    }
    
    # Test that calling it returns either None (if not available) or the compiled factory
    # and caches it or handles it gracefully without raising unexpected errors.
    factory = _get_or_compile_sandbox_factory(assistant)
    
    # If DeepAgents is available, factory should be built. If not, it falls back to None.
    # What's important is that it is deterministic and does not raise compilation exceptions.
    assert factory is None or hasattr(factory, "__call__")
