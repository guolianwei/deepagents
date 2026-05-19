# User Scope and Docker Sandbox Provider Design

## 背景

当前 deploy 模板不能直接配置“每个用户一个沙箱”。现有 sandbox 逻辑分为两层：

- sandbox provider：决定使用哪一种后端沙箱。
- sandbox scope：决定沙箱按什么粒度复用。

当前 deploy provider 定义在 `libs/cli/deepagents_cli/deploy/config.py`：

```python
SandboxProvider = Literal["none", "daytona", "langsmith", "modal", "runloop"]
```

当前 deploy scope 只有两种：

```python
SandboxScope = Literal["thread", "assistant"]
```

实际 cache key 生成逻辑在 `libs/cli/deepagents_cli/deploy/templates.py` 的
`_build_backend_factory()` 中：

```python
if SANDBOX_SCOPE == "assistant":
    cache_key = f"assistant:{assistant_id}"
else:
    thread_id = get_config().get("configurable", {}).get("thread_id", "local")
    cache_key = f"thread:{thread_id}"
```

因此当前隔离关系是：

- `scope = "thread"`：一个 `thread_id` 一个 sandbox，同一个 thread 的多轮对话复用同一个 sandbox。
- `scope = "assistant"`：一个 assistant 一个 sandbox，所有用户和所有 thread 共享。
- `scope = "user"`：当前不支持。

另外，`provider = "none"` 不是真正的安全沙箱。它退回到进程内 `StateBackend`，
没有远程容器、VM、devbox 或 OS 级隔离能力，不能作为用户间安全隔离边界。

## 核心概念

子智能体开发前必须先区分 `assistant`、`user`、`thread` 和 Python 进程。
这几个概念会直接影响 sandbox cache key 的设计。

### `assistant`

`assistant` 是逻辑上的 Agent 实例或配置对象，通常由 `assistant_id` 标识。
它代表某个已部署的 graph、提示词、工具、memory、sandbox 配置组合出来的服务对象。

`assistant` 不等同于后台 Python 进程：

- 一个 Python 进程可能只服务一个 assistant。
- 一个 Python 进程也可能服务多个 assistant，取决于部署平台和运行时组织方式。
- 一个 assistant 在生产环境中也可能有多个 Python worker 或进程副本，用于扩容或高可用。
- 因此不能把 `assistant_id` 当成进程 ID 使用。

在当前 sandbox 逻辑中：

```python
cache_key = f"assistant:{assistant_id}"
```

表示按 assistant 这个逻辑身份复用 sandbox，而不是按某个 Python 进程复用 sandbox。

### `user`

`user` 是认证后的调用者身份。部署场景中通常来自 custom auth 注入的
`runtime.user.identity` 或等价运行时身份字段。

当前用户 memory 已经按用户隔离：

```text
namespace = (assistant_id, user_id)
```

但用户级 thread 权限隔离不会自动带来用户级 sandbox 隔离。`@auth.on.threads`
只负责给 LangGraph thread 资源加 owner filter，不会影响 sandbox cache key。

因此 `scope = "user"` 必须显式读取认证身份，并构造：

```python
cache_key = f"user:{assistant_id}:{user_id}"
```

### `thread`

`thread` 是一次会话线程，由 `thread_id` 标识。一个 `thread_id` 可以承载多轮对话。

当前默认行为是：

```python
cache_key = f"thread:{thread_id}"
```

因此在 `scope = "thread"` 下，同一个 `thread_id` 的多轮对话复用同一个 sandbox；
不同 `thread_id` 会使用不同 sandbox。这个行为不能严格替代用户级隔离，因为同一个用户
创建多个 thread 时会得到多个 sandbox。

### 三者关系

推荐理解为：

```text
Python process / worker
  └─ assistant_id
       ├─ user A
       │   ├─ thread 1
       │   └─ thread 2
       └─ user B
           ├─ thread 3
           └─ thread 4
```

不同 scope 的 sandbox 复用关系：

```text
scope = "thread"
  assistant A + user A + thread 1 -> sandbox T1
  assistant A + user A + thread 2 -> sandbox T2

scope = "assistant"
  assistant A + user A + thread 1 -> sandbox A
  assistant A + user B + thread 2 -> sandbox A

scope = "user"
  assistant A + user A + thread 1 -> sandbox UA
  assistant A + user A + thread 2 -> sandbox UA
  assistant A + user B + thread 3 -> sandbox UB
```

本设计要新增的是第三种：同一个 assistant 下，一个用户的所有 thread 固定到同一个 sandbox。

