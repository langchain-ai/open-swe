"""Local shell "sandbox" provider: the host machine, with no isolation."""

import asyncio
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import SandboxBackendProtocol

from ..config import local_sandbox_root_dir
from ..sandboxes.providers import SandboxProvider, SandboxResources

SANDBOX_GITCONFIG = ".gitconfig-sandbox"
LOCAL_SHELL_ENV_EXCLUDE = {
    "ANTHROPIC_API_KEY",
    "FIREWORKS_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "LANGSMITH_API_KEY",
    "OPEN_SWE_OPENAI_OAUTH_ACCOUNT_FILE",
    "OPEN_SWE_OPENAI_OAUTH_BROKER_TOKEN",
    "OPEN_SWE_OPENAI_OAUTH_BROKER_URL",
    "OPENAI_API_KEY",
}


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

    async def create(
        self,
        *,
        snapshot_id: str | None = None,
        resources: SandboxResources | None = None,
        create_params: Mapping[str, Any] | None = None,
    ) -> SandboxBackendProtocol:
        if snapshot_id is not None:
            msg = f"The local sandbox is the host filesystem; it cannot boot {snapshot_id!r}"
            raise ValueError(msg)
        self._reject_sizing("Local", resources, create_params)
        return await asyncio.to_thread(self._backend)

    async def work_dir(self, backend: SandboxBackendProtocol) -> str | None:
        if not isinstance(backend, LocalShellBackend):
            return None
        return str(backend.cwd)

    @staticmethod
    def _backend() -> LocalShellBackend:
        root_dir = local_sandbox_root_dir() or os.getcwd()
        os.makedirs(root_dir, exist_ok=True)

        # The host's own process environment, minus the server's credentials:
        # the agent runs shell commands here and must not be able to read them.
        env = {
            key: value for key, value in os.environ.items() if key not in LOCAL_SHELL_ENV_EXCLUDE
        }
        # A process-level git setting, not app config: when the host already
        # scopes git's global file we leave it alone.
        if not os.environ.get("GIT_CONFIG_GLOBAL"):
            env.update(_scoped_git_config_env(root_dir))

        return LocalShellBackend(
            root_dir=root_dir,
            virtual_mode=True,
            inherit_env=False,
            env=env,
        )
