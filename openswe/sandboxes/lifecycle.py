"""Connecting to the sandbox bound to a thread.

Managed Deep Agents creates and replaces every thread's sandbox, and writes its proxy
config; the backend never creates one, and works that config out as it plans each run
(``ManagedSandbox``).
This module connects to the sandbox a thread is bound to, writes its git identity, and
hands MDA a sandbox the backend made before MDA owned them (``BackendSandbox``): MDA adopts
it, or starts the thread in a fresh one where it is gone. The registry lives in ``state``.
"""

import asyncio
import logging
import shlex
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from deepagents.backends.protocol import SandboxBackendProtocol
from langgraph_sdk import get_client
from pydantic import JsonValue
from runtime_api.sandbox_setup import GitIdentity

from openswe.dashboard.execution_defaults import EXECUTION_DEFAULTS, ExecutionDefaults
from openswe.execution import SANDBOX_EXECUTION, ExecutionProfile
from openswe.github.app import PermissionMap
from openswe.github.sandbox_access import (
    READ_ONLY_REVIEW_PERMISSIONS,
    SandboxGitHubAccess,
    sandbox_token,
)
from openswe.review.findings import REVIEWER_SHADOW_THREAD_KIND, REVIEWER_THREAD_KIND
from openswe.sandboxes.configuration import SandboxResources
from openswe.sandboxes.paths import declared_work_dir, remember_work_dir
from openswe.sandboxes.providers.langsmith import (
    LangSmithCreateOptions,
    get_sandbox_proxy_config,
)
from openswe.sandboxes.providers.registry import SandboxGoneError, connect_existing_sandbox
from openswe.sandboxes.state import (
    SANDBOX_BACKENDS,
    SANDBOX_CONNECTIONS,
    SandboxBackendProxy,
    SandboxDeletedError,
    SandboxHandedOverError,
    SandboxUnreachableError,
    get_or_create_sandbox_backend_proxy,
    get_sandbox_metadata,
    narrowed_repositories,
    set_sandbox_backend,
    thread_token_repositories,
    unwrap_sandbox_backend,
)
from openswe.sandboxes.tool_access import SANDBOX_PROXY_CONFIG_METADATA_KEY
from openswe.tasks.schemas import ThreadMetadata
from openswe.tasks.store import TaskMembership
from openswe.threads.index import ThreadSearch
from openswe.threads.store import THREADS
from openswe.utils.json_types import JsonObject
from openswe.utils.startup_trace import aphase

logger = logging.getLogger(__name__)

client = get_client()


@dataclass(frozen=True, slots=True)
class SandboxCredentialPolicy:
    """The persisted sandbox owner's credential boundary, independent of the current caller."""

    metadata: ThreadMetadata

    async def allows_github(self) -> bool:
        if self.metadata.sandbox_owner == "external":
            return False
        if self.metadata.task_id is not None and self.metadata.sandbox_host_thread_id:
            host = await get_sandbox_metadata(self.metadata.sandbox_host_thread_id)
            if host.get("sandbox_owner") == "external":
                return False
        if self.metadata.owner_type != "user":
            return True
        owner = await self.metadata.owner()
        subject = self.metadata.owner_subject(owner)
        return not subject.startswith("user:") and bool(owner.github_login)

    @classmethod
    async def proxy_token(
        cls, thread_id: str, metadata: Mapping[str, object]
    ) -> SandboxGitHubAccess:
        """The GitHub token requests from ``thread_id``'s sandbox carry: scoped to the thread's
        own repositories, a review's to its pull request's and read-only; none where the
        thread may not use GitHub.

        It is the sandbox's owner's whatever run is going on there: a task worker's run in
        its coordinator's sandbox, or a run whose sender has no linked GitHub account, gets
        the coordinator's, the thread's own scope.
        """
        if not await cls(ThreadMetadata.model_validate(metadata)).allows_github():
            return SandboxGitHubAccess()
        repositories, permissions = await _thread_proxy_scope(thread_id, metadata)
        if repositories == []:
            return SandboxGitHubAccess()
        return await _proxy_token(thread_id, repositories, permissions)

    @classmethod
    async def reaches_github(cls, thread_id: str, metadata: Mapping[str, object]) -> bool:
        """Whether requests from ``thread_id``'s sandbox to GitHub carry a token
        (``proxy_token``): the thread may use GitHub, and its scope names some repository or
        the whole installation. Worked out without minting one."""
        if not await cls(ThreadMetadata.model_validate(metadata)).allows_github():
            return False
        repositories, _permissions = await _thread_proxy_scope(thread_id, metadata)
        return repositories != []


