import os
import re

from deepagents.backends import LocalShellBackend


class LocalShellBackendWrapper(LocalShellBackend):
    """Subclass of LocalShellBackend to sanitize commands for local execution."""

    def execute(self, command: str, *, timeout: int | None = None):
        if isinstance(command, str):
            # In a local sandbox, we run directly on the host machine.
            # There is no proxy to intercept "GH_TOKEN=dummy" and replace it with the real token.
            # On Windows, prepending inline env vars like "GH_TOKEN=dummy" causes command execution to fail.
            # On all platforms, using "dummy" token overrides valid local credentials.
            # Thus, we strip "GH_TOKEN=<value>" prefixes so that the host's actual authenticated credentials are used.
            command = re.sub(r"\bGH_TOKEN=\S+\s*", "", command)
        return super().execute(command, timeout=timeout)


def create_local_sandbox(sandbox_id: str | None = None):
    """Create a local shell sandbox with no isolation.

    WARNING: This runs commands directly on the host machine with no sandboxing.
    Only use for local development with human-in-the-loop enabled.

    The root directory defaults to the current working directory and can be
    overridden via the LOCAL_SANDBOX_ROOT_DIR environment variable.

    Args:
        sandbox_id: Ignored for local sandboxes; accepted for interface compatibility.

    Returns:
        LocalShellBackendWrapper instance implementing SandboxBackendProtocol.
    """
    root_dir = os.getenv("LOCAL_SANDBOX_ROOT_DIR", os.getcwd())

    return LocalShellBackendWrapper(
        root_dir=root_dir,
        inherit_env=True,
    )
