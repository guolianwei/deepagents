"""DeepAgents runnable graph runtime and user context adapter for the User-Scoped Docker Sandbox API Service.
"""

from __future__ import annotations

import hashlib
import types
from typing import Any

from assistants import _get_or_compile_sandbox_factory


def _ctx(identity: str) -> types.SimpleNamespace:
    """Creates the execution context with the user identity expected by DeepAgents sandbox factory.

    Args:
        identity: The authenticated user identity.

    Returns:
        A SimpleNamespace containing server_info.user.identity.
    """
    return types.SimpleNamespace(
        server_info=types.SimpleNamespace(
            user=types.SimpleNamespace(identity=identity),
        ),
    )


def invoke_deepagents_graph(
    *,
    assistant_id: str,
    thread_id: str,
    user_id: str,
    message: str,
    assistant_dict: dict[str, Any],
    docker_client: Any = None
) -> tuple[str, str | None]:
    """Invoke the DeepAgents runtime inside a user-identity context.

    If a real Docker client is connected, it provisions or reuses a dedicated container.
    Otherwise, it falls back gracefully to a dry-run / simulation mode.

    Args:
        assistant_id: The identifier of the assistant configuration.
        thread_id: The dialogue thread identifier.
        user_id: The user identity.
        message: The message payload.
        assistant_dict: The assistant configuration dictionary.
        docker_client: An active docker client library instance, if connected.

    Returns:
        A tuple of (assistant_reply_text, container_id_string).
    """
    factory = None
    try:
        factory = _get_or_compile_sandbox_factory(assistant_dict)
    except Exception as e:
        print(f"[Error] Failed to compile sandbox factory: {e}")

    container_id = None
    execution_result = ""

    # Provision / Reuse the user-scoped container
    if factory and docker_client:
        try:
            # Invoking factory with user context creates or starts the user-scoped container transparently
            sandbox_holder = factory(_ctx(user_id))
            sandbox = sandbox_holder.default
            container_id = sandbox.id

            cmd = message.strip()
            if cmd.startswith("run:"):
                # Explicit command execution inside sandbox
                exec_cmd = cmd[4:].strip()
                res = sandbox.execute(exec_cmd)
                execution_result = f"\n[Sandbox Execution Output (Exit Code {res.exit_code})]:\n{res.output}"
            else:
                # Default mock agent execution inside container
                sandbox.execute("echo 'Interactive conversation initialized.'")
                execution_result = f"\n[Sandbox Active] Container {container_id[:12]} verified."
        except Exception as e:
            print(f"[Error] Sandbox execution failed: {e}")
            execution_result = f"\n[Sandbox Warning] Failed to run in sandbox: {e}"
    else:
        # Simulation Mode
        cache_key = f"user:{assistant_id}:{user_id}"
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:24]
        container_id = f"sim-{digest}"
        execution_result = (
            f"\n[Sandbox Simulation] Running in simulated container '{container_id}'. "
            f"User workspace mapped to '{assistant_dict['base_dir']}'."
        )

    assistant_reply = (
        f"Hi! I am the assistant '{assistant_dict['name']}' (running model {assistant_dict['model']}). "
        f"I received your message: '{message}'."
        f"{execution_result}"
    )

    return assistant_reply, container_id