@dataclass(frozen=True, slots=True)
class SandboxCreateConfig:
    """What a new sandbox boots from: snapshot, VM sizing, provider create params."""

    snapshot_id: str | None
    resources: SandboxResources = field(default_factory=SandboxResources)
    create_params: dict[str, JsonValue] = field(default_factory=dict)
    options: LangSmithCreateOptions | None = None
    profile: ExecutionProfile | None = None

    @classmethod
    async def resolve(cls) -> SandboxCreateConfig:
        return await cls.from_profile(
            ExecutionProfile.current() or await ExecutionProfile.resolve()
        )

    @classmethod
    def from_defaults(cls, defaults: ExecutionDefaults) -> SandboxCreateConfig:
        resources: SandboxResources = {}
        if defaults.vcpus is not None:
            resources["vcpus"] = defaults.vcpus
        if defaults.mem_bytes is not None:
            resources["mem_bytes"] = defaults.mem_bytes
        if defaults.fs_capacity_bytes is not None:
            resources["fs_capacity_bytes"] = defaults.fs_capacity_bytes
        return cls(
            snapshot_id=defaults.snapshot_id,
            resources=resources,
            create_params={**defaults.create_params, "preserve_memory_on_stop": True},
        )

    @classmethod
    def from_options(cls, options: LangSmithCreateOptions) -> SandboxCreateConfig:
        return cls(snapshot_id=options.snapshot_id, options=options)

    @classmethod
    async def from_profile(cls, profile: ExecutionProfile) -> SandboxCreateConfig:
        return cls(snapshot_id=profile.create.snapshot_id, options=profile.create, profile=profile)

    def authority_profile(self) -> ExecutionProfile:
        if self.profile is not None:
            return self.profile.model_copy(
                update={"name": None, "instructions": "", "repos": [], "replacement": None}
            )
        return ExecutionProfile(
            uses_defaults=True,
            create=self.langsmith_options(),
        )

    @property
    def proxy_config(self) -> dict[str, Any] | None:
        if self.options is not None:
            proxy = self.options.extra_fields.get("proxy_config")
            return dict(proxy) if isinstance(proxy, dict) else None
        return get_sandbox_proxy_config(self.create_params)

    @classmethod
    async def bound_profile(
        cls, thread_id: str, metadata: Mapping[str, object]
    ) -> ExecutionProfile | None:
        sandbox_id = metadata.get("sandbox_id")
        if not isinstance(sandbox_id, str):
            return None
        if metadata.get("task_id") is not None:
            worker = ThreadMetadata.model_validate(metadata)
            context = await TaskMembership.context_for_thread(thread_id)
            host_id = context.task.coordinator_thread_id if context is not None else None
            if (
                context is None
                or context.membership.role != "worker"
                or context.task.id != worker.task_id
                or not host_id
                or host_id == thread_id
                or host_id != worker.sandbox_host_thread_id
            ):
                raise PermissionError("The shared sandbox host does not match this worker's task")
            host = ThreadMetadata.model_validate(await get_sandbox_metadata(host_id))
            if host.sandbox_host_thread_id or host.task_id or host.sandbox_id != sandbox_id:
                raise PermissionError("The coordinator's shared sandbox binding changed")
            thread_id = host_id
        return await SANDBOX_EXECUTION.bound(thread_id, sandbox_id)

    @classmethod
    async def for_thread(
        cls,
        metadata: Mapping[str, object],
        *,
        thread_id: str,
    ) -> SandboxCreateConfig:
        """Resolve authority only through this server thread's private sandbox binding."""
        sandbox_id = metadata.get("sandbox_id")
        if isinstance(sandbox_id, str):
            bound = await cls.bound_profile(thread_id, metadata)
            if bound is not None:
                return await cls.from_profile(bound)
            create = LangSmithCreateOptions.resolve().model_copy(
                update={"extra_fields": {"preserve_memory_on_stop": True, "proxy_config": {}}}
            )
            return cls.from_options(create)
        return await cls.resolve()

    async def recreation_options(self) -> LangSmithCreateOptions:
        """Use the source's current image without changing the bound sandbox's authority."""
        options = self.langsmith_options()
        if options.snapshot_id is None:
            return options
        snapshot_id = (await EXECUTION_DEFAULTS.load()).snapshot_id
        return options.model_copy(update={"snapshot_id": snapshot_id})

    def record_boot(self, thread_id: str) -> None:
        from openswe.execution_images import EXECUTION_IMAGES

        profile = self.authority_profile()
        if profile.stale():
            _STALE_BOOTS[thread_id] = profile
        _fire_and_forget(EXECUTION_IMAGES.maybe_start_update(), "execution image update trigger")

    def langsmith_options(self) -> LangSmithCreateOptions:
        """Include immutable federation authority at create; we refresh managed credentials later."""
        return self.options or LangSmithCreateOptions.resolve(
            snapshot_id=self.snapshot_id, create_params=self.create_params, **self.resources
        )