## 目标

实现以下能力：

1. 新增 `scope = "user"`，让同一个用户的所有 thread 复用同一个 sandbox。
2. 新增本机 Docker sandbox provider。
3. 支持“一个用户对应一个 Docker 容器沙箱”。

目标配置示例：

```toml
[sandbox]
provider = "docker"
scope = "user"
image = "python:3.12-slim"
base_dir = "/workspace"
```

目标映射关系：

```text
assistant A + user 1
  thread 1 -> docker container user-1
  thread 2 -> docker container user-1
  thread 3 -> docker container user-1

assistant A + user 2
  thread 1 -> docker container user-2
  thread 2 -> docker container user-2
```

建议 cache key：

```python
cache_key = f"user:{assistant_id}:{user_id}"
```

容器名不应直接使用原始 `user_id`，建议使用 hash：

```text
deepagents-sandbox-<sha256(cache_key)[:24]>
```

## 设计原则

- `thread` 和 `assistant` 的既有行为必须保持兼容。
- `scope = "user"` 必须显式依赖认证后的 user identity。
- `@auth.on.threads` 只负责 thread owner filter，不影响 sandbox cache key。
- 用户 memory namespace 和用户 sandbox cache key 应使用同一个 user identity 来源。
- Docker 容器是用户隔离边界；固定工作目录只是行为约束，不是完整安全边界。
- 不要把宿主机敏感目录或 Docker socket 挂载进用户容器。

## 阶段一：新增 `scope = "user"`

### 配置变更

修改 `libs/cli/deepagents_cli/deploy/config.py`：

```python
SandboxScope = Literal["thread", "assistant", "user"]
```

更新 `SandboxConfig.scope` 文档：

```text
thread: one sandbox per thread_id.
assistant: one sandbox per assistant.
user: one sandbox per assistant and authenticated user identity.
```

### 模板变更

修改 `libs/cli/deepagents_cli/deploy/templates.py` 中的 `_build_backend_factory()`。
新增一个 user identity helper，复用当前 user memory 使用的身份来源：

```python
def _get_user_identity(ctx):
    server_info = getattr(ctx, "server_info", None)
    user = getattr(server_info, "user", None) if server_info else None
    identity = getattr(user, "identity", None) if user else None
    if not identity:
        msg = "user identity is required when sandbox scope is 'user'"
        raise ValueError(msg)
    return str(identity)
```

然后扩展 cache key 生成逻辑：

```python
def _build_backend_factory(assistant_id: str):
    def _factory(ctx):
        from langgraph.config import get_config

        if SANDBOX_SCOPE == "assistant":
            cache_key = f"assistant:{assistant_id}"
        elif SANDBOX_SCOPE == "user":
            user_id = _get_user_identity(ctx)
            cache_key = f"user:{assistant_id}:{user_id}"
        else:
            thread_id = get_config().get("configurable", {}).get("thread_id", "local")
            cache_key = f"thread:{thread_id}"

        sandbox_backend = _get_or_create_sandbox(cache_key)
        ...
```

### 阶段一验证

- `deepagents.toml` 可以解析 `scope = "user"`。
- 未知 scope 仍然报错。
- 模板渲染结果包含 user 分支。
- 同一个 user identity 的不同 thread 得到同一个 cache key。
- 不同 user identity 得到不同 cache key。
- `scope = "user"` 但没有 user identity 时明确报错。
- `scope = "thread"` 和 `scope = "assistant"` 旧行为不变。

## 阶段二：新增 Docker sandbox backend

建议新增模块：

```text
libs/deepagents/deepagents/backends/docker.py
```

实现 `DockerSandbox(BaseSandbox)`，核心接口包括：

- `id`
- `execute(command, timeout=None)`
- `upload_files(files)`
- `download_files(paths)`

示意：

```python
class DockerSandbox(BaseSandbox):
    def __init__(self, *, container, workdir: str = "/workspace") -> None:
        self._container = container
        self._workdir = workdir
        self._default_timeout = 30 * 60

    @property
    def id(self) -> str:
        return self._container.id

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        ...

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        ...

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        ...
```

### `execute()`

命令应固定在 `workdir` 下执行：

```python
result = self._container.exec_run(
    ["/bin/sh", "-lc", command],
    workdir=self._workdir,
    demux=True,
)
```

使用 `/bin/sh -lc` 比 `bash -lc` 更通用，因为轻量镜像不一定安装 bash。

