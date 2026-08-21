"""Local shell "sandbox" provider: the host machine, with no isolation."""

import asyncio
import os
from pathlib import Path

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import SandboxBackendProtocol

from ..config import local_sandbox_root_dir
from ..sandboxes.providers import SandboxProvider

SANDBOX_GITCONFIG = ".gitconfig-sandbox"


def _scoped_git_config_env(root_dir: str) -> dict[str, str]:
    """Point `git config --global` at a sandbox-local file.

    Local sandboxes run on the host, so the bot identity every run writes would
    otherwise overwrite the developer's own `~/.gitconfig` user.name/email. The
    scoped file includes the real one so credential helpers and aliases survive.
    """
    scoped = Path(root_dir) / SANDBOX_GITCONFIG
    if not scoped.exists():
        host = Path.home() / ".gitconfig"
        scoped.write_text(f"[include]\n\tpath = {host}\n" if host.exists() else "")
    return {"GIT_CONFIG_GLOBAL": str(scoped)}


class LocalProvider(SandboxProvider):
    """The host machine.

    WARNING: runs commands directly on the host with no sandboxing. Only for
    local development with human-in-the-loop enabled.

    There is nothing to connect to and nothing to boot: every call builds a
    backend over the same root directory, which is why a local sandbox is never
    gone and never snapshot-booted. The root defaults to the current working
    directory, can be overridden with ``LOCAL_SANDBOX_ROOT_DIR``, and is created
    if it does not exist.
    """

    async def connect(self, sandbox_id: str) -> SandboxBackendProtocol:
        return await asyncio.to_thread(self._backend)

    async def create(self, *, snapshot_id: str | None = None) -> SandboxBackendProtocol:
        if snapshot_id is not None:
            msg = f"The local sandbox is the host filesystem; it cannot boot {snapshot_id!r}"
            raise ValueError(msg)
        return await asyncio.to_thread(self._backend)

    async def work_dir(self, backend: SandboxBackendProtocol) -> str | None:
        if not isinstance(backend, LocalShellBackend):
            return None
        return str(backend.cwd)

    @staticmethod
    def _backend() -> LocalShellBackend:
        root_dir = local_sandbox_root_dir() or os.getcwd()
        os.makedirs(root_dir, exist_ok=True)

        # A process-level git setting, not app config: when the host already
        # scopes git's global file we leave it alone.
        env = {} if os.environ.get("GIT_CONFIG_GLOBAL") else _scoped_git_config_env(root_dir)

        return LocalShellBackend(
            root_dir=root_dir,
            virtual_mode=True,
            inherit_env=True,
            env=env,
        )
