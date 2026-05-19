"""Integration tests for deploy Docker user-scope sandbox reuse."""

from __future__ import annotations

import types
import uuid

import pytest

from deepagents_cli.deploy.bundler import _render_deploy_graph
from deepagents_cli.deploy.config import AgentConfig, DeployConfig, SandboxConfig


def _ctx(identity: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        server_info=types.SimpleNamespace(
            user=types.SimpleNamespace(identity=identity),
        ),
    )


def _docker_client_or_skip() -> object:
    docker = pytest.importorskip("docker")
    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Docker daemon is not available: {exc}")
    return client


@pytest.mark.timeout(120)
def test_docker_provider_user_scope_reuses_per_user_container() -> None:
    client = _docker_client_or_skip()
    assistant_id = f"it-{uuid.uuid4().hex[:12]}"
    config = DeployConfig(
        agent=AgentConfig(name=assistant_id),
        sandbox=SandboxConfig(
            provider="docker",
            scope="user",
            image="alpine:latest",
            base_dir="/tmp",
        ),
    )
    source = _render_deploy_graph(config, mcp_present=False)
    module_globals: dict[str, object] = {"__file__": __file__}
    exec(compile(source, "<deploy_graph_docker_user_scope>", "exec"), module_globals)

    factory = module_globals["_build_backend_factory"](assistant_id)
    cache_keys = [
        f"user:{assistant_id}:user-a",
        f"user:{assistant_id}:user-b",
    ]

    try:
        user_a_thread_1 = factory(_ctx("user-a")).default
        write = user_a_thread_1.execute("echo a > /tmp/user.txt")
        assert write.exit_code == 0, write.output

        user_a_thread_2 = factory(_ctx("user-a")).default
        assert user_a_thread_2.id == user_a_thread_1.id
        read = user_a_thread_2.execute("cat /tmp/user.txt")
        assert read.output.strip() == "a"

        user_b_thread_1 = factory(_ctx("user-b")).default
        assert user_b_thread_1.id != user_a_thread_1.id
        missing = user_b_thread_1.execute("cat /tmp/user.txt")
        assert missing.exit_code != 0

        user_a_thread_3 = factory(_ctx("user-a")).default
        reread = user_a_thread_3.execute("cat /tmp/user.txt")
        assert reread.output.strip() == "a"
    finally:
        for cache_key in cache_keys:
            for container in client.containers.list(
                all=True,
                filters={
                    "label": [
                        "deepagents.sandbox=true",
                        f"deepagents.cache_key={cache_key}",
                    ],
                },
            ):
                container.remove(force=True)
