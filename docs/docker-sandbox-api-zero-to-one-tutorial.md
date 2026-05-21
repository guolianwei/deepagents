# Docker Sandbox API 服务从 0 开始教学

本文是一份按步骤执行的教学文档。目标是从一个干净的本地终端开始，启动 API 服务，注册用户，创建 Assistant，创建 Thread，并完成一次真实的 Agent 对话和 Docker 沙箱隔离验证。

适用场景：

- API 服务运行在本机。
- Docker daemon 运行在容器服务器上。
- 本机通过 `DOCKER_HOST=ssh://deepagents-docker` 访问容器服务器。
- 容器服务器示例地址可以是 `192.168.153.130`。
- 沙箱镜像使用 `python:3.12-slim`。

## 0. 你将完成什么

完成后，你会看到以下结果：

- Alice 可以注册、登录并获得 JWT。
- Alice 可以创建一个 Assistant。
- Alice 可以创建两个 Thread。
- Alice 在第一个 Thread 写入 `/workspace/shared.txt`。
- Alice 在第二个 Thread 仍能读到该文件，说明同一用户复用了同一个沙箱。
- Bob 不能读到 Alice 的文件，说明不同用户使用不同沙箱。
- 远程 Docker 上可以看到带有 `deepagents.sandbox=true` 标签的容器。
- 验证结束后能清理本次创建的容器。

## 1. 打开终端并进入仓库

在 Windows PowerShell 中执行：

```powershell
cd D:\srcs\deepagents
```

确认当前目录：

```powershell
Get-Location
```

期望输出路径包含：

```text
D:\srcs\deepagents
```

## 2. 检查容器服务器

先确认 SSH 别名可用：

```powershell
ssh -o BatchMode=yes deepagents-docker "hostname"
```

再确认 Docker daemon 可用：

```powershell
ssh -o BatchMode=yes deepagents-docker "docker version --format '{{.Server.Version}}'"
```

如果容器服务器示例为 `192.168.153.130`，该 SSH 别名应指向这台容器服务器。

## 3. 准备沙箱镜像

教学中使用 `python:3.12-slim`。先在容器服务器上确认镜像存在：

```powershell
ssh -o BatchMode=yes deepagents-docker "docker image inspect python:3.12-slim"
```

如果提示镜像不存在，拉取镜像：

```powershell
ssh -o BatchMode=yes deepagents-docker "docker pull python:3.12-slim"
```

## 4. 设置 API 服务环境变量

回到本机 PowerShell，设置服务运行环境：

```powershell
$env:PYTHONPATH = "D:\srcs\deepagents\libs\cli;D:\srcs\deepagents\libs\deepagents"
$env:DOCKER_HOST = "ssh://deepagents-docker"
$env:DEEPAGENTS_SANDBOX_API_ENABLE_MODEL = "1"
$env:SANDBOX_API_DB_PATH = "$env:TEMP\deepagents-sandbox-api-tutorial.db"
```

说明：

- `PYTHONPATH` 让示例服务加载本地源码。
- `DOCKER_HOST` 指向容器服务器上的 Docker daemon。
- `DEEPAGENTS_SANDBOX_API_ENABLE_MODEL=1` 表示普通对话走真实模型。
- `SANDBOX_API_DB_PATH` 使用临时 SQLite 数据库，避免污染示例目录。

## 5. 启动 API 服务

```powershell
cd D:\srcs\deepagents\examples\deploy-docker-user-sandbox
uv run uvicorn server:app --host 127.0.0.1 --port 18080
```

保持这个窗口运行。新开一个 PowerShell 窗口继续后续步骤。

在新窗口验证服务：

```powershell
curl.exe -s http://127.0.0.1:18080/openapi.json | Select-String "DeepAgents User-Scoped Sandbox API"
```

如果能看到匹配内容，说明 API 服务已启动。

## 6. 注册 Alice

```powershell
$BaseUrl = "http://127.0.0.1:18080"

$Alice = curl.exe -s -X POST "$BaseUrl/api/v1/auth/register" `
  -H "Content-Type: application/json" `
  -d '{"username":"alice-tutorial","password":"pass-12345"}' | ConvertFrom-Json

$Alice
```

你应该看到类似：

```text
id        : usr_xxxxxxxxxxxx
username  : alice-tutorial
created_at: ...
```

