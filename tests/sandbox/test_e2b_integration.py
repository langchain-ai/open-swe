import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class _FakeSandbox:
    create_calls: list[dict[str, object]] = []
    connect_calls: list[tuple[str, dict[str, object]]] = []

    def __init__(self, sandbox_id: str):
        self.sandbox_id = sandbox_id

    @classmethod
    def create(cls, **kwargs):
        cls.create_calls.append(kwargs)
        return cls("created-sandbox")

    @classmethod
    def connect(cls, sandbox_id: str, **kwargs):
        cls.connect_calls.append((sandbox_id, kwargs))
        return cls(sandbox_id)


class _FakeE2BSandbox:
    def __init__(self, *, sandbox):
        self.sandbox = sandbox


def _load_e2b_module(monkeypatch):
    _FakeSandbox.create_calls = []
    _FakeSandbox.connect_calls = []

    fake_e2b = types.ModuleType("e2b")
    fake_e2b.__dict__["Sandbox"] = _FakeSandbox

    fake_langchain_e2b = types.ModuleType("langchain_e2b")
    fake_langchain_e2b.__dict__["E2BSandbox"] = _FakeE2BSandbox

    monkeypatch.setitem(sys.modules, "e2b", fake_e2b)
    monkeypatch.setitem(sys.modules, "langchain_e2b", fake_langchain_e2b)

    module_path = ROOT / "agent" / "integrations" / "e2b.py"
    spec = importlib.util.spec_from_file_location("e2b_under_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_create_e2b_sandbox_uses_default_timeout(monkeypatch):
    monkeypatch.setenv("E2B_API_KEY", "api-key")
    monkeypatch.delenv("E2B_TEMPLATE", raising=False)
    module = _load_e2b_module(monkeypatch)

    backend = module.create_e2b_sandbox()

    assert backend.sandbox.sandbox_id == "created-sandbox"
    assert _FakeSandbox.create_calls == [{"timeout": 3600, "api_key": "api-key"}]
    assert _FakeSandbox.connect_calls == []


def test_create_e2b_sandbox_uses_template(monkeypatch):
    monkeypatch.setenv("E2B_API_KEY", "api-key")
    monkeypatch.setenv("E2B_TEMPLATE", "open-swe-template")
    module = _load_e2b_module(monkeypatch)

    module.create_e2b_sandbox()

    assert _FakeSandbox.create_calls == [
        {"template": "open-swe-template", "timeout": 3600, "api_key": "api-key"}
    ]


def test_create_e2b_sandbox_reconnects_by_id(monkeypatch):
    monkeypatch.setenv("E2B_API_KEY", "api-key")
    module = _load_e2b_module(monkeypatch)

    backend = module.create_e2b_sandbox("sbx_existing")

    assert backend.sandbox.sandbox_id == "sbx_existing"
    assert _FakeSandbox.connect_calls == [("sbx_existing", {"timeout": 3600, "api_key": "api-key"})]
    assert _FakeSandbox.create_calls == []


def test_e2b_rejects_empty_template(monkeypatch):
    monkeypatch.setenv("E2B_API_KEY", "api-key")
    monkeypatch.setenv("E2B_TEMPLATE", "  ")
    module = _load_e2b_module(monkeypatch)

    try:
        module.create_e2b_sandbox()
    except ValueError as exc:
        assert "E2B_TEMPLATE must not be empty" in str(exc)
    else:
        raise AssertionError("expected empty E2B_TEMPLATE to fail")
