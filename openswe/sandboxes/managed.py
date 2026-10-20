"""Threads whose sandbox Managed Deep Agents owns.

MDA names, creates, adopts, restarts and replaces each such thread's box; one per
thread, kept whatever the options below become, which apply only when MDA creates
it. Before preparing, the run opens the box and reports its name, and the backend
binds the thread to it and connects as to any reused box: proxy credentials, git
identity, tool channel. MDA leaves the box's proxy config to the backend. The
backend never creates or replaces an MDA-owned box itself.
"""

from pydantic import BaseModel

from openswe.bridge.store import Bridge
from openswe.config import ENV
from openswe.sandboxes.lifecycle import SandboxCreateConfig
from openswe.sandboxes.providers.langsmith import LangSmithCreateOptions
from openswe.sandboxes.state import get_sandbox_metadata
from openswe.tasks.schemas import ThreadMetadata


class ManagedSandbox(BaseModel):
    """What MDA creates a thread's box with, and whose box the thread uses."""

    create: LangSmithCreateOptions
    # The thread whose box this one works in, a task worker's coordinator; None for its own.
    thread_id: str | None = None

    @classmethod
    async def for_thread(cls, thread_id: str, workspace_slug: str | None) -> ManagedSandbox | None:
        """The box the thread's MDA run declares, or ``None`` when the backend keeps its own.

        New threads and MDA's own get MDA's box. A thread the backend gave a box keeps
        it, and bridged threads run on the user's machine. A task worker shares its
        coordinator's MDA box once the backend has attached it there, which checks
        that it may.
        """
        if ENV.SANDBOX_TYPE.get() != "langsmith":
            return None
        metadata = ThreadMetadata.model_validate(await get_sandbox_metadata(thread_id))
        if metadata.sandbox_id is not None and Bridge.bridge_id_of(metadata.sandbox_id):
            return None
        owner: str | None = None
        if metadata.task_id is not None:
            owner = metadata.sandbox_host_thread_id
            if owner is None or metadata.sandbox_id is None:
                return None
            host = ThreadMetadata.model_validate(await get_sandbox_metadata(owner))
            if host.sandbox_owner != "mda" or host.sandbox_id != metadata.sandbox_id:
                return None
        elif metadata.sandbox_id is not None and metadata.sandbox_owner != "mda":
            return None
        config = await SandboxCreateConfig.resolve(workspace_slug)
        return cls(create=config.langsmith_options(), thread_id=owner)
