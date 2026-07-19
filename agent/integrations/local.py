import os
import re
from pathlib import Path

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse

_DUMMY_GH_TOKEN = re.compile(r"(?<![A-Za-z0-9_])GH_TOKEN=dummy(?=\s+gh(?:\s|$))")


class OpenSWELocalShellBackend(LocalShellBackend):
    """Keep shell-reported host paths usable by virtual file tools."""

    def _resolve_path(self, key: str) -> Path:
        path = Path(key)
        if path.is_absolute():
            try:
                relative_path = path.resolve().relative_to(self.cwd)
            except ValueError:
                pass
            else:
                return super()._resolve_path(str(relative_path))
        return super()._resolve_path(key)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        command = _DUMMY_GH_TOKEN.sub("env -u GH_TOKEN", command)
        return super().execute(command, timeout=timeout)


def create_local_sandbox(sandbox_id: str | None = None):
    """Create a local shell sandbox with no isolation.

    WARNING: This runs commands directly on the host machine with no sandboxing.
    Only use for local development with human-in-the-loop enabled.

    The root directory defaults to the current working directory and can be
    overridden via the LOCAL_SANDBOX_ROOT_DIR environment variable. It is
    created if it does not already exist.

    Args:
        sandbox_id: Ignored for local sandboxes; accepted for interface compatibility.

    Returns:
        LocalShellBackend instance implementing SandboxBackendProtocol.
    """
    root_dir = os.getenv("LOCAL_SANDBOX_ROOT_DIR", os.getcwd())
    os.makedirs(root_dir, exist_ok=True)

    return OpenSWELocalShellBackend(
        root_dir=root_dir,
        virtual_mode=True,
        inherit_env=True,
    )
