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
    app = modal.App.lookup(MODAL_APP_NAME)

    if sandbox_id:
        sandbox = modal.Sandbox.from_id(sandbox_id, app=app)
    else:
        sandbox = modal.Sandbox.create(
            app=app,
            image=modal.Image.debian_slim(python_version="3.12").apt_install("git"),
        )

    return ModalSandbox(sandbox=sandbox)
