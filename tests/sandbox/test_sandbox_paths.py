import os
import shlex
from typing import cast

import pytest
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

from agent.sandboxes.paths import resolve_repo_dir, resolve_sandbox_work_dir


class _FakeProvider:
    def __init__(self, work_dir: str | None = None, home_dir: str | None = None) -> None:
        self._work_dir = work_dir
        self._home_dir = home_dir

    def get_work_dir(self) -> str:
        if self._work_dir is None:
            raise RuntimeError("work dir unavailable")
        return self._work_dir

    def get_user_home_dir(self) -> str:
        if self._home_dir is None:
            raise RuntimeError("home dir unavailable")
        return self._home_dir


class _FakeSandboxBackend:
    def __init__(
        self,
        *,
        provider: _FakeProvider | None = None,
        shell_paths: dict[str, str] | None = None,
        writable_dirs: set[str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self.sandbox = provider
        self.shell_paths = shell_paths or {}
        self.writable_dirs = writable_dirs or set()
        self.commands: list[str] = []
        if cwd is not None:
            self.cwd = cwd

    @property
    def id(self) -> str:
        return "fake-sandbox"

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        del timeout
        self.commands.append(command)

        if command in self.shell_paths:
            return ExecuteResponse(
                output=self.shell_paths[command],
                exit_code=0,
                truncated=False,
            )

        if command.startswith("test -d "):
            path = shlex.split(command)[2]
            exit_code = 0 if path in self.writable_dirs else 1
            return ExecuteResponse(output="", exit_code=exit_code, truncated=False)

        if command.startswith("echo. > "):
            # The Windows writability probe: echo. > "<dir>\.open-swe-write-probe" && del ...
            directory = command.split('"')[1].rsplit("\\", 1)[0]
            exit_code = 0 if directory in self.writable_dirs else 1
            return ExecuteResponse(output="", exit_code=exit_code, truncated=False)

        return ExecuteResponse(output="", exit_code=1, truncated=False)


async def test_resolve_repo_dir_uses_provider_work_dir() -> None:
    backend = _FakeSandboxBackend(
        provider=_FakeProvider(work_dir="/workspace"),
        writable_dirs={"/workspace"},
    )

    repo_dir = await resolve_repo_dir(cast(SandboxBackendProtocol, backend), "open-swe")

    assert repo_dir == "/workspace/open-swe"
    assert backend.commands == ["test -d /workspace && test -w /workspace"]


async def test_resolve_sandbox_work_dir_falls_back_to_home_when_work_dir_is_not_writable() -> None:
    backend = _FakeSandboxBackend(
        provider=_FakeProvider(work_dir="/workspace", home_dir="/home/daytona"),
        shell_paths={
            "pwd": "/workspace",
            "printf '%s' \"$HOME\"": "/home/daytona",
        },
        writable_dirs={"/home/daytona"},
    )

    work_dir = await resolve_sandbox_work_dir(cast(SandboxBackendProtocol, backend))

    assert work_dir == "/home/daytona"
    assert backend.commands == [
        "test -d /workspace && test -w /workspace",
        "pwd",
        "test -d /home/daytona && test -w /home/daytona",
    ]


async def test_resolve_sandbox_work_dir_caches_the_result() -> None:
    backend = _FakeSandboxBackend(
        provider=_FakeProvider(work_dir="/workspace"),
        writable_dirs={"/workspace"},
    )

    first = await resolve_sandbox_work_dir(cast(SandboxBackendProtocol, backend))
    second = await resolve_sandbox_work_dir(cast(SandboxBackendProtocol, backend))

    assert first == "/workspace"
    assert second == "/workspace"
    assert backend.commands == ["test -d /workspace && test -w /workspace"]


async def test_resolve_repo_dir_resolves_home_dir() -> None:
    backend = _FakeSandboxBackend(
        provider=_FakeProvider(work_dir="/home/daytona"),
        writable_dirs={"/home/daytona"},
    )

    repo_dir = await resolve_repo_dir(cast(SandboxBackendProtocol, backend), "open-swe")

    assert repo_dir == "/home/daytona/open-swe"
    assert backend.commands == ["test -d /home/daytona && test -w /home/daytona"]


async def test_backend_cwd_is_the_first_candidate() -> None:
    backend = _FakeSandboxBackend(
        cwd="/workspace/from-cwd",
        provider=_FakeProvider(work_dir="/workspace"),
        writable_dirs={"/workspace/from-cwd", "/workspace"},
    )

    work_dir = await resolve_sandbox_work_dir(cast(SandboxBackendProtocol, backend))

    assert work_dir == "/workspace/from-cwd"


async def test_windows_work_dir_resolves_through_the_backend_cwd() -> None:
    backend = _FakeSandboxBackend(
        cwd="D:\\agent\\open-swe\\.eval-sandbox",
        writable_dirs={"D:\\agent\\open-swe\\.eval-sandbox"},
    )

    work_dir = await resolve_sandbox_work_dir(cast(SandboxBackendProtocol, backend))

    # Windows absolute paths must survive normalization, and the probe must use
    # a cmd-compatible command rather than POSIX `test`.
    assert work_dir == os.path.normpath("D:\\agent\\open-swe\\.eval-sandbox")
    assert backend.commands == [
        f'echo. > "{work_dir}\\.open-swe-write-probe" && del "{work_dir}\\.open-swe-write-probe"'
    ]


async def test_unwritable_windows_work_dir_raises() -> None:
    backend = _FakeSandboxBackend(
        cwd="D:\\agent\\open-swe\\.eval-sandbox",
        writable_dirs=set(),
    )

    with pytest.raises(RuntimeError, match="Failed to resolve a writable sandbox work directory"):
        await resolve_sandbox_work_dir(cast(SandboxBackendProtocol, backend))
