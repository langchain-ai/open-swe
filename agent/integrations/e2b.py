import os

from deepagents.backends.protocol import SandboxBackendProtocol
from e2b import Sandbox
from langchain_e2b import E2BSandbox

DEFAULT_E2B_SANDBOX_TIMEOUT = 60 * 60
E2B_TEMPLATE_ENV = "E2B_TEMPLATE"


def create_e2b_sandbox(sandbox_id: str | None = None) -> SandboxBackendProtocol:
    """Create or reconnect to an E2B sandbox.

    Args:
        sandbox_id: Optional existing sandbox ID to reconnect to.
            If None, creates a new sandbox.

    Returns:
        E2BSandbox instance implementing SandboxBackendProtocol.
    """
    api_key = os.getenv("E2B_API_KEY")
    if not api_key:
        raise ValueError("E2B_API_KEY environment variable is required")

    template = os.getenv(E2B_TEMPLATE_ENV)
    if template is not None:
        template = template.strip()
        if not template:
            raise ValueError(f"{E2B_TEMPLATE_ENV} must not be empty")

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
