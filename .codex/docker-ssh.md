# Docker SSH Sandbox Host

Use SSH instead of opening Docker TCP ports `2375` or `2376`.

Verified host:

- SSH alias: `deepagents-docker`
- Host: `192.168.153.130`
- User: `glw`
- Remote hostname: `aibot`
- Remote Docker server version: `28.4.0`
- Remote user Docker access: `glw` is in the `docker` group

Local SSH files:

- Private key: `C:\Users\29267\.ssh\id_rsa_deepagents_docker`
- SSH config: `C:\Users\29267\.ssh\config`
- Host alias config:

```sshconfig
Host deepagents-docker
    HostName 192.168.153.130
    User glw
    IdentityFile ~/.ssh/id_rsa_deepagents_docker
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
```

Validation commands:

```powershell
ssh -o BatchMode=yes deepagents-docker "hostname && docker version --format '{{.Server.Version}}'"
```

Expected output:

```text
aibot
28.4.0
```

Docker provider connection setting:

```powershell
$env:DOCKER_HOST = "ssh://deepagents-docker"
```

Notes:

- Do not expose unauthenticated Docker TCP on `0.0.0.0:2375`.
- Current local machine does not have Docker CLI installed.
- Current base Python did not have the `docker` Python package installed when checked.
- Deep Agents Docker provider development should rely on Docker SDK for Python with `DOCKER_HOST=ssh://deepagents-docker`.