async def _proxy_token(
    thread_id: str | None,
    repositories: Sequence[str] | None,
    permissions: PermissionMap | None,
    *,
    force_refresh: bool = False,
) -> SandboxGitHubAccess:
    async with aphase(thread_id, "sandbox.proxy_token"):
        return await sandbox_token(
            repositories=repositories,
            permissions=permissions,
            force_refresh=force_refresh,
        )


_STALE_BOOTS: dict[str, ExecutionProfile] = {}


def take_stale_boot(thread_id: str) -> ExecutionProfile | None:
    """The pinned stale image this thread's new sandbox booted from, once."""
    return _STALE_BOOTS.pop(thread_id, None)


def _fire_and_forget(coro: Coroutine[Any, Any, Any], what: str) -> None:
    task = asyncio.ensure_future(coro)
    _BACKGROUND.add(task)

    def _done(t: asyncio.Task[Any]) -> None:
        _BACKGROUND.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logger.warning("%s failed", what, exc_info=t.exception())

    task.add_done_callback(_done)


_BACKGROUND: set[asyncio.Task[Any]] = set()


_WORK_DIR_READY = "__open_swe_work_dir_ready__"


async def configure_git_identity(sandbox_backend: SandboxBackendProtocol, git: GitIdentity) -> None:
    """Write ``git``, the thread owner's identity, and ready the provider's work dir in one command."""
    identity = (
        f"git config --global user.name {shlex.quote(git.name)} && "
        f"git config --global user.email {shlex.quote(git.email)}"
    )
    work_dir = declared_work_dir(sandbox_backend)
    command = identity
    if work_dir is not None:
        quoted = shlex.quote(work_dir)
        command = (
            f"{{ mkdir -p {quoted} && test -w {quoted} && echo {_WORK_DIR_READY}; }}; {identity}"
        )
    result = await sandbox_backend.aexecute(command)
    if result.exit_code != 0:
        raise RuntimeError(
            f"Failed to configure sandbox Git identity (exit code {result.exit_code}): {result.output}"
        )
    if work_dir is not None and _WORK_DIR_READY in result.output:
        remember_work_dir(sandbox_backend, work_dir)


async def _thread_git_identity(thread_id: str | None) -> GitIdentity:
    """The thread owner's git identity, or the bot's when it has none or cannot be read."""
    if thread_id is None:
        return GitIdentity()
    try:
        metadata = await get_sandbox_metadata(thread_id)
        return await ThreadMetadata.model_validate(metadata).git_identity()
    except Exception:
        logger.warning(
            "Could not resolve the thread owner's git identity",
            extra={"agent_thread_id": thread_id},
            exc_info=True,
        )
        return GitIdentity()


async def _connect_existing_sandbox(
    thread_id: str,
    *,
    cached: SandboxBackendProtocol | None,
    sandbox_id: str | None,
    external: bool = False,
) -> SandboxBackendProtocol:
    """Reuse the sandbox already bound to ``thread_id``, or fail unreachable.

    A ``SandboxGoneError`` propagates untouched so the caller recreates. Nothing
    pings the sandbox first: writing the git identity below has to reach it anyway,
    and fails unreachable when it cannot. An external sandbox is used as it is.
    """
    if cached is not None:
        logger.info("Using cached sandbox backend for thread %s", thread_id)
        sandbox_backend = cached
    else:
        logger.info("Connecting to existing sandbox %s", sandbox_id)
        try:
            async with aphase(thread_id, "sandbox.reconnect", sandbox_id=sandbox_id):
                sandbox_backend = await connect_existing_sandbox(str(sandbox_id))
        except SandboxGoneError:
            raise
        except Exception as exc:
            logger.warning("Failed to connect to existing sandbox %s", sandbox_id)
            raise SandboxUnreachableError(thread_id, sandbox_id, str(exc)) from exc
    if external:
        return sandbox_backend
    try:
        async with aphase(thread_id, "sandbox.git_identity"):
            await configure_git_identity(sandbox_backend, await _thread_git_identity(thread_id))
    except Exception as exc:
        logger.warning(
            "Failed to write the git identity in the sandbox",
            extra={"agent_thread_id": thread_id, "sandbox_id": sandbox_backend.id},
            exc_info=True,
        )
        raise SandboxUnreachableError(thread_id, sandbox_backend.id, str(exc)) from exc
    return sandbox_backend


