from typing import cast

import pytest

import agent.integrations.local as local_mod
from agent.integrations.local import LocalProvider


class _StubLocalShellBackend:
    def __init__(self, *, root_dir, virtual_mode, inherit_env, env=None):  # noqa: ANN001, ANN204
        self.root_dir = root_dir
        self.cwd = root_dir
        self.virtual_mode = virtual_mode
        self.inherit_env = inherit_env
        self.env = env or {}


@pytest.fixture
def stub_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_mod, "LocalShellBackend", _StubLocalShellBackend)


async def test_create_creates_a_missing_root_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path, stub_backend: None
) -> None:
    root = tmp_path / "nested" / "openswe-sandbox"
    monkeypatch.setenv("LOCAL_SANDBOX_ROOT_DIR", str(root))

    backend = await LocalProvider().create()

    assert root.is_dir()
    stub = cast(_StubLocalShellBackend, backend)
    assert stub.root_dir == str(root)
    assert stub.virtual_mode is True
    assert stub.inherit_env is True


async def test_create_defaults_to_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path, stub_backend: None
) -> None:
    monkeypatch.delenv("LOCAL_SANDBOX_ROOT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    backend = await LocalProvider().create()

    stub = cast(_StubLocalShellBackend, backend)
    assert stub.root_dir == str(tmp_path)
    assert stub.virtual_mode is True


async def test_create_scopes_global_git_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path, stub_backend: None
) -> None:
    root = tmp_path / "work"
    monkeypatch.setenv("LOCAL_SANDBOX_ROOT_DIR", str(root))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    host_config = tmp_path / "home" / ".gitconfig"
    host_config.parent.mkdir()
    host_config.write_text("[user]\n\tname = Dev\n")

    backend = await LocalProvider().create()

    scoped = root / local_mod.SANDBOX_GITCONFIG
    stub = cast(_StubLocalShellBackend, backend)
    assert stub.env["GIT_CONFIG_GLOBAL"] == str(scoped)
    assert str(host_config) in scoped.read_text()
    assert host_config.read_text() == "[user]\n\tname = Dev\n"


async def test_create_keeps_an_explicit_global_git_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path, stub_backend: None
) -> None:
    monkeypatch.setenv("LOCAL_SANDBOX_ROOT_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "chosen-gitconfig"))

    backend = await LocalProvider().create()

    stub = cast(_StubLocalShellBackend, backend)
    assert "GIT_CONFIG_GLOBAL" not in stub.env
    assert not (tmp_path / "work" / local_mod.SANDBOX_GITCONFIG).exists()


async def test_connect_returns_the_same_host_root_and_is_never_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path, stub_backend: None
) -> None:
    # The host filesystem does not disappear, so a local sandbox id that no
    # longer resolves to anything is not an error the lifecycle has to recover
    # from: connecting hands back the same root every time.
    monkeypatch.setenv("LOCAL_SANDBOX_ROOT_DIR", str(tmp_path / "work"))

    backend = await LocalProvider().connect("whatever-id")

    assert cast(_StubLocalShellBackend, backend).root_dir == str(tmp_path / "work")


async def test_create_refuses_an_open_swe_snapshot() -> None:
    with pytest.raises(ValueError, match="it cannot boot 'snap-1'"):
        await LocalProvider().create(snapshot_id="snap-1")


async def test_work_dir_is_the_backend_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path, stub_backend: None
) -> None:
    monkeypatch.setenv("LOCAL_SANDBOX_ROOT_DIR", str(tmp_path / "work"))
    backend = await LocalProvider().create()

    assert await LocalProvider().work_dir(backend) == str(tmp_path / "work")
