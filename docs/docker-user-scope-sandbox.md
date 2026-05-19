# Docker User-Scoped Sandbox

This guide documents the `provider = "docker"` + `scope = "user"` deploy
configuration for running one Docker sandbox per authenticated user.

## Status

This feature is implemented in the current development branch. It depends on:

- deploy config support for `provider = "docker"`
- deploy config support for `scope = "user"`
- `DockerSandbox` in `deepagents.backends.docker`
- Docker SDK for Python with SSH transport: `docker[ssh]>=7.0.0,<8.0.0`

## When To Use It

Use `scope = "user"` when one deployed assistant serves multiple authenticated
users and each user should keep a persistent sandbox across multiple threads.

| Scope | Sandbox Reuse | Use When |
| --- | --- | --- |
| `thread` | One sandbox per thread | Threads should be isolated from each other |
| `assistant` | One sandbox per assistant | A single trusted tenant should share one workspace |
| `user` | One sandbox per assistant and user identity | Each authenticated user needs a private reusable workspace |

Do not use `scope = "assistant"` as a user isolation boundary in multi-user
deployments. All users share one sandbox with that scope.

## Minimal Config

```toml
[agent]
name = "docker-user-sandbox"
model = "anthropic:claude-sonnet-4-6"

[sandbox]
provider = "docker"
scope = "user"
image = "python:3.12-slim"
base_dir = "/workspace"

[auth]
provider = "supabase"
```

`scope = "user"` requires auth. At runtime, the generated graph reads the
authenticated user identity from custom auth. If no user identity is present,
the sandbox factory raises:

```text
user identity is required when sandbox scope is 'user'
```

## Runtime Mapping

The sandbox cache key is:

```text
user:{assistant_id}:{user_id}
```

Docker containers are named with a hash of that key:

```text
deepagents-sandbox-<sha256(cache_key)[:24]>
```

The original `user_id` is stored in the cache key label but is not used
directly as the container name.

## Docker Daemon Access

The Python process running the deploy graph must be able to reach a Docker
daemon. For local development and self-hosted runners, prefer Docker SSH
transport:

```powershell
$env:DOCKER_HOST = "ssh://deepagents-docker"
```

Current verified local setup for this repository is recorded in:

```text
.codex/docker-ssh.md
```

Validation command:

```powershell
ssh -o BatchMode=yes deepagents-docker "hostname && docker version --format '{{.Server.Version}}'"
```

Expected output for the current development host:

```text
aibot
28.4.0
```

Do not expose unauthenticated Docker TCP on `0.0.0.0:2375`. If TCP access is
unavoidable in production, use TLS on `2376` and restrict source IPs with a
firewall.

## Default Container Hardening

The deploy Docker provider creates containers with conservative defaults:

- no host directory mounts
- no Docker socket mount
- `network_disabled=True`
- `cap_drop=["ALL"]`
- `security_opt=["no-new-privileges"]`
- `mem_limit="1g"`
- `pids_limit=256`
- command: `sleep infinity`
- working directory: `base_dir`

The first implementation keeps these values fixed. Resource customization,
network opt-in, cleanup policies, idle timeout, and per-user volumes belong to
the lifecycle and hardening follow-up stage.

## Current Backend Limits

`DockerSandbox.execute()` accepts the standard `timeout` parameter for protocol
compatibility, but the current sync Docker SDK implementation does not enforce
that timeout. Commands block until completion.

Use `/bin/sh -lc` compatible commands. The default example image is
`python:3.12-slim`, so do not assume `bash` is installed.

## Migration

From no sandbox:

```toml
[sandbox]
provider = "none"
```

Move first to thread-scoped Docker:

```toml
[sandbox]
provider = "docker"
scope = "thread"
image = "python:3.12-slim"
base_dir = "/workspace"
```

After Docker daemon access is verified, enable user-scoped reuse:

```toml
[sandbox]
provider = "docker"
scope = "user"
image = "python:3.12-slim"
base_dir = "/workspace"

[auth]
provider = "supabase"
```

From assistant-scoped sandbox:

```toml
[sandbox]
provider = "langsmith"
scope = "assistant"
```

Use `scope = "user"` for multi-user isolation:

```toml
[sandbox]
provider = "docker"
scope = "user"
image = "python:3.12-slim"
base_dir = "/workspace"
```

Then verify:

- the same authenticated user can read files written from another thread
- a different authenticated user gets a different container
- missing auth fails before creating a user-scoped sandbox

## Operational Checks

List Deep Agents sandbox containers:

```bash
docker ps -a --filter label=deepagents.sandbox=true
```

When using SSH transport from this Windows development machine:

```powershell
ssh deepagents-docker 'docker ps -a --filter label=deepagents.sandbox=true'
```

Remove only Deep Agents sandbox containers:

```powershell
ssh deepagents-docker 'docker rm -f $(docker ps -aq --filter label=deepagents.sandbox=true)'
```

## Example

See:

```text
examples/deploy-docker-user-sandbox/
```

The example combines:

- Supabase auth
- `scope = "user"`
- Docker SSH transport
- a Python 3.12 container workspace at `/workspace`
