from typing import Any, cast

from langchain_runloop import RunloopSandbox
from runloop_api_client import Client

from ..config import runloop_api_key


def create_runloop_sandbox(sandbox_id: str | None = None):
    """Create or reconnect to a Runloop devbox sandbox.

    Requires the RUNLOOP_API_KEY environment variable to be set.

    Args:
        sandbox_id: Optional existing devbox ID to reconnect to.
            If None, creates a new devbox.

    Returns:
        RunloopSandbox instance implementing SandboxBackendProtocol.
    """
    api_key = runloop_api_key()
    if not api_key:
        raise ValueError("RUNLOOP_API_KEY environment variable is required")

    client = Client(bearer_token=api_key)

    if sandbox_id:
        devbox = client.devboxes.retrieve(sandbox_id)
    else:
        devbox = client.devboxes.create()

    return RunloopSandbox(devbox=cast(Any, devbox))