超时可以先通过 SDK 支持的参数或线程包装实现。若第一版不实现强超时，
必须在 docstring 和测试中明确当前行为。

### `upload_files()` 和 `download_files()`

可使用 Docker SDK archive API：

- 上传：`container.put_archive(parent_dir, tar_bytes)`
- 下载：`container.get_archive(path)`

需要覆盖以下行为：

- 只接受绝对路径。
- 非法路径返回 `invalid_path`。
- 文件不存在返回 `file_not_found`。
- 权限问题返回 `permission_denied`。
- 上传前自动创建父目录。
- 下载目录时返回 `is_directory` 或可诊断错误。

### 阶段二验证

- 创建临时 Docker 容器。
- `execute("pwd")` 返回 `/workspace`。
- `upload_files([("/workspace/a.txt", b"hello")])` 成功。
- `download_files(["/workspace/a.txt"])` 返回 `hello`。
- 继承自 `BaseSandbox` 的 `read`、`write`、`edit`、`grep`、`glob` 可用。
- 超时行为可控或明确记录为后续增强。
- 容器删除后访问返回清晰错误。

## 阶段三：新增 deploy Docker provider

### 配置变更

修改 deploy provider 类型：

```python
SandboxProvider = Literal[
    "none",
    "daytona",
    "langsmith",
    "modal",
    "runloop",
    "docker",
]
```

`SandboxConfig` 增加字段：

```python
base_dir: str = "/workspace"
```

生成模板时增加：

```python
SANDBOX_BASE_DIR = {sandbox_base_dir!r}
```

### 模板变更

在 `libs/cli/deepagents_cli/deploy/templates.py` 中增加 Docker provider block，
并把 `"docker"` 加入 `SANDBOX_BLOCKS`。

示意：

```python
SANDBOX_BLOCK_DOCKER = '''\
from deepagents.backends.docker import DockerSandbox

_SANDBOXES: dict = {}


def _container_name(cache_key: str) -> str:
    import hashlib

    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:24]
    return f"deepagents-sandbox-{digest}"


def _get_or_create_sandbox(cache_key):
    if cache_key in _SANDBOXES:
        return _SANDBOXES[cache_key]

    import docker

    client = docker.from_env()
    name = _container_name(cache_key)

    containers = client.containers.list(
        all=True,
        filters={
            "label": [
                "deepagents.sandbox=true",
                f"deepagents.cache_key={cache_key}",
            ],
        },
    )

    if containers:
        container = containers[0]
        if container.status != "running":
            container.start()
    else:
        container = client.containers.run(
            SANDBOX_IMAGE,
            command="sleep infinity",
            detach=True,
            name=name,
            working_dir=SANDBOX_BASE_DIR,
            labels={
                "deepagents.sandbox": "true",
                "deepagents.cache_key": cache_key,
            },
            mem_limit="1g",
            pids_limit=256,
            network_disabled=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
        )

    backend = DockerSandbox(container=container, workdir=SANDBOX_BASE_DIR)
    _SANDBOXES[cache_key] = backend
    logger.info("Created/reused Docker sandbox %s for key %s", container.id, cache_key)
    return backend
'''
```

### 阶段三验证

- `deepagents.toml` 可以解析 `provider = "docker"`。
- 模板渲染结果包含 Docker block。
- 第一次请求创建容器。
- 同一个 cache key 的第二次请求复用容器。
- 不同 cache key 创建不同容器。
- 容器 label 正确，便于排查和清理。

## 阶段四：实现一个用户一个 Docker sandbox

组合配置：

```toml
[sandbox]
provider = "docker"
scope = "user"
image = "python:3.12-slim"
base_dir = "/workspace"
```

运行时映射：

```text
user_id = runtime.user.identity
cache_key = user:{assistant_id}:{user_id}
container = deepagents-sandbox-<hash(cache_key)>
workdir = /workspace
```

端到端验证场景：

```text
用户 A + thread 1:
  echo a > /workspace/user.txt

用户 A + thread 2:
  cat /workspace/user.txt
  应看到 a

用户 B + thread 1:
  cat /workspace/user.txt
  应不存在

用户 A + thread 3:
  cat /workspace/user.txt
  仍应看到 a
```

这就是“同一个用户所有 thread 固定到同一个 sandbox，不同用户使用不同 sandbox”。

## 安全建议

Docker provider 创建容器时建议：

