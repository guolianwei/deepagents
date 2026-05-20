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

## Running the FastAPI API Service

We have provided a fully-featured, production-ready REST API service in `server.py` using FastAPI. It handles user registration, secure JWT log-ins, assistant registrations, and dynamically launches or reuses a dedicated, persistent Docker container sandbox per user based on their JWT token credentials.

To run the API service locally:

```bash
# Install dependencies and start the FastAPI web server
uv run uvicorn server:app --port 8000 --reload
```

## How To Interact With The API

### 1. Register a New User

```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "alice", "password": "password123"}'
```

Response:
```json
{
  "id": "usr_9f4a7c12bc34",
  "username": "alice",
  "created_at": "2026-05-20T14:30:00Z"
}
```

### 2. Log In and Obtain JWT Access Token

```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "alice", "password": "password123"}'
```

Response:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsIn...",
  "token_type": "bearer"
}
```

Save your token:
```bash
export JWT_TOKEN="eyJhbGciOiJIUzI1NiIsIn..."
```

### 3. Register/Start an Assistant

```bash
curl -X POST http://127.0.0.1:8000/api/v1/assistants \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "coding-assistant",
    "name": "Coding Assistant",
    "model": "anthropic:claude-sonnet-4-6",
    "image": "python:3.12-slim",
    "base_dir": "/workspace"
  }'
```

### 4. Create a Conversational Thread

```bash
curl -X POST http://127.0.0.1:8000/api/v1/threads \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"assistant_id": "coding-assistant", "name": "Isolated Workspace Thread"}'
```

Response:
```json
{
  "thread_id": "thd_7b3a9c42de11",
  "user_id": "usr_9f4a7c12bc34",
  "assistant_id": "coding-assistant",
  "name": "Isolated Workspace Thread"
}
```

### 5. Chat & Execute Commands in Your Persistent Sandbox

Send a standard message to the assistant:
```bash
curl -X POST http://127.0.0.1:8000/api/v1/threads/thd_7b3a9c42de11/chat \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello! Introduce yourself."}'
```

Execute a terminal command inside your private Docker container workspace by prefixing your message with `run:`:
```bash
curl -X POST http://127.0.0.1:8000/api/v1/threads/thd_7b3a9c42de11/chat \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "run: pwd && echo \"hello from user alice\" > /workspace/shared.txt && cat /workspace/shared.txt"}'
```

Because your sandbox is user-scoped:
- **Same User, Different Thread**: If you create a new thread with `thread_id` `thd_different_999` and run `cat /workspace/shared.txt`, it **will** return `"hello from user alice"`.
- **Different User**: If another registered user logs in and attempts to access `/workspace/shared.txt`, their request will run inside a completely separate Docker container, resulting in a "No such file or directory" error.

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
├── deepagents.toml    # Deploy config with Docker user-scope sandbox
└── server.py          # FastAPI API service routing threads to user Docker containers
```

## Resources

- [Docker user-scoped sandbox design](../../docs/user-scope-docker-sandbox-design.md)
- [Docker user-scoped sandbox api service design](../../docs/user-scoped-docker-sandbox-api-service-design.md)
- [Docker user-scoped sandbox guide](../../docs/docker-user-scope-sandbox.md)
- [Anthropic models overview](https://platform.claude.com/docs/en/about-claude/models/overview)
