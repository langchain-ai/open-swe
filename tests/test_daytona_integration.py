import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class _FakeCreateSandboxFromSnapshotParams:
    def __init__(self, *, snapshot: str):
        self.snapshot = snapshot


class _FakeDaytonaConfig:
    def __init__(self, *, api_key: str):
        self.api_key = api_key


class _FakeDaytonaSandbox:
    def __init__(self, *, sandbox):
        self.sandbox = sandbox


def _load_daytona_module(monkeypatch):
    fake_daytona = types.ModuleType("daytona")
    fake_daytona.CreateSandboxFromSnapshotParams = _FakeCreateSandboxFromSnapshotParams
    fake_daytona.DaytonaConfig = _FakeDaytonaConfig
    fake_daytona.Daytona = object

    fake_langchain_daytona = types.ModuleType("langchain_daytona")
    fake_langchain_daytona.DaytonaSandbox = _FakeDaytonaSandbox

    monkeypatch.setitem(sys.modules, "daytona", fake_daytona)
    monkeypatch.setitem(sys.modules, "langchain_daytona", fake_langchain_daytona)
    module_path = ROOT / "agent" / "integrations" / "daytona.py"
    spec = importlib.util.spec_from_file_location("daytona_under_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_daytona_params_default_to_existing_snapshot(monkeypatch):
    monkeypatch.delenv("DAYTONA_SANDBOX_SNAPSHOT", raising=False)
    module = _load_daytona_module(monkeypatch)

    params = module._get_daytona_sandbox_params()

    assert params.snapshot == "daytonaio/sandbox:0.6.0"


def test_daytona_params_use_env_snapshot(monkeypatch):
    monkeypatch.setenv("DAYTONA_SANDBOX_SNAPSHOT", "custom/snapshot:1.0")
    module = _load_daytona_module(monkeypatch)

    params = module._get_daytona_sandbox_params()

    assert params.snapshot == "custom/snapshot:1.0"


def test_daytona_params_reject_empty_snapshot(monkeypatch):
    monkeypatch.setenv("DAYTONA_SANDBOX_SNAPSHOT", "  ")
    module = _load_daytona_module(monkeypatch)

    try:
        module._get_daytona_sandbox_params()
    except ValueError as exc:
        assert "DAYTONA_SANDBOX_SNAPSHOT must not be empty" in str(exc)
    else:
        raise AssertionError("expected empty Daytona snapshot to fail")