- 不挂载宿主机敏感目录。
- 不挂载 `/var/run/docker.sock`。
- 不使用 `--privileged`。
- 使用 `cap_drop=["ALL"]`。
- 使用 `security_opt=["no-new-privileges"]`。
- 限制内存、进程数和 CPU。
- 按需求决定是否 `network_disabled=True`。
- 优先使用非 root 用户镜像。
- 增加容器 TTL、idle cleanup 和最大容器数量限制。

可作为后续增强的隔离能力：

- read-only root filesystem。
- tmpfs `/tmp`。
- per-user Docker volume。
- seccomp profile。
- AppArmor profile。
- 容器资源配额和自动清理任务。

## 推荐交付顺序

1. 先加 `scope = "user"`，用现有 provider 或 fake sandbox 验证 cache key。
2. 再加 `DockerSandbox` backend，单独测试 `execute`、`upload_files`、`download_files`。
3. 再把 `docker` 接入 deploy provider。
4. 最后做端到端验证：同用户多 thread 共享，不同用户隔离。
5. 补安全参数、清理命令和文档。

## 可分派开发阶段

为了让开发工作可以拆给不同子智能体实现，建议把任务拆成互相边界清晰的
work package。每个子智能体只负责自己的文件范围，不回滚或重写其他阶段的改动。

阶段依赖关系：

```text
阶段 0：现状测试基线
  ├─ 阶段 1：scope = "user" 配置和模板 cache key
  ├─ 阶段 2：DockerSandbox backend
  └─ 阶段 5：文档和示例草案

阶段 1 + 阶段 2 完成后
  └─ 阶段 3：deploy docker provider 接入

阶段 3 完成后
  └─ 阶段 4：端到端 user-scope Docker 验证

阶段 4 完成后
  └─ 阶段 6：安全加固和运维清理能力
```

### 阶段 0：现状测试基线

目标：在改动前明确当前 deploy sandbox 行为，并给后续阶段提供回归基线。

建议负责范围：

- `libs/cli/tests/unit_tests/deploy/`
- 只新增或调整测试，不改生产代码。

需要覆盖：

- 当前 `scope = "thread"` 的 cache key 行为。
- 当前 `scope = "assistant"` 的 cache key 行为。
- 当前未知 scope 的报错行为。
- 当前 `provider = "none"` 不按 cache key 创建独立 backend 的行为，如果已有测试覆盖则只补注释。

验收条件：

- 能单独运行 deploy 配置和模板相关单元测试。
- 测试失败时能明确指出是 scope 行为漂移。

建议命令：

```bash
uv run --group test pytest libs/cli/tests/unit_tests/deploy
```

### 阶段 1：`scope = "user"`

目标：只实现用户级 sandbox scope，不引入 Docker provider。

建议负责范围：

- `libs/cli/deepagents_cli/deploy/config.py`
- `libs/cli/deepagents_cli/deploy/templates.py`
- `libs/cli/tests/unit_tests/deploy/`

不要修改：

- `libs/deepagents/deepagents/backends/`
- `libs/partners/`
- Docker provider 相关代码。

实现内容：

- 将 `SandboxScope` 扩展为 `Literal["thread", "assistant", "user"]`。
- 更新 `SandboxConfig.scope` 文档。
- 在 deploy graph 模板中新增 user identity helper。
- 在 `_build_backend_factory()` 中新增 `SANDBOX_SCOPE == "user"` 分支。
- 无 user identity 时抛出明确错误。

验收条件：

- `scope = "user"` 可以通过配置校验。
- 同一个 `assistant_id + user_id` 的不同 thread 生成同一个 cache key。
- 不同 user 生成不同 cache key。
- `thread` 和 `assistant` 旧行为不变。

### 阶段 2：`DockerSandbox` backend

目标：实现可独立测试的 Docker backend，不接入 deploy 配置。

建议负责范围：

- `libs/deepagents/deepagents/backends/docker.py`
- `libs/deepagents/deepagents/backends/__init__.py`，仅当本仓库 backend 有统一导出模式时修改。
- `libs/deepagents/tests/unit_tests/backends/`
- 可选：`libs/deepagents/tests/integration_tests/`，仅用于需要真实 Docker daemon 的测试。

不要修改：

- `libs/cli/deepagents_cli/deploy/config.py`
- `libs/cli/deepagents_cli/deploy/templates.py`

实现内容：

- 新增 `DockerSandbox(BaseSandbox)`。
- 实现 `id`、`execute()`、`upload_files()`、`download_files()`。
- `execute()` 默认在 `workdir` 下运行。
- 文件上传下载使用 Docker SDK archive API。
- 将常见失败映射为 `invalid_path`、`file_not_found`、`permission_denied`、`is_directory`。

