"""Every thread's sandbox, which Managed Deep Agents owns.

MDA names, creates, adopts and restarts each thread's sandbox; one per thread,
kept whatever the options below become, which apply only when MDA creates it. Every run
with a thread works in one: its own, or a task worker's coordinator's. A sandbox the
backend made before MDA owned them becomes MDA's as the next run is planned
(``BackendSandbox``). The run opens the sandbox and reports its name, and the backend
binds the thread to it and connects as to any reused sandbox: proxy credentials, git
identity, tool channel. It reports the sandbox before it prepares, or behind its
preparation and first model call where preparing reaches no sandbox
(``bind_after_prepare``). The backend writes the sandbox's proxy config, unless MDA does
(``writes_proxy``), from what the backend hands it. The backend never creates or replaces a
sandbox itself, and neither does MDA: a thread whose sandbox LangSmith deleted cannot run
again, a review's included.
"""

from collections.abc import Sequence

from pydantic import BaseModel

from openswe.config import ENV
from openswe.sandboxes.lifecycle import (
    BackendSandbox,
    SandboxCreateConfig,
    TaskWorkerSandbox,
    mda_writes_proxy,
)
from openswe.sandboxes.providers.langsmith import LangSmithCreateOptions
from openswe.sandboxes.state import SandboxUnreachableError, get_sandbox_metadata
from openswe.slack.staged_files import slack_files_pending
from openswe.tasks.schemas import ThreadMetadata


class ManagedSandbox(BaseModel):
    """What MDA creates a thread's sandbox with, whose sandbox the thread uses, and who writes
    its proxy config."""

    create: LangSmithCreateOptions
    sandbox_id: str | None = None
    # The thread whose sandbox this one works in, a task worker's coordinator; None for its own.
    thread_id: str | None = None
    # Whether MDA writes the sandbox's whole proxy config, as the backend hands it over
    # (``sandbox_proxy``), and the backend none of it.
    writes_proxy: bool = False
    # Whether the run may prepare before the sandbox is open and report it afterwards
    # (``bind_sandbox``): preparing it reaches no sandbox, and the deployment allows it
    # (``MDA_BIND_SANDBOX_AFTER_PREPARE``).
    bind_after_prepare: bool = False
    # Externally created LangSmith sandbox (evals): adopted without setup, proxy writes, or replacement.
    external: bool = False

    @classmethod
    async def for_thread(
        cls,
        thread_id: str,
        *,
        github_repositories: Sequence[str] | None = None,
        prepare_reaches_sandbox: bool = True,
    ) -> ManagedSandbox:
        """The sandbox the thread's MDA run declares.

        A new thread gets MDA's sandbox, and keeps it: nothing replaces one that is gone.
        A task worker shares its coordinator's, once the backend has
        checked that it may: the one the coordinator is bound to, or, before it has one, the
        one MDA opens for the coordinator's run. ``github_repositories`` narrows the run's
        GitHub token beyond its thread's own scope, as a review's does, which a worker's
        coordinator must reach. ``prepare_reaches_sandbox`` says preparing a turn of the run's kind
        works in the sandbox, as a review's does; the thread says so too where files a Slack
        message brought wait to be written there, and a task worker's attaches to its
        coordinator's.
        """
        raw = await get_sandbox_metadata(thread_id)
        metadata = ThreadMetadata.model_validate(raw)
        # The thread whose sandbox this is: a worker's coordinator.
        owner = (
            await TaskWorkerSandbox(thread_id, metadata).host(github_repositories)
            if metadata.task_id is not None
            else None
        )
        sandbox_thread = owner or thread_id
        host_metadata = raw if owner is None else await get_sandbox_metadata(owner)
        bind_after_prepare = not (
            prepare_reaches_sandbox or owner is not None or slack_files_pending(raw)
        ) and ENV.MDA_BIND_SANDBOX_AFTER_PREPARE.get_bool(default=True)
        if host_metadata.get("sandbox_owner") == "external":
            # A sandbox an eval harness attached: adopted as it is, never set up, written
            # to or replaced, and never handed to MDA's own.
            external_id = host_metadata.get("sandbox_id")
            if not isinstance(external_id, str):
                raise SandboxUnreachableError(
                    sandbox_thread, None, "This thread's external sandbox is not bound"
                )
            return cls(
                create=LangSmithCreateOptions.resolve(),
                thread_id=owner,
                sandbox_id=external_id,
                bind_after_prepare=bind_after_prepare,
                external=True,
            )
        sandbox_metadata = await cls._owned_by_mda(sandbox_thread, host_metadata)
        sandbox_id = sandbox_metadata.get("sandbox_id")
        config = await SandboxCreateConfig.for_thread(sandbox_metadata, thread_id=sandbox_thread)
        return cls(
            create=await config.recreation_options()
            if isinstance(sandbox_id, str)
            else config.langsmith_options(),
            thread_id=owner,
            sandbox_id=sandbox_id if isinstance(sandbox_id, str) else None,
            writes_proxy=mda_writes_proxy(),
            bind_after_prepare=bind_after_prepare,
        )

    @staticmethod
    async def _owned_by_mda(thread_id: str, metadata: dict[str, object]) -> dict[str, object]:
        """``thread_id``'s ``metadata`` once MDA owns its sandbox: adopted, or let go for a fresh
        one, where the backend made it."""
        sandbox_id = metadata.get("sandbox_id")
        if not isinstance(sandbox_id, str) or metadata.get("sandbox_owner") == "mda":
            return metadata
        adopted = await BackendSandbox(thread_id, sandbox_id).hand_to_mda()
        return {
            **metadata,
            "sandbox_id": adopted,
            **({"sandbox_owner": "mda"} if adopted is not None else {}),
        }