## 7. Alice 登录

```powershell
$AliceLogin = curl.exe -s -X POST "$BaseUrl/api/v1/auth/login" `
  -H "Content-Type: application/json" `
  -d '{"username":"alice-tutorial","password":"pass-12345"}' | ConvertFrom-Json

$AliceToken = $AliceLogin.access_token
$AliceToken.Length
```

如果输出是一个大于 0 的数字，说明 token 已拿到。

## 8. 创建 Assistant

```powershell
$AssistantId = "minimax-coder-tutorial"

$Assistant = curl.exe -s -X POST "$BaseUrl/api/v1/assistants" `
  -H "Authorization: Bearer $AliceToken" `
  -H "Content-Type: application/json" `
  -d "{
    `"id`": `"$AssistantId`",
    `"name`": `"Minimax Coder Tutorial`",
    `"model`": `"anthropic:MiniMax-M2.7-highspeed`",
    `"image`": `"python:3.12-slim`",
    `"base_dir`": `"/workspace`"
  }" | ConvertFrom-Json

$Assistant
```

期望：

```text
id    : minimax-coder-tutorial
status: active
```

## 9. 创建 Alice 的第一个 Thread

```powershell
$AliceThread1 = curl.exe -s -X POST "$BaseUrl/api/v1/threads" `
  -H "Authorization: Bearer $AliceToken" `
  -H "Content-Type: application/json" `
  -d "{
    `"assistant_id`": `"$AssistantId`",
    `"name`": `"alice-thread-1`"
  }" | ConvertFrom-Json

$AliceThreadId1 = $AliceThread1.thread_id
$AliceThread1
```

## 10. 发送普通 Agent 对话

```powershell
$Chat1 = curl.exe -s -X POST "$BaseUrl/api/v1/threads/$AliceThreadId1/chat" `
  -H "Authorization: Bearer $AliceToken" `
  -H "Content-Type: application/json" `
  -d '{"message":"Reply with exactly: api-agent-ok"}' | ConvertFrom-Json

$Chat1.response
$Chat1.container_id
```

期望响应包含：

```text
api-agent-ok
[Sandbox Active]
```

这里验证了两件事：

- 普通对话可以进入模型调用路径。
- 服务已经创建或复用 Alice 的 Docker 沙箱。

## 11. 在 Alice 沙箱中执行命令

以 `run:` 开头的消息会进入 Docker 容器执行。

```powershell
$AliceRun1 = curl.exe -s -X POST "$BaseUrl/api/v1/threads/$AliceThreadId1/chat" `
  -H "Authorization: Bearer $AliceToken" `
  -H "Content-Type: application/json" `
  -d '{"message":"run: python --version && echo alice-secret-data > /workspace/shared.txt && cat /workspace/shared.txt"}' | ConvertFrom-Json

$AliceRun1.response
$AliceContainerId = $AliceRun1.container_id
$AliceContainerId
```

期望响应包含：

```text
Python 3.12
alice-secret-data
```

保存下来的 `$AliceContainerId` 会用于后续复用验证。

## 12. 创建 Alice 的第二个 Thread

```powershell
$AliceThread2 = curl.exe -s -X POST "$BaseUrl/api/v1/threads" `
  -H "Authorization: Bearer $AliceToken" `
  -H "Content-Type: application/json" `
  -d "{
    `"assistant_id`": `"$AssistantId`",
    `"name`": `"alice-thread-2`"
  }" | ConvertFrom-Json

$AliceThreadId2 = $AliceThread2.thread_id
```

## 13. 验证同一用户跨 Thread 复用沙箱

```powershell
$AliceRun2 = curl.exe -s -X POST "$BaseUrl/api/v1/threads/$AliceThreadId2/chat" `
  -H "Authorization: Bearer $AliceToken" `
  -H "Content-Type: application/json" `
  -d '{"message":"run: cat /workspace/shared.txt"}' | ConvertFrom-Json

$AliceRun2.response
$AliceRun2.container_id
$AliceRun2.container_id -eq $AliceContainerId
```

期望：

```text
alice-secret-data
True
```

这说明 Alice 的两个 Thread 使用了同一个容器。

## 14. 注册 Bob

```powershell
$Bob = curl.exe -s -X POST "$BaseUrl/api/v1/auth/register" `
  -H "Content-Type: application/json" `
  -d '{"username":"bob-tutorial","password":"pass-12345"}' | ConvertFrom-Json

