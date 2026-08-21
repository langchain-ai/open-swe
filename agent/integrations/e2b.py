from deepagents.backends.protocol import SandboxBackendProtocol
from e2b import Sandbox
from langchain_e2b import E2BSandbox

from ..config import e2b_api_key, e2b_template

DEFAULT_E2B_SANDBOX_TIMEOUT = 60 * 60


def create_e2b_sandbox(sandbox_id: str | None = None) -> SandboxBackendProtocol:
    """Create or reconnect to an E2B sandbox.

    Args:
        sandbox_id: Optional existing sandbox ID to reconnect to.
            If None, creates a new sandbox.

    Returns:
        E2BSandbox instance implementing SandboxBackendProtocol.
    """
    api_key = e2b_api_key()
    if not api_key:
        raise ValueError("E2B_API_KEY environment variable is required")

    template = e2b_template()

    if sandbox_id:
        sandbox = Sandbox.connect(
            sandbox_id,
            timeout=DEFAULT_E2B_SANDBOX_TIMEOUT,
            api_key=api_key,
        )
    elif template:
        sandbox = Sandbox.create(
            template=template,
            timeout=DEFAULT_E2B_SANDBOX_TIMEOUT,
            api_key=api_key,
        )
    else:
        sandbox = Sandbox.create(timeout=DEFAULT_E2B_SANDBOX_TIMEOUT, api_key=api_key)

    return E2BSandbox(sandbox=sandbox)
