import os

from deepagents.backends import LocalShellBackend


def create_local_sandbox(sandbox_id: str | None = None):
    """Create a local shell sandbox with no isolation.

    WARNING: This runs commands directly on the host machine with no sandboxing.
    Only use for local development with human-in-the-loop enabled.

    The root directory defaults to the current working directory and can be
    overridden via the LOCAL_SANDBOX_ROOT_DIR environment variable.

    Args:
        sandbox_id: Ignored for local sandboxes; accepted for interface compatibility.

    Returns:
        LocalShellBackend instance implementing SandboxBackendProtocol.
    """
    root_dir = os.getenv("LOCAL_SANDBOX_ROOT_DIR", os.getcwd())

    return LocalShellBackend(
        root_dir=root_dir,
        inherit_env=True,
    )
