import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class _FakeCommand:
    def __init__(self, *, output: str = "", exit_code: int = 0, cmd_id: str = "cmd-1"):
        self.output = output
        self.exit_code = exit_code
        self.cmd_id = cmd_id


class _FakeSandboxInstance:
    def __init__(
        self, sandbox_id: str = "sbx_created", status: str = "running", *, persistent: bool = True
    ):
        self.sandbox_id = sandbox_id
        self.status = status
        self.persistent = persistent
        self.commands: list[object] = []
        self.files: dict[str, str] = {}
        self.mkdirs: list[str] = []
        self.resumed = False
        self.killed: list[str] = []

    def resume(self):
        self.resumed = True
        self.status = "running"
        return self

    def mkdir(self, path: str) -> None:
        self.mkdirs.append(path)

    def run_command(self, opts):
        self.commands.append(opts)
        cmd = getattr(opts, "cmd", None)
        args = getattr(opts, "args", None) or []
        detached = bool(getattr(opts, "detached", False))
        # Satisfy workspace ensure / path probes used by the adapter.
        # Commands are typically `bash -lc "<shell script>"`.
        script = " ".join(map(str, args))
        joined = f"{cmd} {script}"
        if "test -d" in joined:
            return _FakeCommand(exit_code=1, cmd_id="cmd-dir")
        if "test -e" in joined:
            # Extract the path token after `test -e`.
            marker = "test -e "
            idx = script.find(marker)
            raw = script[idx + len(marker) :].strip() if idx >= 0 else ""
            key = raw.split()[0].strip("'\"") if raw else ""
            if key in self.files or key in self.mkdirs or key == "/workspace":
                return _FakeCommand(exit_code=0, cmd_id="cmd-exists")
            return _FakeCommand(exit_code=1, cmd_id="cmd-missing")
        # Bootstrap probe: pretend python3 + git are already present.
        if "command -v python3" in joined and "command -v git" in joined:
            return _FakeCommand(output="/usr/bin/python3\n/usr/bin/git\n", exit_code=0)
        if detached:
            return _FakeCommand(output="", exit_code=None, cmd_id="cmd-detached")  # type: ignore[arg-type]
        return _FakeCommand(output="ok\n", exit_code=0, cmd_id="cmd-sync")

    def wait_for_command(
        self, cmd_id: str, *, timeout: float | None = None, poll_interval: float = 0.5
    ):
        class _Status:
            output = "detached-done\n"
            exit_code = 0
            running = False

        return _Status()

    def kill_command(self, cmd_id: str) -> None:
        self.killed.append(cmd_id)

    def write_file(self, path: str, content: str) -> None:
        self.files[path] = content

    def read_file(self, path: str) -> str | None:
        return self.files.get(path)


class _FakeSandboxClient:
    created: list[dict] = []
    get_calls: list[str] = []
    instances: dict[str, _FakeSandboxInstance] = {}

    def __init__(self, config=None):
        self.config = config

    def create(self, **kwargs):
        self.__class__.created.append(kwargs)
        inst = _FakeSandboxInstance("sbx_created", status="running")
        self.__class__.instances[inst.sandbox_id] = inst
        return inst

    def get(self, sandbox_id: str):
        self.__class__.get_calls.append(sandbox_id)
        if sandbox_id in self.__class__.instances:
            return self.__class__.instances[sandbox_id]
        return _FakeSandboxInstance(sandbox_id, status="paused", persistent=True)


class _FakeSandboxConfig:
    def __init__(self, api_key=None, base_url=None):
        self.api_key = api_key
        self.base_url = base_url


class _FakeRunCommandOpts:
    def __init__(self, cmd, args=None, cwd=None, env=None, detached=None):
        self.cmd = cmd
        self.args = args
        self.cwd = cwd
        self.env = env
        self.detached = detached


def _load_lightning_module(monkeypatch):
    _FakeSandboxClient.created = []
    _FakeSandboxClient.get_calls = []
    _FakeSandboxClient.instances = {}

    fake_pkg = types.ModuleType("lightning_sdk")
    fake_sandbox_mod = types.ModuleType("lightning_sdk.sandbox")
    fake_sandbox_mod.Sandbox = _FakeSandboxClient
    fake_sandbox_mod.SandboxConfig = _FakeSandboxConfig
    fake_sandbox_mod.SandboxInstance = _FakeSandboxInstance
    fake_sandbox_mod.RunCommandOpts = _FakeRunCommandOpts

    monkeypatch.setitem(sys.modules, "lightning_sdk", fake_pkg)
    monkeypatch.setitem(sys.modules, "lightning_sdk.sandbox", fake_sandbox_mod)

    # deepagents is a real dep — leave it alone.
    module_path = ROOT / "agent" / "integrations" / "lightning.py"
    spec = importlib.util.spec_from_file_location("lightning_under_test", module_path)
    assert spec is not None and spec.loader is not None
    # Ensure a clean reload each test.
    sys.modules.pop("lightning_under_test", None)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_create_lightning_sandbox_requires_api_key(monkeypatch):
    monkeypatch.delenv("LIGHTNING_API_KEY", raising=False)
    monkeypatch.delenv("LIGHTNING_SANDBOX_API_KEY", raising=False)
    module = _load_lightning_module(monkeypatch)

    try:
        module.create_lightning_sandbox()
    except ValueError as exc:
        assert "LIGHTNING_API_KEY" in str(exc)
    else:
        raise AssertionError("expected missing API key to fail")