@dataclass(frozen=True, slots=True)
class TaskWorkerSandbox:
    """A task worker's share of its coordinator's sandbox.

    The backend writes the worker's ``task_id`` and ``sandbox_host_thread_id`` when its
    coordinator delegates; the worker may work in the coordinator's sandbox only while the
    task's membership and both threads' ownership, visibility and GitHub scope agree.
    """

    thread_id: str
    worker: ThreadMetadata

    async def host(self, github_proxy_repositories: Sequence[str] | None = None) -> str:
        """The coordinator whose sandbox the worker may work in; raises ``PermissionError``
        when it may not."""
        worker, thread_id = self.worker, self.thread_id
        context = await TaskMembership.context_for_thread(thread_id)
        host_id = worker.sandbox_host_thread_id
        if (
            context is None
            or not host_id
            or context.membership.role != "worker"
            or context.task.id != worker.task_id
            or host_id != context.task.coordinator_thread_id
            or host_id == thread_id
        ):
            raise PermissionError("The shared sandbox host does not match this worker's task")
        host_context = await TaskMembership.context_for_thread(host_id)
        if (
            host_context is None
            or host_context.task.id != context.task.id
            or host_context.membership.role != "coordinator"
        ):
            raise PermissionError("The shared sandbox host is not this task's coordinator")
        host = ThreadMetadata.model_validate(await get_sandbox_metadata(host_id))
        if (
            host.sandbox_host_thread_id
            or host.task_id
            or worker.owner_type != host.owner_type
            or (worker.owner_type == "system" and worker.visibility != "public")
            or worker.admin_thread != host.admin_thread
            or worker.visibility != host.visibility
        ):
            raise PermissionError("The shared sandbox host has different ownership or permissions")
        if (
            await SandboxCredentialPolicy(worker).allows_github()
            != await SandboxCredentialPolicy(host).allows_github()
        ):
            raise PermissionError("The shared sandbox host has different credential permissions")
        host_repositories = host.github_token_repositories
        worker_repositories = narrowed_repositories(
            github_proxy_repositories, worker.github_token_repositories
        )
        if worker_repositories is not None and (
            host_repositories is None
            or not {repo.lower() for repo in host_repositories}.issubset(
                repo.lower() for repo in worker_repositories
            )
        ):
            raise PermissionError(
                "The shared sandbox grants repositories outside this worker's scope"
            )
        if worker.owner_type != "system":
            owner = await worker.owner()
            host_owner = await host.owner()
            if owner.id != host_owner.id:
                raise PermissionError(
                    "The shared sandbox host has different ownership or permissions"
                )
            for target_id, binding in ((thread_id, worker), (host_id, host)):
                if binding.owner_user_id is None:
                    await client.threads.update(
                        target_id, metadata={"owner_user_id": str(owner.id)}
                    )
        return host_id

    async def attach(self) -> SandboxBackendProtocol:
        """Connect the worker to its coordinator's existing sandbox and bind it there."""
        host_id = await self.host()
        backend = await ensure_sandbox_for_thread(host_id, require_existing=True)
        current_host = ThreadMetadata.model_validate(await get_sandbox_metadata(host_id))
        if current_host.sandbox_id != backend.id:
            raise RuntimeError("The coordinator's sandbox changed while the worker was attaching")
        await client.threads.update(
            thread_id=self.thread_id,
            metadata={
                "sandbox_id": backend.id,
                SANDBOX_PROXY_CONFIG_METADATA_KEY: current_host.sandbox_base_proxy_config,
            },
        )
        published = set_sandbox_backend(self.thread_id, unwrap_sandbox_backend(backend))
        if self.worker.running_background_tasks:
            from openswe.background_tasks import reconcile_background_tasks

            # Ensuring the host's sandbox reconciles only its own commands; reconcile this worker's.
            _fire_and_forget(
                reconcile_background_tasks(self.thread_id), "background task reconcile"
            )
        return published


