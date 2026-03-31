import os

from agent.integrations.daytona import create_daytona_sandbox
from agent.integrations.langsmith import create_langsmith_sandbox
from agent.integrations.local import create_local_sandbox
from agent.integrations.modal import create_modal_sandbox
from agent.integrations.opensandbox import create_opensandbox_sandbox
from agent.integrations.runloop import create_runloop_sandbox

SANDBOX_FACTORIES = {
    "langsmith": create_langsmith_sandbox,
    "daytona": create_daytona_sandbox,
    "modal": create_modal_sandbox,
    "runloop": create_runloop_sandbox,
    "opensandbox": create_opensandbox_sandbox,
    "local": create_local_sandbox,
}


def create_sandbox(sandbox_id: str | None = None):
    """Create or reconnect to a sandbox using the configured provider.

    The provider is selected via the SANDBOX_TYPE environment variable.
    Supported values: langsmith (default), daytona, modal, runloop, opensandbox, local.

    Args:
        sandbox_id: Optional existing sandbox ID to reconnect to.

    Returns:
        A sandbox backend implementing SandboxBackendProtocol.
    """
    sandbox_type = os.getenv("SANDBOX_TYPE", "langsmith")
    factory = SANDBOX_FACTORIES.get(sandbox_type)
    if not factory:
        supported = ", ".join(sorted(SANDBOX_FACTORIES))
        raise ValueError(f"Invalid sandbox type: {sandbox_type}. Supported types: {supported}")
    return factory(sandbox_id)