测试建议：

- 单元测试用 fake container 覆盖参数传递和错误映射。
- 集成测试用真实 Docker 容器，默认 skip，只有检测到 Docker daemon 时运行。

验收条件：

- fake container 单元测试不需要 Docker daemon。
- 真实 Docker 集成测试可验证 `execute`、上传、下载和 BaseSandbox 继承能力。
- 轻量镜像没有 bash 时仍可通过 `/bin/sh -lc` 执行命令。

### 阶段 3：deploy `docker` provider 接入

目标：把 Docker backend 接入 deploy provider，但仍保持 user scope 和 Docker backend 可独立演进。

建议负责范围：

- `libs/cli/deepagents_cli/deploy/config.py`
- `libs/cli/deepagents_cli/deploy/templates.py`
- `libs/cli/tests/unit_tests/deploy/`
- `libs/cli/pyproject.toml`，仅当需要给 CLI 增加 Docker SDK 依赖或 extra。

依赖：

- 阶段 1 的 `scope = "user"` 分支已合入。
- 阶段 2 的 `DockerSandbox` 已可导入。

实现内容：

- 将 `"docker"` 加入 `SandboxProvider`。
- 在 `SandboxConfig` 增加 `base_dir: str = "/workspace"`。
- 模板增加 `SANDBOX_BASE_DIR`。
- 新增 `SANDBOX_BLOCK_DOCKER`。
- 将 `"docker"` 加入 `SANDBOX_BLOCKS`。
- Docker 容器按 cache key hash 命名，并写入 labels。
- 优先按 label 查找并复用已有容器。

验收条件：

- `provider = "docker"` 可以解析。
- 模板渲染包含 Docker block。
- Docker block 使用 `SANDBOX_IMAGE` 和 `SANDBOX_BASE_DIR`。
- 同 cache key 复用容器，不同 cache key 创建不同容器。

### 阶段 4：一个用户一个 Docker sandbox 端到端验证

目标：验证 `provider = "docker" + scope = "user"` 的最终用户隔离语义。

建议负责范围：

- `libs/cli/tests/integration_tests/` 或现有 deploy 集成测试目录。
- 测试 fixtures 和最小 deploy graph 运行样例。

依赖：

- 阶段 1、阶段 2、阶段 3 均已合入。

验证场景：

```text
用户 A + thread 1 写入 /workspace/user.txt
用户 A + thread 2 读取 /workspace/user.txt，应读取到同一内容
用户 B + thread 1 读取 /workspace/user.txt，应不存在
用户 A + thread 3 再次读取 /workspace/user.txt，应仍读取到同一内容
```

验收条件：

- 同用户多 thread 共享一个 Docker container。
- 不同用户得到不同 Docker container。
- 缺少 user identity 时失败清晰。
- 测试结束后清理创建的容器。

### 阶段 5：文档、示例和迁移说明

目标：让使用者知道什么时候用 `thread`、`assistant`、`user`，以及 Docker provider 的运行前提。

建议负责范围：

- `docs/`
- `examples/`，仅当需要最小 deploy 示例。
- README 或 CLI deploy 文档，按仓库现有文档组织决定。

可以与阶段 1 和阶段 2 并行，但最终内容必须等阶段 3 的实际配置字段确定后再校准。

需要说明：

- `provider = "none"` 不是安全沙箱。
- `scope = "user"` 依赖 custom auth 注入 user identity。
- Docker provider 需要本机或 self-host 环境能访问 Docker daemon。
- LangGraph Cloud 这类托管环境通常不能使用本机 Docker provider。
- 一个用户一个容器的推荐配置。

验收条件：

- 文档配置示例和最终代码字段一致。
- 文档明确安全边界和非目标。
- 有最小可复制配置片段。

#### 阶段 5 当前同步结果

当前已将检验结果和阶段 5 文档产物同步到仓库，包括：

- deploy 配置和模板的当前代码支持范围。
- `BaseSandbox` / `LangSmithSandbox` 对 `DockerSandbox` 的可扩展性依据。
- deploy 单元测试和 SDK backend 单元测试的基线结果。
- `uv` 依赖下载超时的修复方式。
- 使用 SSH 访问远端 Docker daemon 的验证结果。
- 用户向配置、迁移和运维说明：`docs/docker-user-scope-sandbox.md`。
- 最小 deploy 示例：`examples/deploy-docker-user-sandbox/`。
- examples 索引：`examples/README.md`。

