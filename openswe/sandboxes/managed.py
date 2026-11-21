"""Every thread's sandbox, which Managed Deep Agents owns.

MDA names, creates, adopts and restarts each thread's sandbox; one per thread,
kept whatever the options below become, which apply only when MDA creates it. Every run
with a thread works in one: its own, or a task worker's coordinator's. A sandbox the
backend made before MDA owned them becomes MDA's as the next run is planned
(``BackendSandbox``). The run opens the sandbox and reports its name, and the backend
binds the thread to it and connects as to any reused sandbox, writing its git identity.
It reports the sandbox before it prepares, or behind its preparation and first model call
where preparing reaches no sandbox (``bind_after_prepare``). MDA creates every sandbox but an
external one with the proxy config the backend works out here, and writes it there before
its commands: the same on every run, with no credential in it (``SandboxProxyCallback``).
The backend never creates or replaces a sandbox itself, and neither does MDA: a thread
whose sandbox LangSmith deleted cannot run again, a review's included.
"""

import logging
from collections.abc import Mapping, Sequence

import httpx2
from pydantic import BaseModel, Field, JsonValue, ValidationError

from openswe.config import ENV
from openswe.sandboxes.lifecycle import (
    BackendSandbox,
    SandboxCreateConfig,
    SandboxCredentialPolicy,
    TaskWorkerSandbox,
)
from openswe.sandboxes.providers.langsmith import LangSmithCreateOptions, LiveSandboxProxy
from openswe.sandboxes.proxy_callback import SandboxProxyCallback
from openswe.sandboxes.state import (
    SandboxDeletedError,
    SandboxUnreachableError,
    get_sandbox_metadata,
)
from openswe.sandboxes.tool_access import SANDBOX_FEDERATED_RULES_KEY
from openswe.slack.staged_files import slack_files_pending
from openswe.tasks.schemas import ThreadMetadata
from openswe.threads.store import THREADS

logger = logging.getLogger(__name__)


class FederatedRules(BaseModel):
    """The cloud identity rules a sandbox was created with, as its thread records them once
    read: LangSmith never changes them, and refuses a proxy config without exactly those
    (HTTP 422). Beside them, the environment the same read set on the sandbox
    (``LiveSandboxProxy.adopt``), which follows the backend's public origin."""

    sandbox_id: str
    rules: list[dict[str, JsonValue]]
    env_vars: dict[str, str] = {}

    @classmethod
    async def of(
        cls,
        thread_id: str,
        metadata: Mapping[str, object],
        sandbox_id: str,
        *,
        env_vars: Mapping[str, str],
    ) -> list[dict[str, JsonValue]] | None:
        """The rules ``thread_id``'s live sandbox ``sandbox_id`` was created with: as recorded
        in the thread's ``metadata``, or read from the sandbox and recorded; None where nothing
        can read them, which keeps the base config's.

        The read also sets the sandbox's own environment, which one created before it came
        from there lacks, so it is made again whenever that environment changes; one it could
        not set is not recorded, and the next run reads again."""
        try:
            recorded = cls.model_validate(metadata.get(SANDBOX_FEDERATED_RULES_KEY))
        except ValidationError:
            logger.debug("Could not validate recorded sandbox cloud identity rules", exc_info=True)
            recorded = None
        env_vars = dict(env_vars)
        if (
            recorded is not None
            and recorded.sandbox_id == sandbox_id
            and recorded.env_vars == env_vars
        ):
            return recorded.rules
        adopted = await LiveSandboxProxy.adopt(sandbox_id, env_vars=env_vars)
        if adopted is None:
            return None
        if adopted.env_vars is not None:
            read = cls(sandbox_id=sandbox_id, rules=adopted.rules, env_vars=adopted.env_vars)
            await THREADS.update(
                thread_id, metadata={SANDBOX_FEDERATED_RULES_KEY: read.model_dump(mode="json")}
            )
        return adopted.rules


class ManagedSandbox(BaseModel):
    """What MDA creates a thread's sandbox with, and whose sandbox the thread uses."""

    create: LangSmithCreateOptions
    sandbox_id: str | None = None
    # The thread whose sandbox this one works in, a task worker's coordinator; None for its own.
    thread_id: str | None = None
    # Whether the run may prepare before the sandbox is open and report it afterwards
    # (``bind_sandbox``): preparing it reaches no sandbox, and the deployment allows it
    # (``MDA_BIND_SANDBOX_AFTER_PREPARE``).
    bind_after_prepare: bool = False
    # Externally created LangSmith sandbox (evals): adopted without setup, proxy writes, or replacement.
    external: bool = False
    # The sandbox's proxy config, which MDA creates it with and writes before its commands;
    # None, for an external sandbox, leaves its own alone.
    proxy_config: dict[str, JsonValue] | None = None
    # Its commands' environment (``SandboxProxyCallback.env_vars``), set in its own run config;
    # sent apart from ``create``, whose shape a run's execution profile pins.
    env_vars: dict[str, str] = Field(default_factory=dict, exclude=True)

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
        env_vars = SandboxProxyCallback.env_vars(
            github=await SandboxCredentialPolicy.reaches_github(sandbox_thread, sandbox_metadata)
        )
        if not isinstance(sandbox_id, str):
            # MDA creates it with this config, the workspace's cloud identity rules in it.
            return cls(
                create=config.langsmith_options(),
                thread_id=owner,
                bind_after_prepare=bind_after_prepare,
                proxy_config=SandboxProxyCallback.proxy_config(config.proxy_config),
                env_vars=env_vars,
            )
        try:
            federated = await FederatedRules.of(
                sandbox_thread, sandbox_metadata, sandbox_id, env_vars=env_vars
            )
        except Exception as exc:
            if isinstance(exc, httpx2.HTTPStatusError) and exc.response.status_code == 404:
                raise SandboxDeletedError(
                    sandbox_thread,
                    sandbox_id,
                    "LangSmith can't find this thread's sandbox",
                    reason="LangSmith answered 404 for the sandbox",
                ) from exc
            # LangSmith refuses a proxy config without the exact cloud identity rules the
            # sandbox was created with (HTTP 422), so the run can't be given one: it ends here.
            raise SandboxUnreachableError(
                sandbox_thread,
                sandbox_id,
                "could not read its cloud identity rules",
                reason="LangSmith did not return the sandbox's cloud identity rules",
            ) from exc
        proxy_config = SandboxProxyCallback.proxy_config(
            config.proxy_config, federated_rules=federated
        )
        return cls(
            create=await config.recreation_options(),
            thread_id=owner,
            sandbox_id=sandbox_id,
            bind_after_prepare=bind_after_prepare,
            proxy_config=proxy_config,
            env_vars=env_vars,
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