$BobLogin = curl.exe -s -X POST "$BaseUrl/api/v1/auth/login" `
  -H "Content-Type: application/json" `
  -d '{"username":"bob-tutorial","password":"pass-12345"}' | ConvertFrom-Json

$BobToken = $BobLogin.access_token
```

## 15. Bob 创建 Thread

```powershell
$BobThread1 = curl.exe -s -X POST "$BaseUrl/api/v1/threads" `
  -H "Authorization: Bearer $BobToken" `
  -H "Content-Type: application/json" `
  -d "{
    `"assistant_id`": `"$AssistantId`",
    `"name`": `"bob-thread-1`"
  }" | ConvertFrom-Json

$BobThreadId1 = $BobThread1.thread_id
```

## 16. 验证 Bob 不能读取 Alice 文件

```powershell
$BobRun1 = curl.exe -s -X POST "$BaseUrl/api/v1/threads/$BobThreadId1/chat" `
  -H "Authorization: Bearer $BobToken" `
  -H "Content-Type: application/json" `
  -d '{"message":"run: cat /workspace/shared.txt"}' | ConvertFrom-Json

$BobRun1.response
$BobContainerId = $BobRun1.container_id
$BobContainerId
$BobContainerId -ne $AliceContainerId
```

期望：

```text
cat: /workspace/shared.txt: No such file or directory
True
```

这说明 Bob 拿到的是另一个 Docker 容器。

## 17. 查看 API 记录的沙箱

```powershell
$Sandboxes = curl.exe -s -X GET "$BaseUrl/api/v1/sandboxes" `
  -H "Authorization: Bearer $AliceToken" | ConvertFrom-Json

$Sandboxes
```

你应该能看到至少两条记录，分别对应 Alice 和 Bob：

```text
user:minimax-coder-tutorial:<alice_user_id>
user:minimax-coder-tutorial:<bob_user_id>
```

## 18. 在容器服务器上查看真实容器

```powershell
ssh deepagents-docker "docker ps -a --filter label=deepagents.sandbox=true --format '{{.ID}} {{.Names}} {{.Status}} {{.Labels}}'"
```

容器标签应包含：

```text
deepagents.sandbox=true
deepagents.cache_key=user:minimax-coder-tutorial:<user_id>
```

## 19. 清理本次教学容器

只清理本次教学产生的容器：

```powershell
ssh deepagents-docker "docker rm -f $AliceContainerId $BobContainerId"
```

确认已清理：

```powershell
ssh deepagents-docker "docker ps -a --filter label=deepagents.sandbox=true"
```

如果没有其他测试容器，应只看到表头。

## 20. 停止 API 服务

回到运行 `uvicorn` 的 PowerShell 窗口，按：

```text
Ctrl+C
```

API 服务停止后，本次从 0 开始教学完成。

## 21. 常见问题

### 注册用户提示已存在

换一个用户名，例如：

```text
alice-tutorial-001
bob-tutorial-001
```

或换一个新的临时数据库路径：

```powershell
$env:SANDBOX_API_DB_PATH = "$env:TEMP\deepagents-sandbox-api-tutorial-001.db"
```

### 普通对话没有返回 `api-agent-ok`

先确认模型路径是否开启：

```powershell
$env:DEEPAGENTS_SANDBOX_API_ENABLE_MODEL
```

应为：

```text
1
```

再确认本机模型配置存在：

```text
C:\Users\29267\.deepagents\config.toml
C:\Users\29267\.deepagents\.env
```

### 响应中出现模拟沙箱

如果看到：

```text
[Sandbox Simulation]
```

说明 API 服务没有连上 Docker daemon。检查：

```powershell
$env:DOCKER_HOST
ssh -o BatchMode=yes deepagents-docker "docker version"
```

修复后重启 API 服务。

### Alice 和 Bob 容器 ID 相同

这是隔离错误。检查：

- Bob 是否使用了自己的 `$BobToken`。
- Bob 是否创建了自己的 Thread。
- `container_id` 是否取自同一个请求对象。
- `deepagents.cache_key` 是否是 `user:<assistant_id>:<user_id>`。
