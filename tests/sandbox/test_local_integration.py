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
    monkeypatch.setattr(local_mod, "OpenSWELocalShellBackend", _StubLocalShellBackend)

    backend = local_mod.create_local_sandbox()

    assert root.is_dir()
    stub = cast(_StubLocalShellBackend, backend)
    assert stub.root_dir == str(root)
    assert stub.virtual_mode is True
    assert stub.inherit_env is True


def test_create_local_sandbox_defaults_to_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("LOCAL_SANDBOX_ROOT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(local_mod, "OpenSWELocalShellBackend", _StubLocalShellBackend)

    backend = local_mod.create_local_sandbox()

    stub = cast(_StubLocalShellBackend, backend)
    assert stub.root_dir == str(tmp_path)
    assert stub.virtual_mode is True


def test_local_sandbox_file_tools_accept_the_real_work_dir(monkeypatch, tmp_path):
    root = tmp_path / "openswe-sandbox"
    monkeypatch.setenv("LOCAL_SANDBOX_ROOT_DIR", str(root))

    backend = local_mod.create_local_sandbox()
    result = backend.execute("mkdir -p repo && printf 'hello' > repo/README.md")

    assert result.exit_code == 0
    read_result = backend.read(f"{root}/repo/README.md")
    assert read_result.error is None
    assert read_result.file_data is not None
    assert read_result.file_data["content"] == "hello"


def test_local_sandbox_uses_host_auth_for_dummy_gh_token(monkeypatch, tmp_path):
    root = tmp_path / "openswe-sandbox"
    monkeypatch.setenv("LOCAL_SANDBOX_ROOT_DIR", str(root))
    commands = []

    def capture_execute(self, command, *, timeout=None):
        commands.append((command, timeout))
        return object()

    monkeypatch.setattr(local_mod.LocalShellBackend, "execute", capture_execute)
    backend = local_mod.create_local_sandbox()

    result = backend.execute("cd repo && GH_TOKEN=dummy gh repo view", timeout=30)

    assert result is not None
    assert commands == [("cd repo && env -u GH_TOKEN gh repo view", 30)]
