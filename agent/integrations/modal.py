import os

import modal
from langchain_modal import ModalSandbox

MODAL_APP_NAME = os.getenv("MODAL_APP_NAME", "open-swe")


def create_modal_sandbox(sandbox_id: str | None = None):
    """Create or reconnect to a Modal sandbox.

    Args:
        sandbox_id: Optional existing sandbox ID to reconnect to.
            If None, creates a new sandbox.

    Returns:
        ModalSandbox instance implementing SandboxBackendProtocol.
    """
    if sandbox_id:
        sandbox = modal.Sandbox.from_id(sandbox_id)
    else:
        app = modal.App.lookup(MODAL_APP_NAME)
        sandbox = modal.Sandbox.create(app=app)

    return ModalSandbox(sandbox=sandbox)
