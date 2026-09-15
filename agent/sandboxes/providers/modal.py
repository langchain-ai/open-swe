import modal
from langchain_modal import ModalSandbox

from agent.config import ENV

MODAL_APP_NAME = ENV.MODAL_APP_NAME.get()


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
        app = await modal.App.lookup.aio(MODAL_APP_NAME)
        sandbox = await modal.Sandbox.create.aio(app=app)

    return ModalSandbox(sandbox=sandbox)
