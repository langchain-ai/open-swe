# Docker Sandbox Provider — Implementation Design

## 背景与目标

**Issue**: [#1233 — Feature: Add SANDBOX_TYPE=docker for local containerized development/testing](https://github.com/langchain-ai/open-swe/issues/1233)

当前 `SANDBOX_TYPE=local` 在宿主机直接执行命令，完全无隔离。`SANDBOX_TYPE=langsmith/daytona/runloop/modal` 都需要云账号或 API key。开发者本地调试没有介于两者之间的选项。

Docker Sandbox Provider 用本地 Docker 容器提供完整隔离，无需任何云依赖，填补这个空缺。

---

## 架构分析

### deepagents 协议层

`BaseSandbox`（`deepagents.backends.sandbox`）是所有沙盒实现的基类。子类只需实现三个抽象方法，其余所有文件操作（`ls`、`read`、`write`、`edit`、`grep`、`glob`）都通过 `execute()` 自动派生：

| 方法 | 说明 |
|---|---|
| `execute(command, *, timeout) → ExecuteResponse` | **必须实现**。所有其他操作依赖它 |
| `upload_files(files) → list[FileUploadResponse]` | **必须实现**。`write()` 用它传输文件内容 |
| `download_files(paths) → list[FileDownloadResponse]` | **必须实现** |
| `id → str` | **必须实现**。返回容器 ID |

### 现有集成模式

每个集成是一个独立文件（`agent/integrations/<name>.py`），通过懒加载注册到 `agent/utils/sandbox.py` 的 `SANDBOX_FACTORIES` 字典中：

```python
SANDBOX_FACTORIES: dict[str, tuple[str, str]] = {
    "docker": ("agent.integrations.docker", "create_docker_sandbox"),  # 新增
    ...
}
```

---

## 实现方案

### 新增文件：`agent/integrations/docker.py`

#### 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SANDBOX_TYPE` | `langsmith` | 设为 `docker` 启用此 provider |
| `DOCKER_SANDBOX_IMAGE` | `ubuntu:22.04` | 容器基础镜像 |
| `DOCKER_SANDBOX_WORKSPACE` | `/workspace` | 容器内工作目录 |

#### 核心类：`DockerSandbox(BaseSandbox)`

```python
from __future__ import annotations

import io
import os
import shlex
import tarfile

import docker
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

DOCKER_DEFAULT_IMAGE = "ubuntu:22.04"
DOCKER_IMAGE_ENV = "DOCKER_SANDBOX_IMAGE"
DOCKER_WORKSPACE_ENV = "DOCKER_SANDBOX_WORKSPACE"
DOCKER_DEFAULT_WORKSPACE = "/workspace"


class DockerSandbox(BaseSandbox):
    """Local Docker container sandbox.

    Provides the same SandboxBackendProtocol interface as cloud providers
    (LangSmith, Daytona, …) but runs entirely on the local Docker daemon —
    no API keys, no network egress for sandbox I/O.

    The container is kept alive with ``tail -f /dev/null`` as PID 1 and
    all agent commands are run via ``docker exec``.  File I/O uses the Docker
    tar archive API (``put_archive`` / ``get_archive``).
    """

    def __init__(self, container, workspace: str = DOCKER_DEFAULT_WORKSPACE) -> None:
        self._container = container
        self._workspace = workspace

    @property
    def id(self) -> str:
        return self._container.id

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Run a shell command inside the Docker container."""
        kwargs: dict = {
            "cmd": ["bash", "-c", command],
            "stdout": True,
            "stderr": True,
            "workdir": self._workspace,
        }
        if timeout is not None:
            # docker-py does not have a per-exec timeout; enforce via shell
            kwargs["cmd"] = ["bash", "-c", f"timeout {timeout} {command}"]

        result = self._container.exec_run(**kwargs)
        output = result.output.decode("utf-8", errors="replace") if result.output else ""
        return ExecuteResponse(
            output=output,
            exit_code=result.exit_code,
            truncated=False,
        )

    def upload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        """Upload files into the container via tar archive."""
        results: list[FileUploadResponse] = []
        for path, content in files:
            try:
                dir_path = os.path.dirname(path) or "/"
                # ensure parent directory exists
                self._container.exec_run(
                    ["bash", "-c", f"mkdir -p {shlex.quote(dir_path)}"]
                )
                # build a single-file tar in memory
                tar_stream = io.BytesIO()
                with tarfile.open(fileobj=tar_stream, mode="w") as tar:
                    info = tarfile.TarInfo(name=os.path.basename(path))
                    info.size = len(content)
                    tar.addfile(info, io.BytesIO(content))
                tar_stream.seek(0)
                self._container.put_archive(dir_path, tar_stream)
                results.append(FileUploadResponse(path=path))
            except Exception as exc:
                results.append(FileUploadResponse(path=path, error=str(exc)))
        return results

    def download_files(
        self, paths: list[str]
    ) -> list[FileDownloadResponse]:
        """Download files from the container via tar archive."""
        results: list[FileDownloadResponse] = []
        for path in paths:
            try:
                bits, _ = self._container.get_archive(path)
                tar_stream = io.BytesIO(b"".join(bits))
                with tarfile.open(fileobj=tar_stream) as tar:
                    member = tar.getmember(os.path.basename(path))
                    f = tar.extractfile(member)
                    content = f.read() if f else b""
                results.append(FileDownloadResponse(path=path, content=content))
            except Exception as exc:
                results.append(
                    FileDownloadResponse(path=path, error=str(exc))
                )
        return results
```

#### 工厂函数：`create_docker_sandbox`

```python
def create_docker_sandbox(sandbox_id: str | None = None) -> DockerSandbox:
    """Create or reconnect to a Docker container sandbox.

    Requires Docker to be installed and the Docker daemon to be running.
    The ``docker`` Python package must be available (``pip install docker``).

    Args:
        sandbox_id: Optional container ID or name to reconnect to.
            If ``None``, a new container is created from the configured image.

    Returns:
        DockerSandbox instance implementing SandboxBackendProtocol.
    """
    try:
        import docker as _docker
    except ImportError as exc:
        raise ImportError(
            "The 'docker' package is required for SANDBOX_TYPE=docker. "
            "Install it with: pip install docker"
        ) from exc

    image = os.getenv(DOCKER_IMAGE_ENV, DOCKER_DEFAULT_IMAGE)
    workspace = os.getenv(DOCKER_WORKSPACE_ENV, DOCKER_DEFAULT_WORKSPACE)

    client = _docker.from_env()

    if sandbox_id:
        container = client.containers.get(sandbox_id)
        if container.status != "running":
            container.start()
    else:
        container = client.containers.run(
            image,
            command="tail -f /dev/null",
            detach=True,
            remove=False,
            working_dir=workspace,
        )
        # ensure workspace directory exists in the new container
        container.exec_run(["bash", "-c", f"mkdir -p {shlex.quote(workspace)}"])

    return DockerSandbox(container, workspace=workspace)
```

### 修改文件：`agent/utils/sandbox.py`

```python
SANDBOX_FACTORIES: dict[str, tuple[str, str]] = {
    "langsmith": ("agent.integrations.langsmith", "create_langsmith_sandbox"),
    "daytona": ("agent.integrations.daytona", "create_daytona_sandbox"),
    "modal": ("agent.integrations.modal", "create_modal_sandbox"),
    "runloop": ("agent.integrations.runloop", "create_runloop_sandbox"),
    "local": ("agent.integrations.local", "create_local_sandbox"),
    "docker": ("agent.integrations.docker", "create_docker_sandbox"),  # ← 新增一行
}
```

### 修改文件：`CUSTOMIZATION.md`

在 sandbox providers 表格中新增一行：

```markdown
| `docker` | `agent/integrations/docker.py` | Docker daemon on localhost; `SANDBOX_TYPE="docker"`, optional `DOCKER_SANDBOX_IMAGE` |
```

在 Warning 块后面补充：

```markdown
> **Note**: `docker` runs commands inside an isolated Linux container on your
> local machine.  Requires Docker Desktop (or Docker Engine) to be installed and
> the `docker` Python package (`pip install docker`).  No API keys needed.
> Container lifecycle is managed per-thread; containers are **not** removed
> automatically — run `docker ps -a` and clean up after testing.
```

---

## 测试方案

### 测试文件：`tests/test_docker_integration.py`

遵循已有的 `test_daytona_integration.py` 模式：用 `monkeypatch.setitem(sys.modules, ...)` 注入 fake 模块，用 `importlib.util.spec_from_file_location` 加载被测模块，完全不依赖真实 Docker daemon。

#### 测试覆盖矩阵

| 测试名 | 覆盖点 |
|---|---|
| `test_create_new_container_default_image` | 无 sandbox_id 时使用默认镜像 `ubuntu:22.04` |
| `test_create_new_container_custom_image` | `DOCKER_SANDBOX_IMAGE` env var 生效 |
| `test_reconnect_running_container` | sandbox_id 传入时调用 `containers.get` 而非 `run` |
| `test_reconnect_stopped_container_starts_it` | stopped 状态容器会被 `start()` |
| `test_missing_docker_package_raises_import_error` | `import docker` 失败时给出清晰错误 |
| `test_execute_returns_correct_response` | `exec_run` 结果正确映射到 `ExecuteResponse` |
| `test_execute_with_timeout` | timeout 参数通过 `bash timeout` 包装 |
| `test_id_property` | `sandbox.id` 返回容器 ID |
| `test_upload_files_creates_tar_and_calls_put_archive` | `upload_files` 构建 tar 并调用 `put_archive` |
| `test_upload_files_creates_parent_directory` | 上传前会 `mkdir -p` 父目录 |
| `test_upload_files_handles_exception` | docker 异常时返回 `FileUploadResponse(error=...)` |
| `test_download_files_extracts_content` | `get_archive` 返回内容正确解包 |
| `test_download_files_handles_exception` | docker 异常时返回 `FileDownloadResponse(error=...)` |
| `test_sandbox_registered_in_factory` | `SANDBOX_FACTORIES["docker"]` 存在且可加载 |

#### 测试结构示例

```python
# tests/test_docker_integration.py
import importlib.util
import io
import sys
import tarfile
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Fake docker module
# ---------------------------------------------------------------------------

class _FakeExecResult:
    def __init__(self, exit_code: int = 0, output: bytes = b"ok"):
        self.exit_code = exit_code
        self.output = output


class _FakeContainer:
    def __init__(self, container_id: str = "abc123", status: str = "running"):
        self.id = container_id
        self.status = status
        self.exec_run_calls: list = []
        self.put_archive_calls: list = []
        self.start_calls: int = 0

    def exec_run(self, cmd, **kwargs) -> _FakeExecResult:
        self.exec_run_calls.append((cmd, kwargs))
        return _FakeExecResult()

    def start(self) -> None:
        self.start_calls += 1

    def get_archive(self, path: str):
        # build a minimal tar containing a single file
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            content = b"file-content"
            info = tarfile.TarInfo(name=path.split("/")[-1])
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        buf.seek(0)
        return [buf.read()], {}

    def put_archive(self, path: str, data) -> None:
        self.put_archive_calls.append((path, data))


class _FakeDockerClient:
    def __init__(self):
        self.containers = _FakeContainers()


class _FakeContainers:
    def __init__(self):
        self._created = _FakeContainer()
        self._existing = _FakeContainer("existing-id", status="running")
        self._stopped = _FakeContainer("stopped-id", status="exited")

    def run(self, image, **kwargs) -> _FakeContainer:
        self._created._image = image
        self._created._run_kwargs = kwargs
        return self._created

    def get(self, container_id: str) -> _FakeContainer:
        if container_id == "stopped-id":
            return self._stopped
        return self._existing


def _make_fake_docker(fake_client: _FakeDockerClient):
    mod = types.ModuleType("docker")
    mod.from_env = lambda: fake_client
    return mod


def _load_docker_module(monkeypatch, fake_docker=None):
    if fake_docker is None:
        fake_docker = _make_fake_docker(_FakeDockerClient())
    monkeypatch.setitem(sys.modules, "docker", fake_docker)
    module_path = ROOT / "agent" / "integrations" / "docker.py"
    spec = importlib.util.spec_from_file_location("docker_under_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------

def test_create_new_container_default_image(monkeypatch):
    monkeypatch.delenv("DOCKER_SANDBOX_IMAGE", raising=False)
    client = _FakeDockerClient()
    module = _load_docker_module(monkeypatch, _make_fake_docker(client))

    sandbox = module.create_docker_sandbox(sandbox_id=None)

    assert client.containers._created._image == "ubuntu:22.04"
    assert sandbox.id == client.containers._created.id


def test_create_new_container_custom_image(monkeypatch):
    monkeypatch.setenv("DOCKER_SANDBOX_IMAGE", "python:3.12-slim")
    client = _FakeDockerClient()
    module = _load_docker_module(monkeypatch, _make_fake_docker(client))

    module.create_docker_sandbox(sandbox_id=None)

    assert client.containers._created._image == "python:3.12-slim"


def test_reconnect_running_container(monkeypatch):
    monkeypatch.delenv("DOCKER_SANDBOX_IMAGE", raising=False)
    client = _FakeDockerClient()
    module = _load_docker_module(monkeypatch, _make_fake_docker(client))

    sandbox = module.create_docker_sandbox(sandbox_id="existing-id")

    assert sandbox.id == "existing-id"
    assert client.containers._existing.start_calls == 0  # already running


def test_reconnect_stopped_container_starts_it(monkeypatch):
    client = _FakeDockerClient()
    module = _load_docker_module(monkeypatch, _make_fake_docker(client))

    sandbox = module.create_docker_sandbox(sandbox_id="stopped-id")

    assert client.containers._stopped.start_calls == 1


def test_missing_docker_package_raises_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "docker", None)  # simulate missing package
    module_path = ROOT / "agent" / "integrations" / "docker.py"
    spec = importlib.util.spec_from_file_location("docker_under_test2", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    try:
        module.create_docker_sandbox()
    except ImportError as exc:
        assert "pip install docker" in str(exc)
    else:
        raise AssertionError("expected ImportError")


# ---------------------------------------------------------------------------
# DockerSandbox unit tests
# ---------------------------------------------------------------------------

def test_execute_returns_correct_response(monkeypatch):
    container = _FakeContainer()
    container.exec_run = lambda cmd, **kw: _FakeExecResult(exit_code=0, output=b"hello\n")
    module = _load_docker_module(monkeypatch)

    sandbox = module.DockerSandbox(container)
    result = sandbox.execute("echo hello")

    assert result.exit_code == 0
    assert result.output == "hello\n"
    assert result.truncated is False


def test_execute_with_timeout_wraps_in_bash_timeout(monkeypatch):
    container = _FakeContainer()
    calls = []
    container.exec_run = lambda cmd, **kw: (calls.append(cmd) or _FakeExecResult())
    module = _load_docker_module(monkeypatch)

    sandbox = module.DockerSandbox(container)
    sandbox.execute("sleep 10", timeout=5)

    assert any("timeout 5" in " ".join(c) for c in calls)


def test_id_property(monkeypatch):
    container = _FakeContainer(container_id="deadbeef")
    module = _load_docker_module(monkeypatch)

    sandbox = module.DockerSandbox(container)
    assert sandbox.id == "deadbeef"


def test_upload_files_calls_put_archive(monkeypatch):
    container = _FakeContainer()
    module = _load_docker_module(monkeypatch)

    sandbox = module.DockerSandbox(container)
    results = sandbox.upload_files([("/workspace/hello.txt", b"hello")])

    assert len(results) == 1
    assert results[0].error is None
    assert len(container.put_archive_calls) == 1


def test_upload_files_handles_exception(monkeypatch):
    container = _FakeContainer()
    container.put_archive = MagicMock(side_effect=RuntimeError("docker error"))
    module = _load_docker_module(monkeypatch)

    sandbox = module.DockerSandbox(container)
    results = sandbox.upload_files([("/workspace/fail.txt", b"data")])

    assert results[0].error is not None
    assert "docker error" in results[0].error


def test_download_files_returns_content(monkeypatch):
    container = _FakeContainer()
    module = _load_docker_module(monkeypatch)

    sandbox = module.DockerSandbox(container)
    results = sandbox.download_files(["/workspace/file.txt"])

    assert len(results) == 1
    assert results[0].content == b"file-content"
    assert results[0].error is None


def test_download_files_handles_exception(monkeypatch):
    container = _FakeContainer()
    container.get_archive = MagicMock(side_effect=RuntimeError("not found"))
    module = _load_docker_module(monkeypatch)

    sandbox = module.DockerSandbox(container)
    results = sandbox.download_files(["/workspace/missing.txt"])

    assert results[0].error is not None
```

---

## 涉及文件汇总

| 操作 | 文件 | 说明 |
|---|---|---|
| **新建** | `agent/integrations/docker.py` | DockerSandbox 类 + 工厂函数（约 120 行） |
| **修改** | `agent/utils/sandbox.py` | SANDBOX_FACTORIES 字典新增 1 行 |
| **修改** | `CUSTOMIZATION.md` | providers 表格新增 docker 行 + 说明 |
| **新建** | `tests/test_docker_integration.py` | 14 个单元测试，无需真实 Docker |

---

## 依赖管理

`docker-py` 是可选依赖，只在 `SANDBOX_TYPE=docker` 时才需要。不修改 `pyproject.toml`（避免给所有使用者新增强制依赖），而是在 `create_docker_sandbox` 内部懒加载，缺少时抛出带安装指引的 `ImportError`。

如维护者希望在 `pyproject.toml` 中声明为可选依赖，可以补充：

```toml
[project.optional-dependencies]
docker = ["docker>=7.0.0"]
```

---

## 限制与注意事项

| 项目 | 说明 |
|---|---|
| 容器清理 | 容器退出后不自动删除（`remove=False`），需用户手动 `docker rm` 或配合 `SANDBOX_TYPE=local` cleanup 逻辑 |
| 网络隔离 | 默认使用 bridge 网络，容器可以访问宿主网络，不提供 egress 过滤 |
| 持久化 | 容器文件系统在 `container.stop()` 后保留；重启后可用 sandbox_id 复用 |
| GitHub CLI | `GH_TOKEN=dummy gh ...` 在 Docker 沙盒中不可用（无 LangSmith proxy）；本地开发时直接用真实 `GH_TOKEN` |
| Windows | Docker Desktop for Windows 支持 Linux 容器，命令兼容 |
| 超时 | docker-py 的 `exec_run` 无原生超时；通过 `bash timeout <n>` 实现软限制 |

---

## PR 提交说明草稿

```
feat: add SANDBOX_TYPE=docker for local containerized development

Closes #1233

## Problem
Developers had two choices for local development:
- `SANDBOX_TYPE=local`: no isolation, runs commands on host
- Cloud providers (langsmith/daytona/…): require API keys

There was no middle ground offering container isolation without cloud dependencies.

## Solution
Add a Docker sandbox provider that runs each agent session in a local Docker
container via docker-py. Implements `BaseSandbox` by mapping `execute()` to
`docker exec` and file I/O to the Docker tar archive API.

## Changes
- `agent/integrations/docker.py` — `DockerSandbox` + `create_docker_sandbox()`
- `agent/utils/sandbox.py` — register `"docker"` in `SANDBOX_FACTORIES`
- `CUSTOMIZATION.md` — add Docker row to the providers table
- `tests/test_docker_integration.py` — 14 unit tests (no real Docker needed)

## Usage
SANDBOX_TYPE=docker python -m agent.webapp
# optionally:
DOCKER_SANDBOX_IMAGE=python:3.12-slim SANDBOX_TYPE=docker ...

## Testing
All 14 tests pass with no Docker daemon required (fake docker module via monkeypatch).
```
