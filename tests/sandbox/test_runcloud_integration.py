import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class _FakeExecResult:
    exit_code: int
    stdout: str
    stderr: str


class _FakeSandbox:
    def __init__(self, sandbox_id: str):
        self.id = sandbox_id
        self.exec_calls: list[tuple[str, int | None]] = []
        self.files: dict[str, bytes] = {}

    def exec(self, command: str, *, timeout: int | None = None):
        self.exec_calls.append((command, timeout))
        if command.startswith("mkdir -p "):
            return _FakeExecResult(exit_code=0, stdout="", stderr="")
        return _FakeExecResult(exit_code=7, stdout="stdout\n", stderr="stderr\n")

    def write_file(self, path: str, content: bytes):
        self.files[path] = content

    def read_file(self, path: str):
        return self.files[path]


class _FakeClient:
    instances: list["_FakeClient"] = []

    def __init__(self):
        self.create_calls: list[dict[str, object]] = []
        self.get_calls: list[str] = []
        self.sandbox: _FakeSandbox | None = None
        self.__class__.instances.append(self)

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        self.sandbox = _FakeSandbox("created-sandbox")
        return self.sandbox

    def get(self, sandbox_id: str):
        self.get_calls.append(sandbox_id)
        self.sandbox = _FakeSandbox(sandbox_id)
        return self.sandbox


def _load_runcloud_module(monkeypatch):
    _FakeClient.instances = []
    fake_runcloud = types.ModuleType("runcloud")
    fake_runcloud.__dict__.update({"Client": _FakeClient, "Sandbox": _FakeSandbox})
    monkeypatch.setitem(sys.modules, "runcloud", fake_runcloud)

    module_path = ROOT / "agent" / "integrations" / "runcloud.py"
    spec = importlib.util.spec_from_file_location("runcloud_under_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_create_run_cloud_sandbox_uses_default_image(monkeypatch):
    monkeypatch.delenv("RUN_CLOUD_IMAGE", raising=False)
    module = _load_runcloud_module(monkeypatch)

    backend = module.create_runcloud_sandbox()

    client = _FakeClient.instances[0]
    assert backend.id == "created-sandbox"
    assert client.create_calls == [{"image": "runcloud/agent-base"}]
    assert client.get_calls == []


def test_create_run_cloud_sandbox_uses_configured_image(monkeypatch):
    monkeypatch.setenv("RUN_CLOUD_IMAGE", "ghcr.io/acme/open-swe:latest")
    module = _load_runcloud_module(monkeypatch)

    module.create_runcloud_sandbox()

    assert _FakeClient.instances[0].create_calls == [{"image": "ghcr.io/acme/open-swe:latest"}]


def test_create_run_cloud_sandbox_reconnects_by_id(monkeypatch):
    module = _load_runcloud_module(monkeypatch)

    backend = module.create_runcloud_sandbox("sbx_existing")

    client = _FakeClient.instances[0]
    assert backend.id == "sbx_existing"
    assert client.get_calls == ["sbx_existing"]
    assert client.create_calls == []


def test_run_cloud_backend_executes_commands(monkeypatch):
    module = _load_runcloud_module(monkeypatch)
    backend = module.create_runcloud_sandbox()

    response = backend.execute("git status", timeout=42)

    assert response.output == "stdout\nstderr\n"
    assert response.exit_code == 7
    assert response.truncated is False
    assert backend._sandbox.exec_calls == [("git status", 42)]


def test_run_cloud_backend_uploads_and_downloads_files(monkeypatch):
    module = _load_runcloud_module(monkeypatch)
    backend = module.create_runcloud_sandbox()

    uploads = backend.upload_files(
        [
            ("/workspace/a.txt", b"alpha"),
            ("/workspace/dir/b.txt", b"beta"),
        ]
    )
    downloads = backend.download_files(
        ["/workspace/a.txt", "/workspace/dir/b.txt", "/workspace/missing.txt"]
    )

    assert [response.error for response in uploads] == [None, None]
    assert downloads[0].content == b"alpha"
    assert downloads[1].content == b"beta"
    assert downloads[2].error
    assert backend._sandbox.exec_calls == [
        ("mkdir -p /workspace", None),
        ("mkdir -p /workspace/dir", None),
    ]


def test_run_cloud_rejects_empty_image(monkeypatch):
    monkeypatch.setenv("RUN_CLOUD_IMAGE", "  ")
    module = _load_runcloud_module(monkeypatch)

    try:
        module.create_runcloud_sandbox()
    except ValueError as exc:
        assert "RUN_CLOUD_IMAGE must not be empty" in str(exc)
    else:
        raise AssertionError("expected empty Run Cloud image to fail")
