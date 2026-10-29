"""Reaching the deployment's sandboxes, which LangSmith runs.

Managed Deep Agents creates every thread's box; the backend only reconnects to one
by its id, so nothing here can create a sandbox.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol


class SandboxGoneError(RuntimeError):
    """The sandbox a thread is bound to no longer exists.

    Distinct from a sandbox that is merely unreachable: a deleted one holds no
    working tree, so callers hand the thread to MDA instead of failing it for good.
    """


async def connect_existing_sandbox(sandbox_id: str) -> SandboxBackendProtocol:
    """Reconnect to the LangSmith sandbox ``sandbox_id``."""
    from openswe.sandboxes.providers.langsmith import create_langsmith_sandbox

    return await create_langsmith_sandbox(sandbox_id)


def validate_sandbox_startup_config() -> None:
    """Validate the LangSmith sandbox configuration at server startup.

    Called from the FastAPI lifespan hook so errors surface at boot rather than on
    the first sandbox connection.
    """
    from openswe.sandboxes.providers.langsmith import LangSmithProvider

    LangSmithProvider.validate_startup_config()
