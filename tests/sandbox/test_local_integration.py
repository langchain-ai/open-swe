from typing import cast

import agent.integrations.local as local_mod


class _StubLocalShellBackend:
    def __init__(self, *, root_dir, virtual_mode, inherit_env):
        self.root_dir = root_dir
        self.virtual_mode = virtual_mode
        self.inherit_env = inherit_env


def test_create_local_sandbox_creates_missing_root_dir(monkeypatch, tmp_path):
    root = tmp_path / "nested" / "openswe-sandbox"
    monkeypatch.setenv("LOCAL_SANDBOX_ROOT_DIR", str(root))
    monkeypatch.setattr(local_mod, "LocalShellBackend", _StubLocalShellBackend)

    backend = local_mod.create_local_sandbox()

    assert root.is_dir()
    stub = cast(_StubLocalShellBackend, backend)
    assert stub.root_dir == str(root)
    assert stub.virtual_mode is True
    assert stub.inherit_env is True


def test_create_local_sandbox_defaults_to_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("LOCAL_SANDBOX_ROOT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(local_mod, "LocalShellBackend", _StubLocalShellBackend)

    backend = local_mod.create_local_sandbox()

    stub = cast(_StubLocalShellBackend, backend)
    assert stub.root_dir == str(tmp_path)
    assert stub.virtual_mode is True
