from __future__ import annotations

import shlex

from deepagents.backends.protocol import ExecuteResponse

from agent.utils.sandbox_paths import (
    aresolve_repo_dir,
    resolve_repo_dir,
    resolve_sandbox_work_dir,
)


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
    ) -> None:
        self.sandbox = provider
        self.shell_paths = shell_paths or {}
        self.writable_dirs = writable_dirs or set()
        self.commands: list[str] = []

    @property
    def id(self) -> str:
        return "fake-sandbox"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
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

        return ExecuteResponse(output="", exit_code=1, truncated=False)


def test_resolve_repo_dir_uses_provider_work_dir() -> None:
    backend = _FakeSandboxBackend(
        provider=_FakeProvider(work_dir="/workspace"),
        writable_dirs={"/workspace"},
    )

    repo_dir = resolve_repo_dir(backend, "open-swe")

    assert repo_dir == "/workspace/open-swe"
    assert backend.commands == ["test -d /workspace && test -w /workspace"]


def test_resolve_sandbox_work_dir_falls_back_to_home_when_work_dir_is_not_writable() -> None:
    backend = _FakeSandboxBackend(
        provider=_FakeProvider(work_dir="/workspace", home_dir="/home/daytona"),
        shell_paths={
            "pwd": "/workspace",
            "printf '%s' \"$HOME\"": "/home/daytona",
        },
        writable_dirs={"/home/daytona"},
    )

    work_dir = resolve_sandbox_work_dir(backend)

    assert work_dir == "/home/daytona"
    assert backend.commands == [
        "test -d /workspace && test -w /workspace",
        "pwd",
        "test -d /home/daytona && test -w /home/daytona",
    ]


def test_resolve_sandbox_work_dir_caches_the_result() -> None:
    backend = _FakeSandboxBackend(
        provider=_FakeProvider(work_dir="/workspace"),
        writable_dirs={"/workspace"},
    )

    first = resolve_sandbox_work_dir(backend)
    second = resolve_sandbox_work_dir(backend)

    assert first == "/workspace"
    assert second == "/workspace"
    assert backend.commands == ["test -d /workspace && test -w /workspace"]


async def test_aresolve_repo_dir_offloads_sync_resolution() -> None:
    backend = _FakeSandboxBackend(
        provider=_FakeProvider(work_dir="/home/daytona"),
        writable_dirs={"/home/daytona"},
    )

    repo_dir = await aresolve_repo_dir(backend, "open-swe")

    assert repo_dir == "/home/daytona/open-swe"
    assert backend.commands == ["test -d /home/daytona && test -w /home/daytona"]
