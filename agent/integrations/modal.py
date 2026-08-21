"""Modal sandbox provider."""

import modal
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_modal import ModalSandbox

from ..config import modal_app_name
from ..sandboxes.providers import SandboxGoneError, SandboxProvider


class ModalProvider(SandboxProvider):
    """Modal sandboxes.

    Cannot boot from an Open SWE snapshot: a Modal sandbox's filesystem comes
    from the image its app defines, not from a captured box.

    Says nothing about the work dir — Modal takes it from the image, so the
    sandbox's own shell is the only thing that knows it.
    """

    async def connect(self, sandbox_id: str) -> SandboxBackendProtocol:
        try:
            sandbox = await modal.Sandbox.from_id.aio(sandbox_id)
        except modal.exception.NotFoundError as e:
            msg = f"Failed to connect to existing sandbox '{sandbox_id}': {e}"
            raise SandboxGoneError(msg) from e
        return ModalSandbox(sandbox=sandbox)

    async def create(self, *, snapshot_id: str | None = None) -> SandboxBackendProtocol:
        if snapshot_id is not None:
            msg = (
                f"Modal cannot boot from snapshot {snapshot_id!r}; its filesystem comes from the "
                f"image of app {modal_app_name()!r}"
            )
            raise ValueError(msg)
        app = await modal.App.lookup.aio(modal_app_name())
        return ModalSandbox(sandbox=await modal.Sandbox.create.aio(app=app))
