# Phase Validation Handoff

Date: 2026-05-21

## Scope

Validate the `examples/deploy-docker-user-sandbox` API service design with:

- remote Docker on `192.168.153.130`
- user-scoped Docker sandbox reuse
- API service registration/login/thread/chat flow
- Minimax model conversation via existing project configuration

## Current Status

The API service design is mostly complete. Basic API tests, Docker connectivity,
Docker user-scope integration, direct Minimax model sanity checks, and the full
live HTTP API flow through FastAPI have passed.

## Verified Results

### API Service Tests

Command:

```powershell
uv run pytest examples/deploy-docker-user-sandbox/tests -q
```

Result:

```text
18 passed
```

### Remote Docker Connectivity

Command:

```powershell
ssh -o BatchMode=yes deepagents-docker "hostname && docker version --format '{{.Server.Version}}'"
```

Result:

```text
aibot
28.4.0
```

The SSH alias `deepagents-docker` maps to the remote Docker host
`192.168.153.130`.

### DeepAgents Docker User-Scope Integration

Working directory:

```text
D:\srcs\deepagents\libs\cli
```

Command:

```powershell
$env:DOCKER_HOST='ssh://deepagents-docker'
uv run --group test pytest tests/integration_tests/test_deploy_docker_user_scope.py -q
```

Result:

```text
1 passed
```

This confirms the deploy template can create and reuse per-user Docker
containers through the remote Docker daemon.

### Minimax Model Sanity Check

Configuration sources:

```text
C:\Users\29267\.deepagents\.env
C:\Users\29267\.deepagents\config.toml
```

Model:

```text
anthropic:MiniMax-M2.7-highspeed
```

Direct model invocation returned the expected English sanity response:

```text
model-ok
```

### Live HTTP API End-to-End Validation

Date: 2026-05-21

Service environment:

```powershell
$env:PYTHONPATH = "D:\srcs\deepagents\libs\cli;D:\srcs\deepagents\libs\deepagents"
$env:DOCKER_HOST = "ssh://deepagents-docker"
$env:DEEPAGENTS_SANDBOX_API_ENABLE_MODEL = "1"
$env:SANDBOX_API_DB_PATH = "$env:TEMP\deepagents-sandbox-api-live-20260521-065252.db"
```

The first live attempt used `python:3.12-slim`, as originally planned, but the
remote Docker daemon could not pull from Docker Hub because its configured proxy
was unavailable:

```text
proxyconnect tcp: dial tcp 192.168.153.1:18288: connect: connection refused
```

The successful live validation used an already-present remote image:

```text
ghcr.io/astral-sh/uv:python3.12-bookworm-slim
```

Validation identifiers:

```text
assistant_id=minimax-coder-existingimg-1779317671
alice_user_id=usr_b53e5e34e4d3
bob_user_id=usr_0b55f5d42e83
alice_container=959ec9af5a675bb52cc6f11b854180c21169c2a4c73553b152fc263e994b5624
bob_container=3bc748cdec045c1b832e363a5effd8107303b473ca8327247f4260c65d8b36b8
```

Evidence:

```text
NORMAL_CHAT_RESPONSE api-agent-ok | [Sandbox Active] Container 959ec9af5a67 verified.
ALICE_WRITE_RESPONSE ... [Sandbox Execution Output (Exit Code 0)]: | alice-secret-data |
ALICE_REUSE_RESPONSE ... [Sandbox Execution Output (Exit Code 0)]: | alice-secret-data |
BOB_READ_RESPONSE ... [Sandbox Execution Output (Exit Code 1)]: | cat: /workspace/shared.txt: No such file or directory |
```

Remote Docker confirmed both validation containers with `deepagents.sandbox=true`
and user-specific `deepagents.cache_key` labels. The validation script then
removed only those two container IDs:

```text
959ec9af5a675bb52cc6f11b854180c21169c2a4c73553b152fc263e994b5624
3bc748cdec045c1b832e363a5effd8107303b473ca8327247f4260c65d8b36b8
```

Final result:

```text
VALIDATION_RESULT=PASS
```

### Remote Docker Host Proxy Fix

Date: 2026-05-21

The remote Docker host was still configured for the old local proxy port:

```text
192.168.153.1:18288
```

The local Windows machine currently exposes the SakuraCat/Meta TUN proxy on:

```text
192.168.153.1:7897
```

Connectivity from `deepagents-docker` to the new port was verified:

```text
CONNECT 192.168.153.1:7897 OK
curl -x http://192.168.153.1:7897 https://registry-1.docker.io/v2/ -> HTTP/2 401
```

Changed on host `192.168.153.130`:

```text
/etc/systemd/system/docker.service.d/proxy.conf
/etc/environment
/home/glw/.config/systemd/user/openclaw-gateway.service
```

Backups created:

```text
/etc/systemd/system/docker.service.d/proxy.conf.bak-20260521-002949
/etc/environment.bak-20260521-003442
/home/glw/.config/systemd/user/openclaw-gateway.service.bak-20260521-003442
```

Docker daemon now reports:

```text
HTTP_PROXY=http://192.168.153.1:7897
HTTPS_PROXY=http://192.168.153.1:7897
```

New SSH sessions now report:

```text
HTTP_PROXY=http://192.168.153.1:7897/
HTTPS_PROXY=http://192.168.153.1:7897/
```

