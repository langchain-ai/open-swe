"""Threads whose sandbox Managed Deep Agents owns.

MDA names, creates, adopts, restarts and replaces each such thread's sandbox; one per
thread, kept whatever the options below become, which apply only when MDA creates
it. Before preparing, the run opens the sandbox and reports its name, and the backend
binds the thread to it and connects as to any reused sandbox: proxy credentials, git
identity, tool channel. The backend writes the sandbox's proxy config, unless MDA does
(``github_proxy``). The backend never creates or replaces an MDA-owned sandbox itself.
"""

from collections.abc import Sequence

from pydantic import BaseModel
from runtime_api.connections import ConnectionRef

from openswe.bridge.store import Bridge
from openswe.github.app import PermissionMap
from openswe.sandboxes.lifecycle import (
    SandboxCreateConfig,
    ensure_sandbox_for_thread,
    hand_gone_sandbox_to_mda,
    mda_github_proxy,
)
from openswe.sandboxes.providers.langsmith import LangSmithCreateOptions
from openswe.sandboxes.state import SandboxUnreachableError, get_sandbox_metadata
from openswe.tasks.schemas import ThreadMetadata


class ManagedSandbox(BaseModel):
    """What MDA creates a thread's sandbox with, whose sandbox the thread uses, and who writes
    its proxy config."""

    create: LangSmithCreateOptions
    sandbox_id: str | None = None
    # The thread whose sandbox this one works in, a task worker's coordinator; None for its own.
    thread_id: str | None = None
    # The connection MDA fills the sandbox's GitHub proxy from, as it writes all of the sandbox's
    # proxy config; None where the backend writes it.
    github_proxy: ConnectionRef | None = None

    @classmethod
    async def for_thread(
        cls,
        thread_id: str,
        workspace_slug: str | None,
        *,
        github_repositories: Sequence[str] | None = None,
        github_permissions: PermissionMap | None = None,
    ) -> ManagedSandbox | None:
        """Resolve the thread's sandbox through its validated coordinator binding for workers."""
        raw = await get_sandbox_metadata(thread_id)
        metadata = ThreadMetadata.model_validate(raw)
        sandbox_metadata = raw
        owner: str | None = None
        if metadata.task_id is not None:
            await ensure_sandbox_for_thread(
                thread_id,
                workspace_slug=workspace_slug,
                github_proxy_repositories=github_repositories,
            )
            raw = await get_sandbox_metadata(thread_id)
            metadata = ThreadMetadata.model_validate(raw)
            owner = metadata.sandbox_host_thread_id
            if owner is None or metadata.sandbox_id is None:
                raise SandboxUnreachableError(
                    thread_id, metadata.sandbox_id, "Worker is unattached"
                )
            sandbox_metadata = await get_sandbox_metadata(owner)
            host = ThreadMetadata.model_validate(sandbox_metadata)
            if host.sandbox_id != metadata.sandbox_id:
                raise SandboxUnreachableError(
                    thread_id, metadata.sandbox_id, "Worker's coordinator sandbox binding changed"
                )
            if Bridge.bridge_id_of(metadata.sandbox_id):
                return None
            if host.sandbox_owner != "mda":
                raise SandboxUnreachableError(
                    thread_id, metadata.sandbox_id, "Worker's coordinator sandbox is not MDA-owned"
                )
        elif metadata.sandbox_id is not None and Bridge.bridge_id_of(metadata.sandbox_id):
            return None
        elif (
            metadata.sandbox_id is not None
            and metadata.sandbox_owner != "mda"
            and not await hand_gone_sandbox_to_mda(thread_id, metadata.sandbox_id)
        ):
            return None
        config = await SandboxCreateConfig.for_thread(raw, workspace_slug)
        return cls(
            create=config.langsmith_options(),
            thread_id=owner,
            sandbox_id=metadata.sandbox_id if owner or metadata.sandbox_owner == "mda" else None,
            # The sandbox's own thread decides: a worker's coordinator.
            github_proxy=await mda_github_proxy(
                owner or thread_id, sandbox_metadata, github_repositories, github_permissions
            ),
        )
