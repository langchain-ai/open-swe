import shlex
from collections.abc import Callable, Mapping
from typing import Any, cast

import pytest
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol
from support.sandbox_fakes import FakeSandboxBackend

from agent.sandboxes.paths import aresolve_repo_dir, aresolve_sandbox_work_dir
from agent.sandboxes.providers import SandboxProvider, SandboxResources
from agent.sandboxes.proxy import SandboxBackendProxy


class _FakeProvider(SandboxProvider):
    """A provider that knows one work dir, or nothing, or fails when asked."""

    def __init__(self, work_dir: str | None = None, *, fails: bool = False) -> None:
        self._work_dir = work_dir
        self._fails = fails
        self.asked_for: list[SandboxBackendProtocol] = []

    async def connect(self, sandbox_id: str) -> SandboxBackendProtocol:
        raise AssertionError("work-dir resolution must not connect")

    async def create(
        self,
        *,
        snapshot_id: str | None = None,
        resources: SandboxResources | None = None,
        create_params: Mapping[str, Any] | None = None,
    ) -> SandboxBackendProtocol:
        raise AssertionError("work-dir resolution must not create")

    async def work_dir(self, backend: SandboxBackendProtocol) -> str | None:
        self.asked_for.append(backend)
        if self._fails:
            raise RuntimeError("work dir unavailable")
        return self._work_dir


InstallProvider = Callable[[_FakeProvider], _FakeProvider]


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> InstallProvider:
    def install(fake: _FakeProvider) -> _FakeProvider:
        monkeypatch.setattr("agent.sandboxes.paths.current_sandbox_provider", lambda: fake)
        return fake

    return install


def _backend(
    *,
    shell_paths: dict[str, str] | None = None,
    writable_dirs: set[str] | None = None,
) -> FakeSandboxBackend:
    """A backend where ``test -d <path>`` succeeds only for ``writable_dirs``."""
    writable = writable_dirs or set()

    def respond(command: str) -> ExecuteResponse | None:
        if not command.startswith("test -d "):
            return None
        exit_code = 0 if shlex.split(command)[2] in writable else 1
        return ExecuteResponse(output="", exit_code=exit_code, truncated=False)

    return FakeSandboxBackend(
        "fake-sandbox",
        respond=respond,
        results=shell_paths or {},
        default=ExecuteResponse(output="", exit_code=1, truncated=False),
    )


async def test_resolve_repo_dir_uses_the_provider_work_dir(provider: InstallProvider) -> None:
    provider(_FakeProvider("/workspace"))
    backend = _backend(writable_dirs={"/workspace"})

    repo_dir = await aresolve_repo_dir(cast(SandboxBackendProtocol, backend), "open-swe")

    assert repo_dir == "/workspace/open-swe"
    assert backend.commands == ["test -d /workspace && test -w /workspace"]


async def test_resolve_work_dir_falls_back_to_the_shell_when_the_provider_declines(
    provider: InstallProvider,
) -> None:
    provider(_FakeProvider(None))
    backend = _backend(shell_paths={"pwd": "/home/user"}, writable_dirs={"/home/user"})

    work_dir = await aresolve_sandbox_work_dir(cast(SandboxBackendProtocol, backend))

    assert work_dir == "/home/user"
    assert backend.commands == ["pwd", "test -d /home/user && test -w /home/user"]


async def test_resolve_work_dir_falls_back_to_the_shell_when_the_provider_raises(
    provider: InstallProvider,
) -> None:
    provider(_FakeProvider(fails=True))
    backend = _backend(shell_paths={"pwd": "/home/user"}, writable_dirs={"/home/user"})

    work_dir = await aresolve_sandbox_work_dir(cast(SandboxBackendProtocol, backend))

    assert work_dir == "/home/user"


async def test_resolve_work_dir_falls_back_to_home_when_provider_dir_is_not_writable(
    provider: InstallProvider,
) -> None:
    provider(_FakeProvider("/workspace"))
    backend = _backend(
        shell_paths={
            "pwd": "/workspace",
            "printf '%s' \"$HOME\"": "/home/daytona",
        },
        writable_dirs={"/home/daytona"},
    )

    work_dir = await aresolve_sandbox_work_dir(cast(SandboxBackendProtocol, backend))

    assert work_dir == "/home/daytona"
    assert backend.commands == [
        "test -d /workspace && test -w /workspace",
        "pwd",
        "printf '%s' \"$HOME\"",
        "test -d /home/daytona && test -w /home/daytona",
    ]


async def test_resolve_work_dir_reports_every_candidate_it_rejected(
    provider: InstallProvider,
) -> None:
    provider(_FakeProvider("/workspace"))
    backend = _backend(shell_paths={"printf '%s' \"$HOME\"": "/home/user"})

    with pytest.raises(RuntimeError, match="Candidates checked: /workspace, /home/user"):
        await aresolve_sandbox_work_dir(cast(SandboxBackendProtocol, backend))


async def test_resolve_work_dir_asks_the_provider_about_the_real_backend(
    provider: InstallProvider,
) -> None:
    # The prober used to be handed the thread's handle, whose backend is private,
    # so every provider answered "no idea" and every sandbox resolved by shell.
    fake = provider(_FakeProvider("/workspace"))
    backend = _backend(writable_dirs={"/workspace"})
    proxy = SandboxBackendProxy(cast(SandboxBackendProtocol, backend), thread_id="thread-1")

    assert await aresolve_sandbox_work_dir(proxy) == "/workspace"
    assert fake.asked_for == [backend]


async def test_resolve_work_dir_caches_on_the_thread_handle(provider: InstallProvider) -> None:
    provider(_FakeProvider("/workspace"))
    backend = _backend(writable_dirs={"/workspace"})
    proxy = SandboxBackendProxy(cast(SandboxBackendProtocol, backend), thread_id="thread-1")

    first = await aresolve_sandbox_work_dir(proxy)
    second = await aresolve_sandbox_work_dir(proxy)

    assert (first, second) == ("/workspace", "/workspace")
    assert backend.commands == ["test -d /workspace && test -w /workspace"]


async def test_a_replacement_backend_does_not_inherit_the_cached_work_dir(
    provider: InstallProvider,
) -> None:
    provider(_FakeProvider("/workspace"))
    proxy = SandboxBackendProxy(
        cast(SandboxBackendProtocol, _backend(writable_dirs={"/workspace"})),
        thread_id="thread-1",
    )
    assert await aresolve_sandbox_work_dir(proxy) == "/workspace"

    replacement = _backend(
        shell_paths={"printf '%s' \"$HOME\"": "/home/user"}, writable_dirs={"/home/user"}
    )
    proxy.replace_backend(cast(SandboxBackendProtocol, replacement))

    assert await aresolve_sandbox_work_dir(proxy) == "/home/user"