Validation after the proxy fix:

```text
docker pull python:3.12-slim -> Image is up to date
PROXY_FIX_API_VALIDATION=PASS
```

The API validation used `python:3.12-slim` and executed:

```text
python --version
echo proxy-fixed > /workspace/proxy.txt
cat /workspace/proxy.txt
```

Observed response:

```text
Python 3.12.13
proxy-fixed
```

The validation container was removed after the check, and
`docker ps -a --filter label=deepagents.sandbox=true` returned no sandbox
containers.

Operational note: the remote host depends on the Windows-side proxy listener
remaining reachable at `192.168.153.1:7897`. If the local TUN/proxy app changes
ports or disables LAN listening, Docker pulls from the remote host will fail
again.

## Code Changes Made

### `graph_runtime.py`

File:

```text
examples/deploy-docker-user-sandbox/graph_runtime.py
```

Changes:

- Added `DEEPAGENTS_SANDBOX_API_ENABLE_MODEL=1` feature switch.
- Default behavior remains deterministic and test-friendly.
- When enabled, the runtime loads `~/.deepagents/.env` and calls
  `deepagents_cli.config.create_model()`.
- Added `_content_to_text()` to normalize provider response content and avoid
  surfacing Minimax/Anthropic thinking blocks as the user reply.
- Regular chat can now invoke the configured Minimax model.
- `run:` messages still execute shell commands in the user sandbox.

### `server.py`

File:

```text
examples/deploy-docker-user-sandbox/server.py
```

Changes:

- Added `SANDBOX_API_DB_PATH` support so live validation can use a temporary
  SQLite database.
- Changed `get_db()` to a generator dependency that closes the SQLite connection
  after each request.

### `test_graph_runtime.py`

File:

```text
examples/deploy-docker-user-sandbox/tests/test_graph_runtime.py
```

Changes:

- Added a unit test for the model-enabled reply path.
- The test monkeypatches `_generate_model_reply()` so CI does not need network
  or Minimax credentials.

## Current Working Tree Notes

Modified files:

```text
examples/deploy-docker-user-sandbox/graph_runtime.py
examples/deploy-docker-user-sandbox/server.py
examples/deploy-docker-user-sandbox/tests/test_graph_runtime.py
examples/deploy-docker-user-sandbox/phase-validation-handoff.md
```

Untracked local runtime artifact:

```text
examples/deploy-docker-user-sandbox/sandbox_api.db
```

Do not commit `sandbox_api.db` unless the project explicitly wants a seed
database. It is a local runtime artifact.

## Interrupted Live Validation

A temporary FastAPI service was started with:

```text
http://127.0.0.1:18080/openapi.json
```

The server reached ready state, but the live HTTP flow was interrupted before
registration/chat requests were executed.

The temporary uvicorn process was stopped after interruption. If the browser is
still open on `http://127.0.0.1:18080/openapi.json`, it may be showing stale
state or a disconnected target.

## Next Agent Instructions

Continue from the live HTTP end-to-end validation.

Use a temporary database so the default `sandbox_api.db` is not polluted:

```powershell
$env:PYTHONPATH = "D:\srcs\deepagents\libs\cli;D:\srcs\deepagents\libs\deepagents"
$env:DOCKER_HOST = "ssh://deepagents-docker"
$env:DEEPAGENTS_SANDBOX_API_ENABLE_MODEL = "1"
$env:SANDBOX_API_DB_PATH = "$env:TEMP\deepagents-sandbox-api-live.db"
```

Start the API service:

```powershell
cd D:\srcs\deepagents\examples\deploy-docker-user-sandbox
uv run uvicorn server:app --host 127.0.0.1 --port 18080
```

Validate:

1. Register user A and user B.
2. Login both users and capture bearer tokens.
3. Create assistant with:

   ```json
   {
     "id": "minimax-coder",
     "name": "Minimax Coder",
     "model": "anthropic:MiniMax-M2.7-highspeed",
     "image": "python:3.12-slim",
     "base_dir": "/workspace"
   }
   ```

4. User A creates thread A1 and sends a normal chat message. Confirm the reply
   comes from the Minimax model path and includes sandbox active status.
5. User A sends:

   ```text
   run: echo 'alice-secret-data' > /workspace/shared.txt && cat /workspace/shared.txt
   ```

6. User A creates thread A2 and sends:

   ```text
   run: cat /workspace/shared.txt
   ```

   Expected: reads `alice-secret-data` from the same user container.

7. User B creates thread B1 and sends:

   ```text
   run: cat /workspace/shared.txt
   ```

   Expected: different container, file not found.

8. Verify remote containers:

   ```powershell
   ssh deepagents-docker "docker ps -a --filter label=deepagents.sandbox=true --format '{{.ID}} {{.Names}} {{.Status}} {{.Labels}}'"
   ```

9. Clean up only validation containers after collecting evidence.

## Acceptance Criteria

- API registration/login works for two users.
- Minimax chat path is exercised through the FastAPI service.
- User A thread A1 and A2 share the same Docker container.
- User B gets a different Docker container.
- User B cannot read User A's `/workspace/shared.txt`.
- Containers are confirmed on host `192.168.153.130` through `deepagents-docker`.
- Test containers are cleaned up after validation.
