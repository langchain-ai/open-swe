import modal
from langchain_modal import ModalSandbox

from ..config import modal_app_name


async def create_modal_sandbox(sandbox_id: str | None = None):
    """Create or reconnect to a Modal sandbox.

    Args:
        sandbox_id: Optional existing sandbox ID to reconnect to.
            If None, creates a new sandbox.

    Returns:
        ModalSandbox instance implementing SandboxBackendProtocol.
    """
    if sandbox_id:
        sandbox = await modal.Sandbox.from_id.aio(sandbox_id)
    else:
        app = await modal.App.lookup.aio(modal_app_name())
        sandbox = await modal.Sandbox.create.aio(app=app)

    return ModalSandbox(sandbox=sandbox)
