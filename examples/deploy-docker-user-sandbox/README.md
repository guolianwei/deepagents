# deploy-docker-user-sandbox

A `deepagents deploy` example that gives each authenticated user one reusable
Docker sandbox. Multiple threads from the same user share `/workspace`; different
users get different Docker containers.

This example demonstrates:

- `provider = "docker"`
- `scope = "user"`
- Supabase auth as the user identity source
- Docker SSH transport for a self-hosted Docker daemon

## Prerequisites

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Claude model access |
| `LANGSMITH_API_KEY` | Required for deploy |
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_PUBLISHABLE_DEFAULT_KEY` | Your Supabase publishable key |
| `DOCKER_HOST` | Docker daemon endpoint, for example `ssh://deepagents-docker` |

The configured model is `anthropic:claude-sonnet-4-6`. Update it if your
deployment uses another provider.

## Docker SSH Setup

For the current Windows development machine, the verified host alias is recorded
in `../../.codex/docker-ssh.md`.

Set Docker access before running deploy or integration tests:

```powershell
$env:DOCKER_HOST = "ssh://deepagents-docker"
```

Verify the remote Docker daemon:

```powershell
ssh -o BatchMode=yes deepagents-docker "hostname && docker version --format '{{.Server.Version}}'"
```

Expected output on the current development host:

```text
aibot
28.4.0
```

Do not expose unauthenticated Docker TCP on `0.0.0.0:2375`.

## Deploy

```bash
deepagents deploy
```

The `[auth]` section generates the Supabase auth handler. The `[sandbox]`
section creates Docker containers keyed by `assistant_id + user.identity`.

## What To Try

Run two threads as the same authenticated user:

```text
Write "hello from user A" to /workspace/user.txt
```

Then in another thread for the same user:

```text
Read /workspace/user.txt
```

The file should still be present.

Run a thread as a different authenticated user:

```text
Read /workspace/user.txt
```

The file should not exist because that user gets a different container.

## Query Via SDK

Pass a Supabase JWT in the `Authorization` header. The deployment validates the
token and uses the authenticated user identity for the sandbox cache key.

```python
from langgraph_sdk import get_client

client = get_client(
    url="https://<your-deployment-url>",
    headers={"Authorization": "Bearer <your-supabase-jwt>"},
)
thread = await client.threads.create()

async for chunk in client.runs.stream(
    thread["thread_id"],
    "agent",
    input={
        "messages": [
            {
                "role": "user",
                "content": "Create /workspace/user.txt with my user id in it.",
            }
        ]
    },
    stream_mode="messages",
):
    print(chunk.data, end="", flush=True)
```

## Inspect And Clean Up Containers

List Deep Agents sandbox containers:

```powershell
ssh deepagents-docker 'docker ps -a --filter label=deepagents.sandbox=true'
```

Remove only Deep Agents sandbox containers:

```powershell
ssh deepagents-docker 'docker rm -f $(docker ps -aq --filter label=deepagents.sandbox=true)'
```

## Structure

```text
deploy-docker-user-sandbox/
├── AGENTS.md          # Agent instructions for the Docker workspace
├── README.md          # Setup, deploy, and validation guide
└── deepagents.toml    # Deploy config with Docker user-scope sandbox
```

## Resources

- [Docker user-scoped sandbox design](../../docs/user-scope-docker-sandbox-design.md)
- [Docker user-scoped sandbox guide](../../docs/docker-user-scope-sandbox.md)
- [Anthropic models overview](https://platform.claude.com/docs/en/about-claude/models/overview)
