"""DeepAgents runnable graph runtime and user context adapter for the User-Scoped Docker Sandbox API Service.
"""

from __future__ import annotations

import hashlib
import os
import types
from pathlib import Path
from typing import Any

from assistants import _get_or_compile_sandbox_factory

ENABLE_MODEL_ENV = "DEEPAGENTS_SANDBOX_API_ENABLE_MODEL"


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


def _content_to_text(content: Any) -> str:
    """Normalize provider response content into user-facing text.

    Args:
        content: A LangChain message content value.

    Returns:
        Text content, with provider reasoning blocks omitted when possible.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
        if text_parts:
            return "\n".join(text_parts)
    return str(content)


def _load_model_env() -> None:
    """Load local DeepAgents model credentials without overriding the process env."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    load_dotenv(Path.home() / ".deepagents" / ".env", override=False)


def _generate_model_reply(model_spec: str, assistant_name: str, message: str) -> str:
    """Generate a real chat response with the configured model provider.

    Args:
        model_spec: Provider-qualified model spec, for example
            `anthropic:MiniMax-M2.7-highspeed`.
        assistant_name: Human-readable assistant name.
        message: User message.

    Returns:
        Normalized assistant reply text.
    """
    _load_model_env()

    from deepagents_cli.config import create_model
    from langchain_core.messages import HumanMessage, SystemMessage

    model = create_model(model_spec).model
    response = model.invoke(
        [
            SystemMessage(
                content=(
                    f"You are {assistant_name}, a DeepAgents API service assistant. "
                    "Answer concisely and do not mention implementation internals unless asked."
                )
            ),
            HumanMessage(content=message),
        ]
    )
    return _content_to_text(response.content)


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
    model_reply: str | None = None

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
                # Verify the sandbox is live for this user-scoped conversation.
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

    if os.environ.get(ENABLE_MODEL_ENV) == "1" and not message.strip().startswith("run:"):
        try:
            model_reply = _generate_model_reply(
                str(assistant_dict["model"]),
                str(assistant_dict["name"]),
                message,
            )
        except Exception as e:
            print(f"[Error] Model invocation failed: {e}")
            model_reply = f"[Model Warning] Failed to invoke model {assistant_dict['model']}: {e}"

    if model_reply is None:
        model_reply = (
            f"Hi! I am the assistant '{assistant_dict['name']}' (running model {assistant_dict['model']}). "
            f"I received your message: '{message}'."
        )

    assistant_reply = f"{model_reply}{execution_result}"

    return assistant_reply, container_id
