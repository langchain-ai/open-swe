"""Unit tests for the Docker sandbox provider (agent/integrations/docker.py).

All tests run without a real Docker daemon.  The ``docker`` package is replaced
with a lightweight fake module injected via ``monkeypatch.setitem``, and the
integration module is loaded in isolation via ``importlib`` — the same
technique used in ``test_daytona_integration.py``.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import tarfile
import types
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Fake docker module helpers
# ---------------------------------------------------------------------------


def _make_tar_bytes(filename: str, content: bytes) -> bytes:
    """Build an in-memory tar archive containing a single file."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=filename)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    buf.seek(0)
    return buf.read()


class _FakeExecResult:
    def __init__(self, exit_code: int = 0, output: bytes = b"ok"):
        self.exit_code = exit_code
        self.output = output


class _FakeContainer:
    """Minimal stand-in for docker.models.containers.Container."""

    def __init__(
        self,
        container_id: str = "abc123def456",
        status: str = "running",
        exec_result: _FakeExecResult | None = None,
    ) -> None:
        self.id = container_id
        self.status = status
        self._exec_result = exec_result or _FakeExecResult()
        self.exec_run_calls: list[tuple] = []
        self.put_archive_calls: list[tuple] = []
        self.start_calls: int = 0

    def exec_run(self, cmd, **kwargs) -> _FakeExecResult:
        self.exec_run_calls.append((cmd, kwargs))
        return self._exec_result

    def start(self) -> None:
        self.start_calls += 1

    def get_archive(self, path: str):
        content = _make_tar_bytes(path.rsplit("/", 1)[-1], b"file-content")
        return [content], {}

    def put_archive(self, path: str, data) -> None:
        self.put_archive_calls.append((path, data))


class _FakeContainerCollection:
    def __init__(self) -> None:
        self.running = _FakeContainer("running-id", "running")
        self.stopped = _FakeContainer("stopped-id", "exited")
        self.fresh = _FakeContainer("fresh-id", "running")
        self._image_used: str | None = None
        self._run_kwargs: dict = {}

    def run(self, image: str, **kwargs) -> _FakeContainer:
        self._image_used = image
        self._run_kwargs = kwargs
        return self.fresh

    def get(self, container_id: str) -> _FakeContainer:
        if container_id == "stopped-id":
            return self.stopped
        return self.running


class _FakeDockerClient:
    def __init__(self) -> None:
        self.containers = _FakeContainerCollection()


def _make_fake_docker_module(fake_client: _FakeDockerClient) -> types.ModuleType:
    mod = types.ModuleType("docker")
    mod.from_env = lambda: fake_client  # type: ignore[attr-defined]
    return mod