当前工作树已包含 `scope = "user"`、`DockerSandbox`、`provider = "docker"`、
`base_dir` 和 Docker SSH 依赖的实现改动。合并前仍需要用最终代码再校准文档中的
错误文案、默认值和测试命令。

#### 使用者文档草案

##### 什么时候选择不同 scope

| scope | 复用粒度 | 适用场景 | 风险 |
| --- | --- | --- | --- |
| `thread` | 一个 `thread_id` 一个 sandbox | 默认选项；每个会话线程隔离 | 同一用户多个 thread 不共享文件 |
| `assistant` | 一个 assistant 一个 sandbox | 单租户、内部演示、所有线程共享工作区 | 多用户部署时会共享同一个 sandbox，不适合作为用户隔离 |
| `user` | 一个 `assistant_id + user identity` 一个 sandbox | 多用户部署；同一用户跨 thread 共享工作区 | 必须启用 auth，并且 user identity 必须稳定可信 |

`scope = "user"` 的核心语义：

```text
assistant A + user 1 + thread 1 -> sandbox user:assistant-A:user-1
assistant A + user 1 + thread 2 -> sandbox user:assistant-A:user-1
assistant A + user 2 + thread 1 -> sandbox user:assistant-A:user-2
```

因此它解决的是“同一个用户多个 thread 共享同一个沙箱，不同用户使用不同沙箱”。

##### 最小配置示例

目标配置：

```toml
[agent]
name = "my-agent"
model = "anthropic:claude-sonnet-4-6"

[sandbox]
provider = "docker"
scope = "user"
image = "python:3.12-slim"
base_dir = "/workspace"

[auth]
provider = "supabase"
```

如果只想先验证 Docker provider，而不启用用户级复用，可用：

```toml
[sandbox]
provider = "docker"
scope = "thread"
image = "python:3.12-slim"
base_dir = "/workspace"
```

如果 `scope = "user"` 但没有配置 auth，或运行时没有注入 user identity，运行时应明确失败：

```text
user identity is required when sandbox scope is 'user'
```

##### Docker provider 运行前提

Docker provider 需要运行 deploy graph 的 Python 进程能够访问 Docker daemon。
推荐的开发和自托管方式是使用 Docker SSH transport，而不是开放 Docker 明文 TCP 端口。

本工程当前已验证的开发环境：

```powershell
$env:DOCKER_HOST = "ssh://deepagents-docker"
```

验证命令：

```powershell
ssh -o BatchMode=yes deepagents-docker "hostname && docker version --format '{{.Server.Version}}'"
```

已验证输出：

```text
aibot
28.4.0
```

对应本地记录文件：

- `.codex/docker-ssh.md`

Docker provider 当前依赖：

```toml
docker[ssh] >= 7.0.0, < 8.0.0
```

当前工作树采用的依赖声明：

- CLI extra：`deepagents-cli[docker]`
- all-sandboxes extra 包含 `docker`
- deploy bundle 在 `provider = "docker"` 时注入 `docker[ssh]>=7.0.0,<8.0.0`

如果后续 `DockerSandbox` 被作为 SDK 稳定一等 backend 对外推荐，再考虑补 SDK 侧
`deepagents[docker]` optional extra。

##### Docker SSH 而不是 2375

不要在开发服务器上开放未认证的 Docker TCP：

```text
tcp://0.0.0.0:2375
```

原因是未保护的 Docker API 基本等同于暴露宿主机 root 级控制能力。

推荐使用：

```text
DOCKER_HOST=ssh://deepagents-docker
```

如果生产环境必须使用 TCP，应使用 TLS 保护的 `2376`，并通过防火墙限制来源 IP。
本需求当前不需要开放 `2375` 或 `2376`。

##### 安全边界说明

`provider = "docker"` 提供容器级隔离，但不是完整的强安全沙箱。默认实现必须遵守：

- 不挂载宿主机敏感目录。
- 不挂载 `/var/run/docker.sock`。
- 不使用 privileged 容器。
- 默认 drop capabilities。
- 默认启用 `no-new-privileges`。
- 默认限制 memory、pids、CPU。
- 默认禁用网络，除非用户显式开启。
- 容器名使用 hash，不直接暴露原始 user id。

第一版可以先固定保守默认值，配置化资源限制和网络开关可放入阶段 6。

##### 从现有配置迁移

从无沙箱迁移：

```toml
[sandbox]
provider = "none"
```

改为：

```toml
[sandbox]
provider = "docker"
scope = "thread"
image = "python:3.12-slim"
base_dir = "/workspace"
```

