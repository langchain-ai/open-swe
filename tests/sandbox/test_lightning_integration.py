import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class _FakeSandboxInstance:
    def __init__(
        self, sandbox_id: str = "sbx_created", status: str = "running", *, persistent: bool = True
    ):
        self.sandbox_id = sandbox_id
        self.status = status
        self.persistent = persistent
        self.resumed = False
        self.mkdirs: list[str] = []

    def resume(self):
        self.resumed = True
        self.status = "running"
        return self

    def mkdir(self, path: str) -> None:
        self.mkdirs.append(path)


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


class _FakeLightningSandbox:
    def __init__(self, *, sandbox, workdir="/workspace", timeout=1800):
        self.sandbox = sandbox
        self._workdir = workdir
        self._timeout = timeout

    @property
    def id(self) -> str:
        return self.sandbox.sandbox_id

    def get_work_dir(self) -> str:
        return self._workdir


def _load_lightning_module(monkeypatch):
    _FakeSandboxClient.created = []
    _FakeSandboxClient.get_calls = []
    _FakeSandboxClient.instances = {}

    fake_sdk = types.ModuleType("lightning_sdk")
    fake_sdk_sandbox = types.ModuleType("lightning_sdk.sandbox")
    fake_sdk_sandbox.Sandbox = _FakeSandboxClient
    fake_sdk_sandbox.SandboxConfig = _FakeSandboxConfig

    def resume_if_needed(sandbox):
        status = (sandbox.status or "").lower()
        if status in {"paused", "stopped", "idle"} or getattr(sandbox, "persistent", False):
            if status not in {"running", "ready"}:
                return sandbox.resume()
        return sandbox

    def ensure_workdir(sandbox, workdir):
        sandbox.mkdir(workdir)

    fake_lc = types.ModuleType("langchain_lightning")
    fake_lc.LightningSandbox = _FakeLightningSandbox
    fake_lc.ensure_workdir = ensure_workdir
    fake_lc.resume_if_needed = resume_if_needed

    monkeypatch.setitem(sys.modules, "lightning_sdk", fake_sdk)
    monkeypatch.setitem(sys.modules, "lightning_sdk.sandbox", fake_sdk_sandbox)
    monkeypatch.setitem(sys.modules, "langchain_lightning", fake_lc)

    module_path = ROOT / "agent" / "integrations" / "lightning.py"
    sys.modules.pop("lightning_under_test", None)
    spec = importlib.util.spec_from_file_location("lightning_under_test", module_path)
    assert spec is not None and spec.loader is not None
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


def test_create_lightning_sandbox_defaults_to_python313(monkeypatch):
    monkeypatch.setenv("LIGHTNING_API_KEY", "sk-lit-test")
    monkeypatch.delenv("LIGHTNING_SANDBOX_RUNTIME", raising=False)
    monkeypatch.delenv("LIGHTNING_SANDBOX_IMAGE", raising=False)
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
            "runtime": "python313",
        }
    ]
    assert backend.sandbox.mkdirs == ["/workspace"]


def test_create_lightning_sandbox_respects_env(monkeypatch):
    monkeypatch.setenv("LIGHTNING_SANDBOX_API_KEY", "sk-lit-sandbox")
    monkeypatch.setenv("LIGHTNING_SANDBOX_INSTANCE_TYPE", "cpu-4")
    monkeypatch.setenv("LIGHTNING_SANDBOX_TIMEOUT_MS", "600000")
    monkeypatch.setenv("LIGHTNING_SANDBOX_PERSISTENT", "false")
    monkeypatch.setenv("LIGHTNING_SANDBOX_NETWORK_POLICY", "deny-all")
    monkeypatch.setenv("LIGHTNING_SANDBOX_RUNTIME", "python313")
    module = _load_lightning_module(monkeypatch)

    module.create_lightning_sandbox()

    assert _FakeSandboxClient.created == [
        {
            "name": f"open-swe-{__import__('os').getpid()}",
            "instance_type": "cpu-4",
            "persistent": False,
            "network_policy": "deny-all",
            "timeout": 600000,
            "runtime": "python313",
        }
    ]


def test_create_lightning_sandbox_uses_image_without_runtime(monkeypatch):
    monkeypatch.setenv("LIGHTNING_API_KEY", "sk-lit-test")
    monkeypatch.setenv("LIGHTNING_SANDBOX_IMAGE", "ghcr.io/example/dev:latest")
    monkeypatch.delenv("LIGHTNING_SANDBOX_RUNTIME", raising=False)
    module = _load_lightning_module(monkeypatch)

    module.create_lightning_sandbox()

    created = _FakeSandboxClient.created[0]
    assert created["image"] == "ghcr.io/example/dev:latest"
    assert "runtime" not in created


def test_create_lightning_sandbox_reconnects_and_resumes(monkeypatch):
    monkeypatch.setenv("LIGHTNING_API_KEY", "sk-lit-test")
    module = _load_lightning_module(monkeypatch)

    backend = module.create_lightning_sandbox("sbx_existing")

    assert backend.id == "sbx_existing"
    assert _FakeSandboxClient.get_calls == ["sbx_existing"]
    assert backend.sandbox.resumed is True
    assert _FakeSandboxClient.created == []


def test_sandbox_factory_registers_lightning():
    from agent.utils.sandbox import SANDBOX_FACTORIES

    assert "lightning" in SANDBOX_FACTORIES
    module_name, fn_name = SANDBOX_FACTORIES["lightning"]
    assert module_name == "agent.integrations.lightning"
    assert fn_name == "create_lightning_sandbox"
