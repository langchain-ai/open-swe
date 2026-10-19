"""The sandbox an MDA run declares for its thread.

MDA adopts the box by name, starting it if it stopped, and creates it under that
name when there is none, with the options this backend would create it with. The
backend still owns it from there: it binds the thread to the box, configures its
proxy credentials and git identity, and replaces or recreates it. MDA leaves the
box's proxy config alone, so the backend's GitHub credentials and tool channel stay.
"""

from typing import Final

from pydantic import BaseModel, Field

from openswe.bridge.store import Bridge
from openswe.config import ENV
from openswe.sandboxes.lifecycle import SandboxCreateConfig, bind_created_sandbox
from openswe.sandboxes.providers.langsmith import LangSmithCreateOptions
from openswe.sandboxes.state import get_sandbox_metadata
from openswe.tasks.schemas import ThreadMetadata

_NAME_PREFIX: Final = "open-swe-"


class ManagedSandbox(BaseModel):
    """A thread's box, by the name MDA finds it under, and how to create it there."""

    name: str
    create: LangSmithCreateOptions
    thread_id: str = Field(exclude=True)
    workspace_slug: str | None = Field(default=None, exclude=True)

    @classmethod
    async def for_thread(cls, thread_id: str, workspace_slug: str | None) -> ManagedSandbox | None:
        """The box the thread's MDA run declares, or ``None`` when MDA holds none.

        A thread already bound to a box declares that box, however it was created.
        Bridged threads run on the user's machine, and a task worker shares its
        coordinator's box only once the backend has checked that it may.
        """
        if ENV.SANDBOX_TYPE.get() != "langsmith":
            return None
        metadata = ThreadMetadata.model_validate(await get_sandbox_metadata(thread_id))
        name = metadata.sandbox_id
        if name is None:
            if metadata.task_id is not None:
                return None
            name = f"{_NAME_PREFIX}{thread_id}"
        elif Bridge.bridge_id_of(name) is not None:
            return None
        config = await SandboxCreateConfig.resolve(workspace_slug)
        return cls(
            name=name,
            create=config.langsmith_options(),
            thread_id=thread_id,
            workspace_slug=workspace_slug,
        )

    async def bind(self) -> None:
        """Bind the thread to this box when it has none yet: MDA has just made it."""
        await bind_created_sandbox(self.thread_id, self.name, workspace_slug=self.workspace_slug)