async def _thread_proxy_scope(
    thread_id: str,
    metadata: Mapping[str, object],
    repositories: Sequence[str] | None = None,
    permissions: PermissionMap | None = None,
) -> tuple[Sequence[str] | None, PermissionMap | None]:
    if metadata.get("kind") in {REVIEWER_THREAD_KIND, REVIEWER_SHADOW_THREAD_KIND, "review-scout"}:
        permissions = READ_ONLY_REVIEW_PERMISSIONS
        reviewed_pr = metadata.get("pr")
        owner = reviewed_pr.get("owner") if isinstance(reviewed_pr, dict) else None
        name = reviewed_pr.get("name") if isinstance(reviewed_pr, dict) else None
        reviewed_repo = (
            [f"{owner}/{name}"]
            if isinstance(owner, str) and owner and isinstance(name, str) and name
            else []
        )
        repositories = narrowed_repositories(repositories, reviewed_repo)
    async with aphase(thread_id, "sandbox.token_scope"):
        repositories = narrowed_repositories(
            repositories, await thread_token_repositories(thread_id)
        )
    return repositories, permissions


async def ensure_sandbox_for_thread(
    thread_id: str,
    *,
    abandon_unreachable: bool = False,
    require_existing: bool = False,
) -> SandboxBackendProtocol:
    """Connect to the sandbox bound to ``thread_id``; the backend never creates one.

    1. Metadata has an id -> reuse this process's connection to that sandbox if
       it has one, else reconnect; then write the git identity.
    2. A task worker (metadata has ``task_id``) -> attach to its coordinator's
       existing sandbox.
    3. No sandbox yet -> ``SandboxUnreachableError``: MDA creates the thread's sandbox
       when its run starts, and binds the thread to it before preparing.

    A sandbox that can't be reached raises ``SandboxUnreachableError``: swapping in an
    empty one would destroy whatever the agent had not yet committed. MDA recovers
    its own sandboxes. A sandbox the backend made before MDA owned them that is *deleted*
    (``SandboxGoneError``) holds nothing, so the thread is handed to MDA: unbound,
    its next run starts in MDA's sandbox rather than reconnecting to the deleted one.

    ``abandon_unreachable`` hands over a merely unreachable sandbox too, for callers
    whose sandbox holds nothing but a re-derivable checkout — the read-only
    reviewer and review scout.

    ``require_existing`` is for a task worker attaching to its coordinator's sandbox,
    which it must never hand over.

    The git identity is written on every connection: reused sandboxes can lose their
    global config, and Vercel rejects commits whose email cannot resolve to a GitHub
    account. MDA writes the proxy config (``ManagedSandbox``).
    """
    async with aphase(thread_id, "sandbox.thread_metadata"):
        sandbox_metadata = await get_sandbox_metadata(thread_id)
    if sandbox_metadata.get("task_id") is not None:
        worker = TaskWorkerSandbox(thread_id, ThreadMetadata.model_validate(sandbox_metadata))
        return await worker.attach()
    raw_sandbox_id = sandbox_metadata.get("sandbox_id")
    sandbox_id = raw_sandbox_id if isinstance(raw_sandbox_id, str) else None
    if sandbox_id is None:
        raise SandboxUnreachableError(
            thread_id,
            None,
            "The coordinator must attach its sandbox first"
            if require_existing
            else "Managed Deep Agents could not open this thread's sandbox"
            if sandbox_metadata.get("sandbox_owner") == "mda"
            else "Managed Deep Agents opens this thread's sandbox when its run starts",
        )
    external = sandbox_metadata.get("sandbox_owner") == "external"
    try:
        sandbox_backend = await _connect_existing_sandbox(
            thread_id,
            cached=SANDBOX_CONNECTIONS.get(sandbox_id),
            sandbox_id=sandbox_id,
            external=external,
        )
    except (SandboxGoneError, SandboxUnreachableError) as exc:
        if require_existing:
            raise SandboxUnreachableError(
                thread_id,
                sandbox_id,
                "The coordinator must recover its sandbox before workers attach",
            ) from exc
        owned_by_mda = sandbox_metadata.get("sandbox_owner") == "mda"
        if (
            not owned_by_mda
            and not external
            and (isinstance(exc, SandboxGoneError) or abandon_unreachable)
        ):
            await BackendSandbox(thread_id, sandbox_id).release()
            raise SandboxHandedOverError(thread_id, sandbox_id, str(exc)) from exc
        # Nothing replaces a sandbox of MDA's: the thread stays bound to it.
        raise SandboxUnreachableError(thread_id, sandbox_id, str(exc)) from exc

    # Publishing last is what makes a failure above visible. Callers reach the
    # proxy's cached backend without awaiting the startup task that produced it,
    # so a backend published before this point would be used by the rest of the
    # run while the initialization that failed is only logged.
    from openswe.utils.background_task_state import RUNNING_BACKGROUND_TASKS_KEY

    published = set_sandbox_backend(thread_id, sandbox_backend)
    if sandbox_metadata.get(RUNNING_BACKGROUND_TASKS_KEY):
        from openswe.background_tasks import reconcile_background_tasks

        # A runner killed with its sandbox never calls back; this is where its task turns up lost.
        _fire_and_forget(reconcile_background_tasks(thread_id), "background task reconcile")
    return published


