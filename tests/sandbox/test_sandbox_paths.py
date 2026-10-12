import shlex
from typing import cast

from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

from openswe.sandboxes.lifecycle import configure_git_identity
from openswe.sandboxes.paths import resolve_repo_dir, resolve_sandbox_work_dir


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


class _IdentityBackend(_FakeSandboxBackend):
    """Answers the git identity write, which also readies the provider's work dir."""

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if "git config --global" not in command:
            return await super().aexecute(command, timeout=timeout)
        self.commands.append(command)
        ready = "/workspace" in self.writable_dirs
        return ExecuteResponse(
            output="__open_swe_work_dir_ready__\n" if ready else "", exit_code=0, truncated=False
        )


async def test_the_identity_write_readies_the_work_dir_so_no_probe_follows() -> None:
    backend = _IdentityBackend(
        provider=_FakeProvider(work_dir="/workspace"), writable_dirs={"/workspace"}
    )

    await configure_git_identity(cast(SandboxBackendProtocol, backend))

    assert await resolve_sandbox_work_dir(cast(SandboxBackendProtocol, backend)) == "/workspace"
    assert len(backend.commands) == 1


async def test_a_work_dir_the_identity_write_could_not_ready_is_still_probed() -> None:
    backend = _IdentityBackend(
        provider=_FakeProvider(work_dir="/workspace", home_dir="/home/daytona"),
        shell_paths={"pwd": "/workspace", "printf '%s' \"$HOME\"": "/home/daytona"},
        writable_dirs={"/home/daytona"},
    )

    await configure_git_identity(cast(SandboxBackendProtocol, backend))

    assert await resolve_sandbox_work_dir(cast(SandboxBackendProtocol, backend)) == "/home/daytona"