def test_create_lightning_sandbox_uses_defaults(monkeypatch):
    monkeypatch.setenv("LIGHTNING_API_KEY", "sk-lit-test")
    monkeypatch.delenv("LIGHTNING_SANDBOX_INSTANCE_TYPE", raising=False)
    monkeypatch.delenv("LIGHTNING_SANDBOX_TIMEOUT_MS", raising=False)
    module = _load_lightning_module(monkeypatch)

    backend = module.create_lightning_sandbox()

    assert backend.id == "sbx_created"
    assert backend.get_work_dir() == "/workspace"
    assert _FakeSandboxClient.created == [
        {
            "name": f"open-swe-{__import__('os').getpid()}",
            "instance_type": "cpu-1",
            "persistent": True,
            "network_policy": "allow-all",
        }
    ]
    # workspace ensure
    assert backend.sandbox.mkdirs == ["/workspace"] or any(
        getattr(c, "args", None) and "mkdir" in " ".join(map(str, getattr(c, "args", [])))
        for c in backend.sandbox.commands
    )


def test_create_lightning_sandbox_respects_env(monkeypatch):
    monkeypatch.setenv("LIGHTNING_SANDBOX_API_KEY", "sk-lit-sandbox")
    monkeypatch.setenv("LIGHTNING_SANDBOX_INSTANCE_TYPE", "cpu-4")
    monkeypatch.setenv("LIGHTNING_SANDBOX_TIMEOUT_MS", "600000")
    monkeypatch.setenv("LIGHTNING_SANDBOX_PERSISTENT", "false")
    monkeypatch.setenv("LIGHTNING_SANDBOX_NETWORK_POLICY", "deny-all")
    module = _load_lightning_module(monkeypatch)

    module.create_lightning_sandbox()

    assert _FakeSandboxClient.created == [
        {
            "name": f"open-swe-{__import__('os').getpid()}",
            "instance_type": "cpu-4",
            "persistent": False,
            "network_policy": "deny-all",
            "timeout": 600000,
        }
    ]


def test_create_lightning_sandbox_reconnects_and_resumes(monkeypatch):
    monkeypatch.setenv("LIGHTNING_API_KEY", "sk-lit-test")
    module = _load_lightning_module(monkeypatch)

    backend = module.create_lightning_sandbox("sbx_existing")

    assert backend.id == "sbx_existing"
    assert _FakeSandboxClient.get_calls == ["sbx_existing"]
    assert backend.sandbox.resumed is True
    assert _FakeSandboxClient.created == []


def test_lightning_execute_uses_detached_timeout(monkeypatch):
    monkeypatch.setenv("LIGHTNING_API_KEY", "sk-lit-test")
    module = _load_lightning_module(monkeypatch)
    backend = module.create_lightning_sandbox()

    result = backend.execute("echo hi", timeout=30)

    assert result.exit_code == 0
    assert "detached-done" in result.output
    detached_cmds = [c for c in backend.sandbox.commands if getattr(c, "detached", None)]
    assert detached_cmds
    assert detached_cmds[-1].cmd == "bash"
    assert detached_cmds[-1].args == ["-lc", "echo hi"]
    assert detached_cmds[-1].cwd == "/workspace"


def test_lightning_upload_download_text(monkeypatch):
    monkeypatch.setenv("LIGHTNING_API_KEY", "sk-lit-test")
    module = _load_lightning_module(monkeypatch)
    backend = module.create_lightning_sandbox()

    uploads = backend.upload_files([("/workspace/hello.txt", b"hello")])
    assert uploads[0].error is None
    assert backend.sandbox.files["/workspace/hello.txt"] == "hello"

    downloads = backend.download_files(["/workspace/hello.txt"])
    assert downloads[0].error is None
    assert downloads[0].content == b"hello"


def test_sandbox_factory_registers_lightning():
    from agent.utils.sandbox import SANDBOX_FACTORIES

    assert "lightning" in SANDBOX_FACTORIES
    module_name, fn_name = SANDBOX_FACTORIES["lightning"]
    assert module_name == "agent.integrations.lightning"
    assert fn_name == "create_lightning_sandbox"