async def bind_mda_sandbox(
    thread_id: str,
    sandbox_id: str | None,
    *,
    open_error: str | None = None,
) -> None:
    """Bind a thread to the sandbox Managed Deep Agents opened for it, and mark the thread MDA's.

    MDA names and creates the sandbox, and writes its proxy config; the next connection
    writes its git identity, as it does on any reused sandbox. A sandbox under the name the
    thread is bound to, such as one MDA adopted, keeps the creation authority and proxy
    rules recorded for it; a new one is bound with the run's. With no ``sandbox_id`` MDA
    could not open the sandbox (``open_error`` says why), and the thread stays MDA's:
    preparing connects to the sandbox it was bound to before, unless LangSmith confirms it
    deleted (``SandboxDeletedError``), since nothing replaces it, and a thread bound to none is
    unreachable rather than given one here. A task worker attaches to its coordinator's
    sandbox through the backend.
    """
    raw = await get_sandbox_metadata(thread_id)
    metadata = ThreadMetadata.model_validate(raw)
    if metadata.task_id is not None:
        return
    if metadata.sandbox_owner == "external":
        if metadata.sandbox_id is None or (sandbox_id is None and open_error is not None):
            raise SandboxUnreachableError(
                thread_id,
                metadata.sandbox_id,
                "Could not open this thread's external sandbox",
                reason=open_error,
            )
        if sandbox_id not in (None, metadata.sandbox_id):
            raise SandboxUnreachableError(
                thread_id,
                metadata.sandbox_id,
                "Managed Deep Agents opened a different sandbox than this thread is bound to",
                reason=f"Expected {metadata.sandbox_id}; opened {sandbox_id}. The binding was preserved.",
            )
        return
    if (
        metadata.sandbox_owner == "mda"
        and metadata.sandbox_id is not None
        and sandbox_id not in (None, metadata.sandbox_id)
    ):
        raise SandboxUnreachableError(
            thread_id,
            metadata.sandbox_id,
            "Managed Deep Agents opened a different sandbox than this thread is bound to",
            reason=f"Expected {metadata.sandbox_id}; opened {sandbox_id}. The binding was preserved.",
        )
    if sandbox_id is None and open_error is not None and await metadata.mda_sandbox_deleted():
        raise SandboxDeletedError(
            thread_id,
            metadata.sandbox_id,
            "LangSmith deleted this thread's sandbox",
            reason=open_error,
        )
    if not (metadata.sandbox_owner == "mda" and sandbox_id in (None, metadata.sandbox_id)):
        await _bind_mda_sandbox(thread_id, sandbox_id, raw)
    if sandbox_id is None and metadata.sandbox_id is None:
        raise SandboxUnreachableError(
            thread_id,
            None,
            "Managed Deep Agents could not open this thread's sandbox",
            reason=open_error,
        )