先使用 `scope = "thread"` 验证 Docker provider 可用，再切到 `scope = "user"`。

从 assistant 级沙箱迁移：

```toml
[sandbox]
provider = "langsmith"
scope = "assistant"
```

如果是多用户部署，不建议直接保持 `assistant` scope。应改为：

```toml
[sandbox]
provider = "docker"
scope = "user"
image = "python:3.12-slim"
base_dir = "/workspace"
```

并确认：

- 已配置 `[auth]`。
- `runtime.user.identity` 在每次请求中稳定存在。
- 同一用户多个 thread 的文件复用是预期行为。
- 不同用户不能读取彼此容器中的文件。

##### 文档最终校准清单

阶段 1-4 合入后，需要回头校准以下内容：

- `deepagents.toml` 是否继续使用 `base_dir` 作为字段名。
- Docker SDK 依赖是否继续只在 CLI/deploy bundle 侧声明。
- `scope = "user"` 缺少身份时的最终错误文案。
- Docker 默认镜像是否仍沿用当前 `python:3`，示例是否继续使用 `python:3.12-slim`。
- 默认是否继续禁用网络。
- 资源限制默认值是否继续为 `mem_limit="1g"`、`pids_limit=256`。
- 端到端测试是否需要真实 Docker daemon，还是提供 fake provider 覆盖大部分逻辑。

### 阶段 6：安全加固和容器生命周期管理

目标：在功能可用后补齐生产运行需要的安全和运维能力。

建议负责范围：

- Docker provider block。
- `DockerSandbox` backend。
- 运维文档和清理脚本。

实现内容可以分批：

- 容器 TTL 或 idle timeout。
- 最大容器数量限制。
- cleanup 命令或脚本。
- CPU、memory、pids 限制配置化。
- `network_disabled` 配置化。
- 可选 per-user volume。
- 可选 read-only root filesystem、tmpfs、seccomp、AppArmor。

验收条件：

- 不影响阶段 4 的用户隔离语义。
- 默认配置偏保守。
- 清理逻辑不会删除非 Deep Agents 创建的容器。

## 子智能体交接约定

每个子智能体完成任务时需要给出：

- 修改的文件列表。
- 新增或修改的 public API。
- 运行过的测试命令和结果。
- 未覆盖的风险或需要后续阶段接手的事项。

并行开发时的文件所有权建议：

- 子智能体 A：阶段 1，负责 deploy scope 配置和模板。
- 子智能体 B：阶段 2，负责 `DockerSandbox` backend。
- 子智能体 C：阶段 0 和阶段 4，负责测试基线与端到端验证。
- 子智能体 D：阶段 5，负责文档和示例，等 A/B/C 的字段稳定后校准。
- 子智能体 E：阶段 6，负责安全和生命周期增强，等核心功能合入后启动。

集成顺序建议：

1. 合入阶段 0 测试基线。
2. 合入阶段 1，并确认旧 scope 测试仍通过。
3. 合入阶段 2，并确认 backend 单元测试通过。
4. 合入阶段 3，并确认 deploy 模板测试通过。
5. 合入阶段 4，并跑 Docker 端到端验证。
6. 合入阶段 5 文档和示例校准。
7. 合入阶段 6 安全和生命周期增强。

## 当前工程验证记录（2026-05-19）

本节记录实现前对当前代码和开发环境的实际检验结果，用于判断本设计是否可落地。

### 代码现状核对

已确认当前 deploy 配置层只支持以下 sandbox provider 和 scope：

```python
SandboxProvider = Literal["none", "daytona", "langsmith", "modal", "runloop"]
SandboxScope = Literal["thread", "assistant"]
```

对应文件：

- `libs/cli/deepagents_cli/deploy/config.py`
- `libs/cli/deepagents_cli/deploy/templates.py`

因此新增 `provider = "docker"` 和 `scope = "user"` 需要同步更新：

- `SandboxProvider`
- `SandboxScope`
- `VALID_SANDBOX_PROVIDERS`
- `VALID_SANDBOX_SCOPES`
- `_ALLOWED_SANDBOX_KEYS`
- starter config 示例
- `SANDBOX_BLOCKS`
- deploy graph 模板渲染参数和测试

已确认当前模板中 `_build_backend_factory()` 只区分 `assistant` 和默认 `thread`：

```python
if SANDBOX_SCOPE == "assistant":
    cache_key = f"assistant:{assistant_id}"
else:
    thread_id = get_config().get("configurable", {}).get("thread_id", "local")
    cache_key = f"thread:{thread_id}"
```

