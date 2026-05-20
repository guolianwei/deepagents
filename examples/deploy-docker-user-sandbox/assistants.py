"""Assistant registry and configuration caching for the User-Scoped Docker Sandbox API Service.

Manages assistant DB records and compiles the DeepAgents sandbox factories dynamically.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
from typing import Any

# DeepAgents imports
try:
    from deepagents_cli.deploy.bundler import _render_deploy_graph
    from deepagents_cli.deploy.config import AgentConfig, DeployConfig, SandboxConfig
    DEEPAGENTS_AVAILABLE = True
except ImportError:
    DEEPAGENTS_AVAILABLE = False

# Global cache mapping assistant_id to compiled sandbox factory
sandbox_factories: dict[str, Any] = {}


def _get_or_compile_sandbox_factory(assistant: dict[str, Any]) -> Any:
    """Dynamically compiles the user-scope Docker sandbox factory for an assistant.

    Args:
        assistant: The assistant dictionary from database row containing config details.

    Returns:
        The compiled sandbox factory instance, or None if DeepAgents is not importable.

    Raises:
        ValueError: If `_build_backend_factory` cannot be extracted from compiled source.
    """
    assistant_id = assistant["id"]
    if assistant_id in sandbox_factories:
        return sandbox_factories[assistant_id]

    if not DEEPAGENTS_AVAILABLE:
        return None

    # 1. Prepare deploy config mirroring deepagents.toml
    config = DeployConfig(
        agent=AgentConfig(name=assistant_id),
        sandbox=SandboxConfig(
            provider="docker",
            scope="user",
            image=assistant["image"],
            base_dir=assistant["base_dir"]
        )
    )

    # 2. Render deploy graph source code
    source = _render_deploy_graph(config, mcp_present=False)

    # 3. Compile and execute graph source to register globals
    module_globals: dict[str, Any] = {"__file__": __file__}
    exec(compile(source, f"<deploy_graph_{assistant_id}>", "exec"), module_globals)

    # 4. Extract build factory and generate the sandbox backend factory
    build_factory = module_globals.get("_build_backend_factory")
    if not build_factory:
        raise ValueError("Failed to retrieve _build_backend_factory from compiled deploy graph.")

    factory = build_factory(assistant_id)
    sandbox_factories[assistant_id] = factory
    return factory


# --- DATABASE ASSISTANT OPERATIONS ---

def get_assistant(db: sqlite3.Connection, assistant_id: str) -> sqlite3.Row | None:
    """Retrieve an assistant configuration record by its ID.

    Args:
        db: An active sqlite3 database connection.
        assistant_id: The unique identifier of the assistant.

    Returns:
        A sqlite3.Row containing assistant details, or None if not found.
    """
    return db.execute(
        "SELECT id, name, model, image, base_dir, config, created_at FROM assistants WHERE id = ?",
        (assistant_id,)
    ).fetchone()


def list_assistants(db: sqlite3.Connection) -> list[sqlite3.Row]:
    """Retrieve all registered assistant configuration records.

    Args:
        db: An active sqlite3 database connection.

    Returns:
        A list of sqlite3.Row objects representing all registered assistants.
    """
    return db.execute("SELECT id, name, model, image, base_dir, config, created_at FROM assistants").fetchall()


def create_assistant(
    db: sqlite3.Connection,
    assistant_id: str,
    name: str,
    model: str,
    image: str,
    base_dir: str,
    config: dict[str, Any]
) -> dict[str, Any]:
    """Register a new assistant configuration record inside the database.

    Args:
        db: An active sqlite3 database connection.
        assistant_id: The requested assistant identifier.
        name: The human-readable name of the assistant.
        model: The model name/identifier.
        image: The Docker image string.
        base_dir: The container base workspace path.
        config: Additional configuration dictionary.

    Returns:
        A dictionary containing the registered assistant details.
    """
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    db.execute(
        "INSERT INTO assistants (id, name, model, image, base_dir, config, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (assistant_id, name, model, image, base_dir, json.dumps(config), now)
    )
    db.commit()
    return {"id": assistant_id, "status": "active"}