def _load_docker_module(
    monkeypatch,
    fake_docker: types.ModuleType | None = None,
) -> types.ModuleType:
    """Load agent/integrations/docker.py with a patched 'docker' import."""
    if fake_docker is None:
        fake_docker = _make_fake_docker_module(_FakeDockerClient())
    monkeypatch.setitem(sys.modules, "docker", fake_docker)
    module_path = ROOT / "agent" / "integrations" / "docker.py"
    spec = importlib.util.spec_from_file_location("docker_under_test", module_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Factory: create_docker_sandbox
# ---------------------------------------------------------------------------


def test_create_new_container_uses_default_image(monkeypatch):
    """No sandbox_id + no env override → ubuntu:22.04."""
    monkeypatch.delenv("DOCKER_SANDBOX_IMAGE", raising=False)
    client = _FakeDockerClient()
    module = _load_docker_module(monkeypatch, _make_fake_docker_module(client))

    sandbox = module.create_docker_sandbox(sandbox_id=None)

    assert client.containers._image_used == "ubuntu:22.04"
    assert sandbox.id == client.containers.fresh.id


def test_create_new_container_uses_custom_image(monkeypatch):
    """DOCKER_SANDBOX_IMAGE env var overrides the default."""
    monkeypatch.setenv("DOCKER_SANDBOX_IMAGE", "python:3.12-slim")
    client = _FakeDockerClient()
    module = _load_docker_module(monkeypatch, _make_fake_docker_module(client))

    module.create_docker_sandbox(sandbox_id=None)

    assert client.containers._image_used == "python:3.12-slim"


def test_create_new_container_runs_detached_with_keepalive(monkeypatch):
    """New container is detached and uses tail -f /dev/null as keepalive."""
    monkeypatch.delenv("DOCKER_SANDBOX_IMAGE", raising=False)
    client = _FakeDockerClient()
    module = _load_docker_module(monkeypatch, _make_fake_docker_module(client))

    module.create_docker_sandbox(sandbox_id=None)

    kwargs = client.containers._run_kwargs
    assert kwargs.get("detach") is True
    assert kwargs.get("command") == "tail -f /dev/null"


def test_reconnect_running_container_does_not_start(monkeypatch):
    """Reconnecting to a running container must not call start()."""
    client = _FakeDockerClient()
    module = _load_docker_module(monkeypatch, _make_fake_docker_module(client))

    sandbox = module.create_docker_sandbox(sandbox_id="running-id")

    assert sandbox.id == "running-id"
    assert client.containers.running.start_calls == 0


def test_reconnect_stopped_container_starts_it(monkeypatch):
    """Reconnecting to a stopped container must call start() once."""
    client = _FakeDockerClient()
    module = _load_docker_module(monkeypatch, _make_fake_docker_module(client))

    module.create_docker_sandbox(sandbox_id="stopped-id")

    assert client.containers.stopped.start_calls == 1


def test_missing_docker_package_raises_import_error_with_hint(monkeypatch):
    """When 'docker' is not installed, ImportError must mention pip install."""
    monkeypatch.setitem(sys.modules, "docker", None)  # simulate missing package
    module_path = ROOT / "agent" / "integrations" / "docker.py"
    spec = importlib.util.spec_from_file_location("docker_under_test_nopkg", module_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    try:
        mod.create_docker_sandbox()
    except ImportError as exc:
        assert "pip install docker" in str(exc)
    else:
        raise AssertionError("expected ImportError when docker package is missing")


def test_custom_workspace_is_passed_to_sandbox(monkeypatch):
    """DOCKER_SANDBOX_WORKSPACE env var is forwarded to DockerSandbox."""
    monkeypatch.setenv("DOCKER_SANDBOX_WORKSPACE", "/custom/workdir")
    client = _FakeDockerClient()
    module = _load_docker_module(monkeypatch, _make_fake_docker_module(client))

    sandbox = module.create_docker_sandbox(sandbox_id=None)

    assert sandbox._workspace == "/custom/workdir"


# ---------------------------------------------------------------------------
# DockerSandbox.id
# ---------------------------------------------------------------------------


def test_id_returns_container_id(monkeypatch):
    module = _load_docker_module(monkeypatch)
    container = _FakeContainer(container_id="deadbeef01234567")
    sandbox = module.DockerSandbox(container)

    assert sandbox.id == "deadbeef01234567"


# ---------------------------------------------------------------------------
# DockerSandbox.execute
# ---------------------------------------------------------------------------


def test_execute_returns_stdout_and_exit_code(monkeypatch):
    module = _load_docker_module(monkeypatch)
    container = _FakeContainer(exec_result=_FakeExecResult(exit_code=0, output=b"hello\n"))
    sandbox = module.DockerSandbox(container)

    result = sandbox.execute("echo hello")

    assert result.output == "hello\n"
    assert result.exit_code == 0
    assert result.truncated is False


def test_execute_non_zero_exit_code_is_preserved(monkeypatch):
    module = _load_docker_module(monkeypatch)
    container = _FakeContainer(exec_result=_FakeExecResult(exit_code=1, output=b"error"))
    sandbox = module.DockerSandbox(container)

    result = sandbox.execute("false")

    assert result.exit_code == 1


def test_execute_none_output_becomes_empty_string(monkeypatch):
    module = _load_docker_module(monkeypatch)
    fake_result = _FakeExecResult(exit_code=0, output=None)  # type: ignore[arg-type]
    container = _FakeContainer(exec_result=fake_result)
    sandbox = module.DockerSandbox(container)

    result = sandbox.execute("true")

    assert result.output == ""


def test_execute_timeout_uses_timeout_binary_not_bash_prefix(monkeypatch):
    """timeout must wrap the full bash subprocess, not be prefixed inside bash -c.

    The correct form is ["timeout", "N", "bash", "-c", command] so that
    heredocs/pipes within `command` are parsed by the inner bash process.
    The incorrect form ("bash -c 'timeout N command'") would bind heredoc stdin
    to `timeout` rather than the embedded command, silently breaking
    BaseSandbox-generated edit/write scripts.
    """
    module = _load_docker_module(monkeypatch)
    container = _FakeContainer()
    sandbox = module.DockerSandbox(container)

    sandbox.execute("sleep 60", timeout=5)

    called_cmd = container.exec_run_calls[-1][0]
    # Must be ["timeout", "5", "bash", "-c", "sleep 60"]
    assert called_cmd[0] == "timeout", (
        f"Expected 'timeout' as argv[0] but got {called_cmd[0]!r}. "
        "Did you use the broken 'bash -c \"timeout N cmd\"' form?"
    )
    assert called_cmd[1] == "5"
    assert called_cmd[2] == "bash"
    assert called_cmd[3] == "-c"
    assert called_cmd[4] == "sleep 60"


def test_execute_timeout_heredoc_safe(monkeypatch):
    """Simulate a heredoc-bearing command (like BaseSandbox edit template).

    With the correct [timeout, N, bash, -c, cmd] form the heredoc markers are
    part of the script text passed to the inner bash, so they are interpreted
    correctly.  This test verifies the command structure is preserved exactly.
    """
    heredoc_command = "python3 -c \"import sys; print(sys.stdin.read())\" <<'EOF'\npayload\nEOF"
    module = _load_docker_module(monkeypatch)
    container = _FakeContainer()
    sandbox = module.DockerSandbox(container)

    sandbox.execute(heredoc_command, timeout=10)

    called_cmd = container.exec_run_calls[-1][0]
    # The heredoc string must be passed intact as argv[4] to inner bash
    assert called_cmd[4] == heredoc_command


def test_execute_without_timeout_does_not_add_timeout_prefix(monkeypatch):
    module = _load_docker_module(monkeypatch)
    container = _FakeContainer()
    sandbox = module.DockerSandbox(container)

    sandbox.execute("echo hi")

    called_cmd = container.exec_run_calls[-1][0]
    assert called_cmd[0] == "bash"
    assert not any("timeout" in part for part in called_cmd)


def test_execute_uses_workspace_as_workdir(monkeypatch):
    module = _load_docker_module(monkeypatch)
    container = _FakeContainer()
    sandbox = module.DockerSandbox(container, workspace="/my/workspace")

    sandbox.execute("ls")

    _, kwargs = container.exec_run_calls[-1]
    assert kwargs.get("workdir") == "/my/workspace"


# ---------------------------------------------------------------------------
# DockerSandbox.upload_files
# ---------------------------------------------------------------------------


def test_upload_files_returns_success_for_each_file(monkeypatch):
    module = _load_docker_module(monkeypatch)
    container = _FakeContainer()
    sandbox = module.DockerSandbox(container)

    results = sandbox.upload_files(
        [
            ("/workspace/a.txt", b"content-a"),
            ("/workspace/b.txt", b"content-b"),
        ]
    )

    assert len(results) == 2
    assert all(r.error is None for r in results)
    assert results[0].path == "/workspace/a.txt"
    assert results[1].path == "/workspace/b.txt"


def test_upload_files_calls_put_archive(monkeypatch):
    module = _load_docker_module(monkeypatch)
    container = _FakeContainer()
    sandbox = module.DockerSandbox(container)

    sandbox.upload_files([("/workspace/hello.py", b"print('hi')")])

    assert len(container.put_archive_calls) == 1
    dest_path, tar_data = container.put_archive_calls[0]
    assert dest_path == "/workspace"


def test_upload_files_creates_parent_directory(monkeypatch):
    module = _load_docker_module(monkeypatch)
    container = _FakeContainer()
    sandbox = module.DockerSandbox(container)

    sandbox.upload_files([("/workspace/sub/dir/file.txt", b"data")])

    mkdir_calls = [
        c
        for c, _ in container.exec_run_calls
        if isinstance(c, list) and any("mkdir" in part for part in c)
    ]
    assert len(mkdir_calls) >= 1


def test_upload_files_mkdir_failure_returns_error_without_calling_put_archive(monkeypatch):
    """If mkdir -p fails (e.g. permission denied), upload must surface the error
    and must NOT proceed to put_archive with a misleading tar error.
    """
    module = _load_docker_module(monkeypatch)
    container = _FakeContainer(
        exec_result=_FakeExecResult(exit_code=1, output=b"permission denied")
    )
    sandbox = module.DockerSandbox(container)

    results = sandbox.upload_files([("/readonly/file.txt", b"data")])

    assert len(results) == 1
    assert results[0].error is not None
    assert "mkdir" in results[0].error or "permission denied" in results[0].error
    # put_archive must never have been called
    assert len(container.put_archive_calls) == 0


def test_upload_files_handles_put_archive_exception(monkeypatch):
    module = _load_docker_module(monkeypatch)
    container = _FakeContainer()
    container.put_archive = MagicMock(side_effect=RuntimeError("docker daemon error"))
    sandbox = module.DockerSandbox(container)

    results = sandbox.upload_files([("/workspace/fail.txt", b"data")])

    assert len(results) == 1
    assert results[0].error is not None
    assert "docker daemon error" in results[0].error


def test_upload_files_tar_contains_correct_content(monkeypatch):
    """The tar archive written to put_archive must contain the uploaded bytes."""
    module = _load_docker_module(monkeypatch)
    container = _FakeContainer()
    sandbox = module.DockerSandbox(container)
    payload = b"hello world"

    sandbox.upload_files([("/workspace/test.txt", payload)])

    _, tar_stream = container.put_archive_calls[0]
    tar_stream.seek(0)
    with tarfile.open(fileobj=tar_stream) as tar:
        member = tar.getmember("test.txt")
        f = tar.extractfile(member)
        assert f is not None
        assert f.read() == payload


# ---------------------------------------------------------------------------
# DockerSandbox.download_files
# ---------------------------------------------------------------------------


def test_download_files_returns_content(monkeypatch):
    module = _load_docker_module(monkeypatch)
    container = _FakeContainer()
    sandbox = module.DockerSandbox(container)

    results = sandbox.download_files(["/workspace/file.txt"])

    assert len(results) == 1
    assert results[0].error is None
    assert results[0].content == b"file-content"


def test_download_files_handles_get_archive_exception(monkeypatch):
    module = _load_docker_module(monkeypatch)
    container = _FakeContainer()
    container.get_archive = MagicMock(side_effect=RuntimeError("file not found"))
    sandbox = module.DockerSandbox(container)

    results = sandbox.download_files(["/workspace/missing.txt"])

    assert len(results) == 1
    assert results[0].error is not None
    assert "file not found" in results[0].error


def test_download_files_multiple_paths(monkeypatch):
    module = _load_docker_module(monkeypatch)
    container = _FakeContainer()
    sandbox = module.DockerSandbox(container)

    results = sandbox.download_files(
        [
            "/workspace/a.txt",
            "/workspace/b.txt",
        ]
    )

    assert len(results) == 2
    assert all(r.error is None for r in results)


# ---------------------------------------------------------------------------
# Registry: SANDBOX_FACTORIES
# ---------------------------------------------------------------------------


def test_docker_registered_in_sandbox_factories(monkeypatch):
    """'docker' key must be present in SANDBOX_FACTORIES and point to the right module."""
    # We test the registry without loading docker.py (to avoid import side-effects).
    import importlib

    sandbox_mod = importlib.import_module("agent.utils.sandbox")
    factories = sandbox_mod.SANDBOX_FACTORIES

    assert "docker" in factories
    module_name, func_name = factories["docker"]
    assert module_name == "agent.integrations.docker"
    assert func_name == "create_docker_sandbox"
