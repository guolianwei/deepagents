"""Unit tests for Phase 4 (DeepAgents Graph Runtime Integration).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add parent directory to sys.path to allow importing from a folder containing hyphens
sys.path.insert(0, str(Path(__file__).parent.parent))

from graph_runtime import _ctx, invoke_deepagents_graph


def test_execution_context_creation() -> None:
    """Test context creation format for sandbox factory."""
    identity = "usr_alice_123"
    ctx = _ctx(identity)
    
    assert hasattr(ctx, "server_info")
    assert hasattr(ctx.server_info, "user")
    assert ctx.server_info.user.identity == identity


def test_simulation_fallback_mode() -> None:
    """Test that invoke_deepagents_graph falls back gracefully to simulation mode when Docker is offline."""
    assistant_dict = {
        "id": "helper-assistant",
        "name": "Helper Assistant",
        "model": "anthropic:claude-3-5-sonnet",
        "image": "python:3.12-slim",
        "base_dir": "/workspace"
    }
    
    reply, container_id = invoke_deepagents_graph(
        assistant_id="helper-assistant",
        thread_id="thd_1111",
        user_id="usr_alice",
        message="Hello Assistant!",
        assistant_dict=assistant_dict,
        docker_client=None
    )
    
    assert "sim-" in container_id
    assert "Running in simulated container" in reply
    assert "I received your message: 'Hello Assistant!'" in reply


def test_mocked_docker_sandbox_execution() -> None:
    """Test standard command execution path using a mocked sandbox factory and docker client."""
    assistant_dict = {
        "id": "helper-assistant",
        "name": "Helper Assistant",
        "model": "anthropic:claude-3-5-sonnet",
        "image": "python:3.12-slim",
        "base_dir": "/workspace"
    }
    
    # Mock deepagents sandbox factory
    mock_sandbox = MagicMock()
    mock_sandbox.id = "container_abc_123"
    
    # Mock command execution output
    mock_exec_result = MagicMock()
    mock_exec_result.exit_code = 0
    mock_exec_result.output = "hello from sandbox"
    mock_sandbox.execute.return_value = mock_exec_result
    
    mock_sandbox_holder = MagicMock()
    mock_sandbox_holder.default = mock_sandbox
    
    mock_factory = MagicMock(return_value=mock_sandbox_holder)
    
    # Inject factory directly into the factories cache
    from assistants import sandbox_factories
    sandbox_factories["helper-assistant"] = mock_factory
    
    mock_docker_client = MagicMock()
    
    # 1. Test regular interactive message
    reply, container_id = invoke_deepagents_graph(
        assistant_id="helper-assistant",
        thread_id="thd_1111",
        user_id="usr_alice",
        message="Hello Sandbox!",
        assistant_dict=assistant_dict,
        docker_client=mock_docker_client
    )
    
    assert container_id == "container_abc_123"
    assert "[Sandbox Active]" in reply
    mock_sandbox.execute.assert_called_with("echo 'Interactive conversation initialized.'")
    
    # 2. Test command execution message (run:)
    reply, container_id = invoke_deepagents_graph(
        assistant_id="helper-assistant",
        thread_id="thd_1111",
        user_id="usr_alice",
        message="run: whoami",
        assistant_dict=assistant_dict,
        docker_client=mock_docker_client
    )
    
    assert container_id == "container_abc_123"
    assert "[Sandbox Execution Output (Exit Code 0)]" in reply
    assert "hello from sandbox" in reply
    mock_sandbox.execute.assert_called_with("whoami")
    
    # Clean up factories cache
    sandbox_factories.pop("helper-assistant", None)


def test_model_reply_path_uses_configured_model_when_enabled(monkeypatch) -> None:
    """Test that regular chat can use the real-model code path when enabled."""
    assistant_dict = {
        "id": "helper-assistant",
        "name": "Helper Assistant",
        "model": "anthropic:MiniMax-M2.7-highspeed",
        "image": "python:3.12-slim",
        "base_dir": "/workspace",
    }

    mock_sandbox = MagicMock()
    mock_sandbox.id = "container_abc_123"
    mock_sandbox_holder = MagicMock()
    mock_sandbox_holder.default = mock_sandbox

    from assistants import sandbox_factories

    sandbox_factories["helper-assistant"] = MagicMock(return_value=mock_sandbox_holder)

    import graph_runtime

    monkeypatch.setenv("DEEPAGENTS_SANDBOX_API_ENABLE_MODEL", "1")
    monkeypatch.setattr(
        graph_runtime,
        "_generate_model_reply",
        lambda model_spec, assistant_name, message: (
            f"model={model_spec}; assistant={assistant_name}; message={message}"
        ),
    )

    try:
        reply, container_id = invoke_deepagents_graph(
            assistant_id="helper-assistant",
            thread_id="thd_1111",
            user_id="usr_alice",
            message="Hello through Minimax",
            assistant_dict=assistant_dict,
            docker_client=MagicMock(),
        )
    finally:
        sandbox_factories.pop("helper-assistant", None)

    assert container_id == "container_abc_123"
    assert "model=anthropic:MiniMax-M2.7-highspeed" in reply
    assert "message=Hello through Minimax" in reply
    assert "[Sandbox Active]" in reply