async def _bind_mda_sandbox(
    thread_id: str, sandbox_id: str | None, raw: Mapping[str, object]
) -> None:
    metadata = ThreadMetadata.model_validate(raw)
    bind_metadata: JsonObject = {"sandbox_owner": "mda"}
    if sandbox_id is None or sandbox_id == metadata.sandbox_id:
        await THREADS.update(thread_id=thread_id, metadata=bind_metadata)
        return
    config = await SandboxCreateConfig.for_thread(raw, thread_id=thread_id)
    bind_metadata["sandbox_id"] = sandbox_id
    bind_metadata[SANDBOX_PROXY_CONFIG_METADATA_KEY] = config.proxy_config or {}
    async with aphase(thread_id, "sandbox.bind_thread"):
        await SANDBOX_EXECUTION.bind(
            thread_id, sandbox_id, config.authority_profile(), bind_metadata
        )
    if metadata.sandbox_id is not None:
        SANDBOX_CONNECTIONS.pop(metadata.sandbox_id, None)
    config.record_boot(thread_id)


# More threads than this bound to one sandbox count as sharing it, without reading them all.
_SHARED_SANDBOX_SCAN: Final = 200


@dataclass(frozen=True, slots=True)
class BackendSandbox:
    """A sandbox the backend made for a thread before Managed Deep Agents owned them."""

    thread_id: str
    sandbox_id: str

    async def hand_to_mda(self) -> str | None:
        """Make MDA the owner of the thread's sandbox; the name MDA adopts it by, or None when
        the thread is unbound and its run starts in a fresh sandbox MDA creates.

        MDA keeps one sandbox per thread, so it adopts this one under its own name unless
        another thread is bound to it too or LangSmith says it is gone. A sandbox LangSmith
        cannot account for is adopted, never abandoned: MDA's adoption raises rather than
        create a second sandbox while it cannot find the first.
        """
        from openswe.sandboxes.providers.langsmith import sandbox_exists

        if await self._shared():
            logger.info(
                "Another thread is bound to this thread's sandbox, so Managed Deep Agents "
                "starts it in a fresh one",
                extra={"thread_id": self.thread_id, "sandbox_id": self.sandbox_id},
            )
            await self.release()
            return None
        try:
            gone = not await sandbox_exists(self.sandbox_id)
        except Exception:
            logger.warning(
                "Could not tell whether a thread's old sandbox still exists",
                extra={"thread_id": self.thread_id, "sandbox_id": self.sandbox_id},
                exc_info=True,
            )
            gone = False
        if gone:
            await self.release()
            return None
        await self.adopt()
        return self.sandbox_id

    async def _shared(self) -> bool:
        """Whether another thread, not a task worker sharing it, is bound to the sandbox."""
        bound = await THREADS.search(
            ThreadSearch(metadata={"sandbox_id": self.sandbox_id}, limit=_SHARED_SANDBOX_SCAN)
        )
        return len(bound) >= _SHARED_SANDBOX_SCAN or any(
            thread.thread_id != self.thread_id and thread.metadata.get("task_id") is None
            for thread in bound
        )

    async def adopt(self) -> None:
        """Bind the thread to the sandbox as MDA's, under the name it has.

        The binding keeps the creation authority and proxy rules recorded for the sandbox, as
        it does for any sandbox MDA reports under the name the thread is bound to.
        """
        self._forget()
        await bind_mda_sandbox(self.thread_id, self.sandbox_id)
        logger.info(
            "Managed Deep Agents adopted a thread's sandbox",
            extra={"thread_id": self.thread_id, "sandbox_id": self.sandbox_id},
        )

    async def release(self) -> None:
        """Unbind the thread from the sandbox, so its next run starts in a fresh one of MDA's."""
        self._forget()
        await THREADS.update(
            thread_id=self.thread_id,
            metadata={"sandbox_id": None, SANDBOX_PROXY_CONFIG_METADATA_KEY: None},
        )
        logger.info(
            "Handed a thread to Managed Deep Agents",
            extra={"thread_id": self.thread_id, "sandbox_id": self.sandbox_id},
        )

    def _forget(self) -> None:
        """Drop this process's connection to the sandbox, made while the backend owned it."""
        SANDBOX_CONNECTIONS.pop(self.sandbox_id, None)
        if (cached := SANDBOX_BACKENDS.get(self.thread_id)) is not None:
            cached.detach()


def get_cached_sandbox_backend(
    thread_id: str,
    *,
    reconnect: Callable[[], Awaitable[SandboxBackendProtocol]] | None = None,
) -> SandboxBackendProxy:
    return get_or_create_sandbox_backend_proxy(thread_id, reconnect=reconnect)