因此 `scope = "user"` 需要新增明确分支，而不是修改现有 `thread` 默认分支。

已确认用户 memory 已经有用户身份来源实现：

- `_make_user_namespace_factory(assistant_id)` 使用 `rt.server_info.user.identity`
- `make_graph()` 中用户 memory seeding 使用 `runtime.user.identity`

因此 sandbox 的 user scope 应与 user memory 使用同一身份来源，避免 memory 与 sandbox 隔离口径不一致。

### SDK sandbox 抽象核对

已确认 `libs/deepagents/deepagents/backends/sandbox.py` 中的 `BaseSandbox` 是合适扩展点。

`DockerSandbox` 可以按 `LangSmithSandbox` 的模式实现：

- 继承 `BaseSandbox`
- 实现 `id`
- 实现 `execute(command, *, timeout=None)`
- 实现 `upload_files(files)`
- 实现 `download_files(paths)`

`BaseSandbox` 已经提供：

- `read`
- `write`
- `edit`
- `grep`
- `glob`
- async wrappers inherited through protocol behavior

因此 Docker backend 不需要重新实现这些高层文件操作，只需要确保 Docker 容器内具备 `python3`、`grep`、`/bin/sh` 等基础命令。

### 已运行测试

deploy 配置和模板相关单元测试已通过：

```powershell
uv run --group test pytest tests/unit_tests/deploy -q
```

结果：

```text
152 passed, 1 warning
```

SDK sandbox/backend 相关测试首次运行时依赖下载失败，失败点是 `uv` 下载 `regex==2026.2.28` 超时，不是测试失败。

修复方式：

```powershell
$env:UV_HTTP_TIMEOUT='300'
$env:UV_LINK_MODE='copy'
uv sync --group test
```

随后重跑 SDK backend 测试：

```powershell
uv run --group test pytest tests/unit_tests/backends/test_sandbox_backend.py tests/unit_tests/backends/test_langsmith_sandbox.py -q
```

结果：

```text
63 passed, 2 warnings
```

剩余警告来自当前 `.venv` 使用 Python 3.14：

- `Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater.`
- `langsmith.sandbox is in alpha.`

测试已通过，但完整开发建议按仓库 `Makefile` 的预期使用 Python 3.12（`acp` 除外）。

### Docker SSH 环境验证

当前不开放 Docker TCP `2375` / `2376`，使用 SSH 访问远端 Docker daemon。

已配置并验证的 SSH alias：

```sshconfig
Host deepagents-docker
    HostName 192.168.153.130
    User glw
    IdentityFile ~/.ssh/id_rsa_deepagents_docker
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
```

连接验证命令：

```powershell
ssh -o BatchMode=yes deepagents-docker "hostname && docker version --format '{{.Server.Version}}'"
```

结果：

```text
aibot
28.4.0
```

已确认：

- `192.168.153.130:22` 可访问。
- `glw` 用户可以无交互 SSH 登录。
- 远端系统是 Ubuntu。
- 远端 Docker CLI 存在。
- 远端 Docker server version 为 `28.4.0`。
- `glw` 已在远端 `docker` 组中，可以访问 Docker daemon。

Docker provider 开发时建议使用：

```powershell
$env:DOCKER_HOST = "ssh://deepagents-docker"
```

当前本机状态：

- 本机未安装 Docker CLI。
- 当前 base Python 未安装 `docker` Python package。
- Deep Agents Docker provider 需要依赖 Docker SDK for Python，并通过 `DOCKER_HOST=ssh://deepagents-docker` 访问远端 daemon。

### 当前可行性结论

本设计可行，且可以按阶段独立开发：

1. `scope = "user"` 是 CLI deploy 模板层的增量变更，风险低。
2. `DockerSandbox` 可以作为 SDK backend 独立实现和测试，不需要先接入 deploy。
3. `provider = "docker"` 接入 deploy 时，需要补齐 bundle 依赖渲染和 `base_dir` 配置。
4. 端到端验证可使用已配置的 `deepagents-docker` SSH alias，不需要开放 Docker 明文 TCP 端口。

当前主要待补环境：

- 给相关开发/测试环境安装 Docker SDK for Python，例如 `docker>=7,<8`。
- 决定 Docker SDK 是 SDK 核心依赖、optional extra，还是仅 deploy bundle 在 `provider = "docker"` 时注入。
- 若执行完整 SDK 测试，建议准备 Python 3.12 环境，减少 Python 3.14 兼容噪声。
