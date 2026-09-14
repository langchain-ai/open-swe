from typing import cast

import agent.sandboxes.providers.local as local_mod


class _StubLocalShellBackend:
    def __init__(self, *, root_dir, virtual_mode, inherit_env, env=None):
        self.root_dir = root_dir
        self.virtual_mode = virtual_mode
        self.inherit_env = inherit_env
        self.env = env or {}


def test_create_local_sandbox_creates_missing_root_dir(monkeypatch, tmp_path):
    root = tmp_path / "nested" / "openswe-sandbox"
    monkeypatch.setenv("LOCAL_SANDBOX_ROOT_DIR", str(root))
    monkeypatch.setattr(local_mod, "LocalShellBackend", _StubLocalShellBackend)

    backend = local_mod.create_local_sandbox()

    assert root.is_dir()
    stub = cast(_StubLocalShellBackend, backend)
    assert stub.root_dir == str(root)
    assert stub.virtual_mode is True
    assert stub.inherit_env is False


def test_create_local_sandbox_defaults_to_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("LOCAL_SANDBOX_ROOT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(local_mod, "LocalShellBackend", _StubLocalShellBackend)

    backend = local_mod.create_local_sandbox()

    stub = cast(_StubLocalShellBackend, backend)
    assert stub.root_dir == str(tmp_path)
    assert stub.virtual_mode is True


def test_create_local_sandbox_scopes_global_git_config(monkeypatch, tmp_path):
    root = tmp_path / "work"
    monkeypatch.setenv("LOCAL_SANDBOX_ROOT_DIR", str(root))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    host_config = tmp_path / "home" / ".gitconfig"
    host_config.parent.mkdir()
    host_config.write_text("[user]\n\tname = Dev\n")
    monkeypatch.setattr(local_mod, "LocalShellBackend", _StubLocalShellBackend)

    backend = local_mod.create_local_sandbox()

    scoped = root / local_mod.SANDBOX_GITCONFIG
    stub = cast(_StubLocalShellBackend, backend)
    assert stub.env["GIT_CONFIG_GLOBAL"] == str(scoped)
    assert str(host_config) in scoped.read_text()
    assert host_config.read_text() == "[user]\n\tname = Dev\n"


def test_create_local_sandbox_keeps_explicit_global_git_config(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_SANDBOX_ROOT_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "chosen-gitconfig"))
    monkeypatch.setattr(local_mod, "LocalShellBackend", _StubLocalShellBackend)

    backend = local_mod.create_local_sandbox()

    stub = cast(_StubLocalShellBackend, backend)
    assert stub.env["GIT_CONFIG_GLOBAL"] == str(tmp_path / "chosen-gitconfig")
    assert not (tmp_path / "work" / local_mod.SANDBOX_GITCONFIG).exists()


def test_create_local_sandbox_excludes_credential_broker_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_SANDBOX_ROOT_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("OPEN_SWE_OPENAI_OAUTH_BROKER_URL", "http://127.0.0.1:3210/token")
    monkeypatch.setenv("OPEN_SWE_OPENAI_OAUTH_BROKER_TOKEN", "broker-secret")
    monkeypatch.setenv("OPEN_SWE_OPENAI_OAUTH_ACCOUNT_FILE", "/tmp/legacy-account.json")
    monkeypatch.setenv("OPENAI_API_KEY", "provider-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "provider-secret")
    monkeypatch.setenv("VISIBLE_TO_AGENT", "visible")
    monkeypatch.setattr(local_mod, "LocalShellBackend", _StubLocalShellBackend)

    backend = local_mod.create_local_sandbox()

    stub = cast(_StubLocalShellBackend, backend)
    assert stub.env["VISIBLE_TO_AGENT"] == "visible"
    assert "OPEN_SWE_OPENAI_OAUTH_BROKER_URL" not in stub.env
    assert "OPEN_SWE_OPENAI_OAUTH_BROKER_TOKEN" not in stub.env
    assert "OPEN_SWE_OPENAI_OAUTH_ACCOUNT_FILE" not in stub.env
    assert "OPENAI_API_KEY" not in stub.env
    assert "ANTHROPIC_API_KEY" not in stub.env
