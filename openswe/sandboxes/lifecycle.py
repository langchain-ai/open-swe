"""Get-or-create lifecycle for the sandbox bound to a thread.

Creation, reconnection, proxy-credential refresh, git identity, and the
recreate rebind. The registry itself lives in ``state``.
"""

import asyncio
import logging
import shlex
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any, Literal

from deepagents.backends.protocol import SandboxBackendProtocol
from langgraph_sdk import get_client

from openswe.bridge.backend import BridgeSandboxBackend
from openswe.bridge.store import Bridge
from openswe.config import ENV
from openswe.github.app import PermissionMap
from openswe.github.proxy import get_recorded_proxy_base_config, record_proxy_token_expiry
from openswe.github.sandbox_access import (
    READ_ONLY_REVIEW_PERMISSIONS,
    SandboxGitHubAccess,
    workspace_token,
)
from openswe.review.findings import REVIEWER_THREAD_KIND
from openswe.sandboxes.handoff import complete_handoff
from openswe.sandboxes.paths import declared_work_dir, remember_work_dir
from openswe.sandboxes.providers.langsmith import (
    LangSmithCreateOptions,
    configure_sandbox_proxy,
    get_sandbox_proxy_config,
)
from openswe.sandboxes.providers.registry import SandboxGoneError, create_sandbox
from openswe.sandboxes.state import (
    SANDBOX_BACKENDS,
    SANDBOX_CONNECTIONS,
    SandboxBackendProxy,
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
from openswe.utils.authorship import OPEN_SWE_BOT_EMAIL, OPEN_SWE_BOT_NAME
from openswe.utils.startup_trace import aphase
from openswe.workspaces.refresh import is_snapshot_stale, maybe_start_update
from openswe.workspaces.store import (
    SandboxResources,
    Workspace,
    load_workspace,
)

logger = logging.getLogger(__name__)

client = get_client()


SandboxSource = Literal["workspace", "base"]


@dataclass(frozen=True, slots=True)
class SandboxCreateConfig:
    """What a new sandbox boots from: snapshot, VM sizing, provider create params."""

    snapshot_id: str | None
    resources: SandboxResources = field(default_factory=SandboxResources)
    create_params: dict[str, Any] = field(default_factory=dict)
    workspace: Workspace | None = None

    @classmethod
    async def resolve(
        cls,
        workspace_slug: str | None = None,
        *,
        source: SandboxSource = "workspace",
    ) -> SandboxCreateConfig:
        workspace = None if source == "base" else await load_workspace(workspace_slug)
        if workspace is not None and workspace.inherit_default_sandbox:
            workspace = await load_workspace(None)
        create_params = workspace.sandbox_create_params() if workspace is not None else {}
        create_params = {**create_params, "preserve_memory_on_stop": True}
        if workspace is None:
            return cls(snapshot_id=None, create_params=create_params)
        return cls(
            snapshot_id=workspace.ready_snapshot_id,
            resources=workspace.sandbox_resources(),
            create_params=create_params,
            workspace=workspace,
        )

    @property
    def proxy_config(self) -> dict[str, Any] | None:
        return get_sandbox_proxy_config(self.create_params)

    def langsmith_options(self) -> LangSmithCreateOptions:
        """What the box is created with when MDA creates it, its proxy config left to us."""
        create_params = {k: v for k, v in self.create_params.items() if k != "proxy_config"}
        return LangSmithCreateOptions.resolve(
            snapshot_id=self.snapshot_id, create_params=create_params, **self.resources
        )

    async def boot(self) -> SandboxBackendProtocol:
        if self.create_params:
            return await create_sandbox(
                snapshot_id=self.snapshot_id,
                create_params=self.create_params,
                prepare_work_dir=False,
                **self.resources,
            )
        # The git identity write right after boot readies the work dir in its own round trip.
        return await create_sandbox(
            snapshot_id=self.snapshot_id, prepare_work_dir=False, **self.resources
        )


async def _proxy_token(
    thread_id: str | None,
    workspace_slug: str | None,
    repositories: Sequence[str] | None,
    permissions: PermissionMap | None,
) -> SandboxGitHubAccess:
    async with aphase(thread_id, "sandbox.proxy_token"):
        return await workspace_token(
            workspace_slug, repositories=repositories, permissions=permissions
        )


async def _create_sandbox_with_proxy(
    *,
    thread_id: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    github_proxy_permissions: PermissionMap | None = None,
    workspace_slug: str | None = None,
    source: SandboxSource = "workspace",
    record_stale_boot: bool = False,
) -> SandboxBackendProtocol:
    """Create a new sandbox with GitHub proxy auth configured."""
    async with aphase(thread_id, "sandbox.resolve_snapshot"):
        config = await SandboxCreateConfig.resolve(workspace_slug, source=source)
    # The proxy token does not depend on the sandbox, so it is minted while the box boots.
    token = (
        asyncio.ensure_future(
            _proxy_token(
                thread_id, workspace_slug, github_proxy_repositories, github_proxy_permissions
            )
        )
        if ENV.SANDBOX_TYPE.get() == "langsmith"
        else None
    )
    if token is not None:
        token.add_done_callback(lambda minted: minted.cancelled() or minted.exception())
    try:
        async with aphase(thread_id, "sandbox.boot", snapshot_id=config.snapshot_id):
            sandbox_backend = await config.boot()
    except BaseException:
        if token is not None:
            token.cancel()
        raise

    async with git_identity(thread_id, sandbox_backend):
        if token is not None:
            access = await token
            proxy_config = config.proxy_config
            async with aphase(thread_id, "sandbox.proxy_configure"):
                await _configure_proxy(
                    sandbox_backend.id,
                    access,
                    proxy_config,
                    thread_id=thread_id,
                )
            record_proxy_token_expiry(
                thread_id,
                access.expires_at,
                repositories=github_proxy_repositories,
                permissions=github_proxy_permissions,
                workspace_slug=workspace_slug,
                base_proxy_config=proxy_config,
            )

    if (
        record_stale_boot
        and thread_id
        and config.workspace is not None
        and is_snapshot_stale(config.workspace)
    ):
        _STALE_BOOTS[thread_id] = config.workspace
    _fire_and_forget(maybe_start_update(config.workspace), "workspace update trigger")
    return sandbox_backend


_STALE_BOOTS: dict[str, Workspace] = {}


def take_stale_boot(thread_id: str) -> Workspace | None:
    """The workspace whose stale snapshot this thread's new sandbox booted from, once."""
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


async def _configure_proxy(
    sandbox_id: str,
    access: SandboxGitHubAccess,
    base_proxy_config: dict[str, Any] | None,
    *,
    thread_id: str | None = None,
) -> None:
    kwargs: dict[str, Any] = {}
    if base_proxy_config is not None:
        kwargs["base_proxy_config"] = base_proxy_config
    if thread_id is not None:
        kwargs["thread_id"] = thread_id
    await configure_sandbox_proxy(sandbox_id, access.token, **kwargs)


async def _refresh_github_proxy(
    sandbox_backend: SandboxBackendProtocol,
    *,
    thread_id: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    github_proxy_permissions: PermissionMap | None = None,
    base_proxy_config: dict[str, Any] | None = None,
    workspace_slug: str | None = None,
) -> None:
    """Refresh managed proxy credentials for reused LangSmith sandboxes."""
    if ENV.SANDBOX_TYPE.get() != "langsmith":
        return

    access = await _proxy_token(
        thread_id, workspace_slug, github_proxy_repositories, github_proxy_permissions
    )

    current_backend = unwrap_sandbox_backend(sandbox_backend)
    async with aphase(thread_id, "sandbox.proxy_refresh"):
        await _configure_proxy(
            current_backend.id,
            access,
            base_proxy_config,
            thread_id=thread_id,
        )
    record_proxy_token_expiry(
        thread_id,
        access.expires_at,
        repositories=github_proxy_repositories,
        permissions=github_proxy_permissions,
        workspace_slug=workspace_slug,
        base_proxy_config=base_proxy_config,
    )


async def _refresh_github_proxy_or_fail(
    sandbox_backend: SandboxBackendProtocol,
    thread_id: str,
    github_proxy_repositories: Sequence[str] | None = None,
    base_proxy_config: dict[str, Any] | None = None,
    workspace_slug: str | None = None,
    *,
    github_proxy_permissions: PermissionMap | None = None,
) -> SandboxBackendProtocol:
    """Refresh proxy credentials; a sandbox we can't reconfigure is unreachable."""
    try:
        await _refresh_github_proxy(
            sandbox_backend,
            thread_id=thread_id,
            github_proxy_repositories=github_proxy_repositories,
            github_proxy_permissions=github_proxy_permissions,
            base_proxy_config=base_proxy_config,
            workspace_slug=workspace_slug,
        )
    except Exception as exc:
        logger.warning(
            "Failed to refresh GitHub proxy for sandbox %s on thread %s",
            sandbox_backend.id,
            thread_id,
            exc_info=True,
        )
        raise SandboxUnreachableError(thread_id, sandbox_backend.id, str(exc)) from exc
    return sandbox_backend


_WORK_DIR_READY = "__open_swe_work_dir_ready__"


async def configure_git_identity(sandbox_backend: SandboxBackendProtocol) -> None:
    """Write the bot identity, and ready the provider's work dir in the same round trip.

    Creating the work dir and proving it writable is the check the work-dir probe
    makes, so every connection that runs this spares the run that probe.
    """
    identity = (
        f"git config --global user.name '{OPEN_SWE_BOT_NAME}' && "
        f"git config --global user.email '{OPEN_SWE_BOT_EMAIL}'"
    )
    work_dir = declared_work_dir(sandbox_backend)
    if work_dir is None:
        await sandbox_backend.aexecute(identity)
        return
    quoted = shlex.quote(work_dir)
    result = await sandbox_backend.aexecute(
        f"{{ mkdir -p {quoted} && test -w {quoted} && echo {_WORK_DIR_READY}; }}; {identity}"
    )
    if _WORK_DIR_READY in result.output:
        remember_work_dir(sandbox_backend, work_dir)


@asynccontextmanager
async def git_identity(
    thread_id: str | None, sandbox_backend: SandboxBackendProtocol
) -> AsyncIterator[None]:
    """Write the bot identity while the body configures the proxy.

    The identity needs the box, not the proxy, and the cost is the round trip
    rather than the two `git config` calls — on a cold sandbox that round trip
    is over a second of the critical path before the first model call. A body
    that raises has lost the sandbox, so the write is dropped rather than joined.
    """

    async def run() -> None:
        async with aphase(thread_id, "sandbox.git_identity"):
            await configure_git_identity(sandbox_backend)

    task = asyncio.create_task(run())
    try:
        yield
    except BaseException:
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
        raise
    await task


async def _connect_existing_sandbox(
    thread_id: str,
    *,
    cached: SandboxBackendProtocol | None,
    sandbox_id: str | None,
    github_proxy_repositories: Sequence[str] | None = None,
    github_proxy_permissions: PermissionMap | None = None,
    base_proxy_config: dict[str, Any] | None = None,
    workspace_slug: str | None = None,
) -> SandboxBackendProtocol:
    """Reuse the sandbox already bound to ``thread_id``, or fail unreachable.

    A ``SandboxGoneError`` propagates untouched so the caller recreates. Nothing
    pings the box first: refreshing the proxy below has to reach it anyway, and
    raises the same unreachable error when it cannot.
    """
    if cached is not None:
        logger.info("Using cached sandbox backend for thread %s", thread_id)
        sandbox_backend = cached
    else:
        logger.info("Connecting to existing sandbox %s", sandbox_id)
        try:
            async with aphase(thread_id, "sandbox.reconnect", sandbox_id=sandbox_id):
                sandbox_backend = await create_sandbox(str(sandbox_id))
        except SandboxGoneError:
            raise
        except Exception as exc:
            logger.warning("Failed to connect to existing sandbox %s", sandbox_id)
            raise SandboxUnreachableError(thread_id, sandbox_id, str(exc)) from exc
    async with git_identity(thread_id, sandbox_backend):
        refreshed = await _refresh_github_proxy_or_fail(
            sandbox_backend,
            thread_id,
            github_proxy_repositories,
            base_proxy_config,
            workspace_slug,
            github_proxy_permissions=github_proxy_permissions,
        )
    return refreshed


async def _attach_task_worker_sandbox(
    thread_id: str,
    metadata: Mapping[str, object],
    *,
    github_proxy_repositories: Sequence[str] | None,
    workspace_slug: str | None,
) -> SandboxBackendProtocol:
    worker = ThreadMetadata.model_validate(metadata)
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
        or worker.workspace != context.task.workspace.slug
        or (host.workspace or host.environment or "default") != context.task.workspace.slug
        or (workspace_slug is not None and workspace_slug != context.task.workspace.slug)
        or worker.admin_thread != host.admin_thread
        or worker.visibility != host.visibility
    ):
        raise PermissionError("The shared sandbox host has different ownership or permissions")
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
        raise PermissionError("The shared sandbox grants repositories outside this worker's scope")
    if worker.owner_type != "system":
        owner = await worker.owner()
        host_owner = await host.owner()
        if owner.id != host_owner.id:
            raise PermissionError("The shared sandbox host has different ownership or permissions")
        for target_id, binding in ((thread_id, worker), (host_id, host)):
            if binding.owner_user_id is None:
                await client.threads.update(target_id, metadata={"owner_user_id": str(owner.id)})
    backend = await ensure_sandbox_for_thread(
        host_id, workspace_slug=context.task.workspace.slug, require_existing=True
    )
    current_host = ThreadMetadata.model_validate(await get_sandbox_metadata(host_id))
    if current_host.sandbox_id != backend.id:
        raise RuntimeError("The coordinator's sandbox changed while the worker was attaching")
    await client.threads.update(
        thread_id=thread_id,
        metadata={
            "sandbox_id": backend.id,
            SANDBOX_PROXY_CONFIG_METADATA_KEY: current_host.sandbox_base_proxy_config,
        },
    )
    published = set_sandbox_backend(thread_id, unwrap_sandbox_backend(backend))
    if worker.running_background_tasks:
        from openswe.background_tasks import reconcile_background_tasks

        # Ensuring the host's sandbox reconciles only its own commands; reconcile this worker's.
        _fire_and_forget(reconcile_background_tasks(thread_id), "background task reconcile")
    return published


async def _thread_proxy_scope(
    thread_id: str,
    metadata: Mapping[str, object],
    repositories: Sequence[str] | None = None,
    permissions: PermissionMap | None = None,
) -> tuple[Sequence[str] | None, PermissionMap | None]:
    if metadata.get("kind") in {REVIEWER_THREAD_KIND, "review-scout"}:
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
    github_proxy_repositories: Sequence[str] | None = None,
    github_proxy_permissions: PermissionMap | None = None,
    workspace_slug: str | None = None,
    allow_replacement: bool = False,
    record_stale_boot: bool = False,
    require_existing: bool = False,
) -> SandboxBackendProtocol:
    """Get-or-create a healthy sandbox bound to ``thread_id``.

    Three cases (dispatch uses ``multitask_strategy="interrupt"``, so a thread
    never provisions two sandboxes concurrently — no cross-process sentinel is
    needed):

    1. Metadata has an id -> reuse this process's connection to that sandbox if
       it has one, else reconnect; then refresh proxy.
    2. No sandbox at all -> create one and persist the id.
    3. A task worker (metadata has ``task_id``) -> attach to its coordinator's
       existing sandbox; it never creates or replaces one.

    A sandbox that exists but can't be reached raises ``SandboxUnreachableError``
    instead of being replaced, because a replacement is empty and swapping one in
    silently destroys whatever the agent had not yet committed. A *deleted* one
    (``SandboxGoneError``) is always replaced: it holds nothing, and the stale id
    in thread metadata is what every later run keeps reconnecting to, so refusing
    would brick the thread permanently.

    ``allow_replacement`` extends replacement to merely unreachable sandboxes,
    for callers whose sandbox holds nothing but a re-derivable checkout — the
    read-only reviewer, which re-preps the repo every run.

    ``github_proxy_permissions`` narrows what the proxy's GitHub token may do, at
    creation, reconnection and every expiry refresh. Reviewer and review-scout
    threads always receive read-only GitHub credentials.

    ``record_stale_boot`` is for callers that collect ``take_stale_boot``.

    ``require_existing`` raises ``SandboxUnreachableError`` instead of creating or
    replacing a sandbox.

    For LangSmith sandboxes, also refreshes the GitHub App proxy auth. Newly
    created sandboxes boot from the workspace's snapshot when one is ready,
    otherwise LangSmith's own base snapshot.
    Re-applies git identity every run because reused/reconnected sandboxes can
    lose their ``--global`` config, and Vercel preview deploys reject commits
    whose author email can't be resolved to a GitHub account.
    """
    async with aphase(thread_id, "sandbox.thread_metadata"):
        sandbox_metadata = await get_sandbox_metadata(thread_id)
    if sandbox_metadata.get("task_id") is not None:
        return await _attach_task_worker_sandbox(
            thread_id,
            sandbox_metadata,
            github_proxy_repositories=github_proxy_repositories,
            workspace_slug=workspace_slug,
        )
    raw_sandbox_id = sandbox_metadata.get("sandbox_id")
    sandbox_id = raw_sandbox_id if isinstance(raw_sandbox_id, str) else None
    # MDA names, creates and replaces this thread's box; only it may give the thread another.
    owned_by_mda = sandbox_metadata.get("sandbox_owner") == "mda"
    if owned_by_mda and sandbox_id is None:
        raise SandboxUnreachableError(
            thread_id, None, "Managed Deep Agents could not open this thread's sandbox"
        )
    if require_existing and sandbox_id is None:
        raise SandboxUnreachableError(
            thread_id, None, "The coordinator must attach its sandbox first"
        )
    bridge_id = Bridge.bridge_id_of(sandbox_id)
    if bridge_id is not None:
        # The sandbox is the user's own machine: there is nothing to boot, no
        # managed proxy to reconfigure, and no global git config of theirs to
        # rewrite. An unreachable bridge fails the run, as any bound sandbox does.
        async with aphase(thread_id, "sandbox.bridge_connect", sandbox_id=sandbox_id):
            bridged = await BridgeSandboxBackend.connect(thread_id, bridge_id)
        async with aphase(thread_id, "sandbox.handoff"):
            await complete_handoff(thread_id, sandbox_metadata, bridged)
        return set_sandbox_backend(thread_id, bridged)
    metadata_proxy_config = sandbox_metadata.get(SANDBOX_PROXY_CONFIG_METADATA_KEY)
    base_proxy_config = (
        metadata_proxy_config
        if isinstance(metadata_proxy_config, dict)
        else get_recorded_proxy_base_config(thread_id)
    )
    created = False
    created_proxy_config: dict[str, Any] | None = None
    github_proxy_repositories, github_proxy_permissions = await _thread_proxy_scope(
        thread_id, sandbox_metadata, github_proxy_repositories, github_proxy_permissions
    )

    if sandbox_id is None:
        logger.info("Creating new sandbox for thread %s", thread_id)
        sandbox_backend = await _create_sandbox_with_proxy(
            thread_id=thread_id,
            github_proxy_repositories=github_proxy_repositories,
            github_proxy_permissions=github_proxy_permissions,
            workspace_slug=workspace_slug,
            record_stale_boot=record_stale_boot,
        )
        created = True
        created_proxy_config = get_recorded_proxy_base_config(thread_id)
        logger.info("Sandbox created: %s", sandbox_backend.id)
    else:
        try:
            sandbox_backend = await _connect_existing_sandbox(
                thread_id,
                cached=SANDBOX_CONNECTIONS.get(sandbox_id),
                sandbox_id=sandbox_id,
                github_proxy_repositories=github_proxy_repositories,
                github_proxy_permissions=github_proxy_permissions,
                base_proxy_config=base_proxy_config,
                workspace_slug=workspace_slug,
            )
        except (SandboxGoneError, SandboxUnreachableError) as exc:
            if require_existing:
                raise SandboxUnreachableError(
                    thread_id,
                    sandbox_id,
                    "The coordinator must recover its sandbox before workers attach",
                ) from exc
            gone = isinstance(exc, SandboxGoneError)
            if owned_by_mda:
                # MDA recreates a deleted box under its name when the next run opens it.
                raise SandboxUnreachableError(thread_id, sandbox_id, str(exc)) from exc
            if not (gone or allow_replacement):
                raise
            logger.warning(
                "Replacing %s sandbox %s for thread %s",
                "deleted" if gone else "unreachable",
                sandbox_id,
                thread_id,
            )
            try:
                sandbox_backend = await _create_sandbox_with_proxy(
                    thread_id=thread_id,
                    github_proxy_repositories=github_proxy_repositories,
                    github_proxy_permissions=github_proxy_permissions,
                    workspace_slug=workspace_slug,
                    record_stale_boot=record_stale_boot,
                )
                created = True
                created_proxy_config = get_recorded_proxy_base_config(thread_id)
            except Exception as create_exc:
                # Keep the failure typed so callers still recognize "this run has no
                # sandbox" and can notify the user.
                logger.warning(
                    "Failed to replace sandbox %s for thread %s",
                    sandbox_id,
                    thread_id,
                    exc_info=True,
                )
                raise SandboxUnreachableError(
                    thread_id, sandbox_id, str(create_exc)
                ) from create_exc
            logger.info("Replacement sandbox created: %s", sandbox_backend.id)

    # Bind the thread only once the sandbox is created and initialized: a run
    # that dies earlier leaves no id to reconnect to, so the next run creates
    # rather than adopting a half-built box.
    if created:
        bind_metadata: dict[str, Any] = {"sandbox_id": sandbox_backend.id}
        if created_proxy_config is not None:
            bind_metadata[SANDBOX_PROXY_CONFIG_METADATA_KEY] = created_proxy_config
        async with aphase(thread_id, "sandbox.bind_thread"):
            await client.threads.update(thread_id=thread_id, metadata=bind_metadata)

    # Publishing last is what makes a failure above visible. Callers reach the
    # proxy's cached backend without awaiting the startup task that produced it,
    # so a backend published before this point would be used by the rest of the
    # run while the initialization that failed is only logged.
    from openswe.sandboxes.tool_access import provision_tool_url
    from openswe.utils.background_task_state import RUNNING_BACKGROUND_TASKS_KEY

    async with aphase(thread_id, "sandbox.handoff"):
        await complete_handoff(thread_id, sandbox_metadata, sandbox_backend)
    await provision_tool_url(thread_id, sandbox_backend)
    published = set_sandbox_backend(thread_id, sandbox_backend)
    if sandbox_metadata.get(RUNNING_BACKGROUND_TASKS_KEY):
        from openswe.background_tasks import reconcile_background_tasks

        # A runner killed with its sandbox never calls back; this is where its task turns up lost.
        _fire_and_forget(reconcile_background_tasks(thread_id), "background task reconcile")
    return published


async def bind_mda_sandbox(
    thread_id: str, sandbox_id: str | None, *, workspace_slug: str | None = None
) -> None:
    """Bind a thread to the box Managed Deep Agents opened for it, and mark the thread MDA's.

    MDA names, creates and replaces the box; the next connection configures it as it
    does any reused box: proxy credentials, git identity, the tool channel. With no
    ``sandbox_id`` MDA could not open the box, and the thread stays MDA's, so preparing
    reports it unreachable rather than creating one here. A task worker attaches to its
    coordinator's box through the backend, as before.
    """
    metadata = ThreadMetadata.model_validate(await get_sandbox_metadata(thread_id))
    if metadata.task_id is not None:
        return
    if metadata.sandbox_owner == "mda" and sandbox_id in (None, metadata.sandbox_id):
        return
    bind_metadata: dict[str, Any] = {"sandbox_owner": "mda"}
    config = await SandboxCreateConfig.resolve(workspace_slug)
    if sandbox_id is not None and sandbox_id != metadata.sandbox_id:
        bind_metadata["sandbox_id"] = sandbox_id
        if config.proxy_config is not None:
            bind_metadata[SANDBOX_PROXY_CONFIG_METADATA_KEY] = config.proxy_config
        if metadata.sandbox_id is not None:
            SANDBOX_CONNECTIONS.pop(metadata.sandbox_id, None)
        if config.workspace is not None and is_snapshot_stale(config.workspace):
            _STALE_BOOTS[thread_id] = config.workspace
        _fire_and_forget(maybe_start_update(config.workspace), "workspace update trigger")
    async with aphase(thread_id, "sandbox.bind_thread"):
        await client.threads.update(thread_id=thread_id, metadata=bind_metadata)


# The replacement ``recreate_sandbox`` authorized for an MDA box: its workspace and source.
_REPLACEMENT_METADATA_KEY = "sandbox_replacement"


async def request_mda_replacement(
    thread_id: str, *, workspace_slug: str | None, source: SandboxSource
) -> LangSmithCreateOptions:
    """What MDA replaces the thread's box with, recorded so the reconnect applies its proxy.

    The reconnect takes the proxy config from this record, never from anything MDA sends.
    """
    config = await SandboxCreateConfig.resolve(workspace_slug, source=source)
    await client.threads.update(
        thread_id=thread_id,
        metadata={_REPLACEMENT_METADATA_KEY: {"workspace": workspace_slug, "source": source}},
    )
    return config.langsmith_options()


async def reconnect_replaced_sandbox(
    thread_id: str,
    *,
    workspace_slug: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    github_proxy_permissions: PermissionMap | None = None,
) -> SandboxBackendProtocol:
    """Connect again to the thread's box, which MDA just replaced with a fresh one of the same name.

    A replacement from another workspace or the base snapshot brings that one's proxy rules.
    """
    metadata = await get_sandbox_metadata(thread_id)
    requested = metadata.get(_REPLACEMENT_METADATA_KEY)
    if isinstance(requested, dict):
        workspace = requested.get("workspace")
        config = await SandboxCreateConfig.resolve(
            workspace if isinstance(workspace, str) else None,
            source="base" if requested.get("source") == "base" else "workspace",
        )
        await client.threads.update(
            thread_id=thread_id,
            metadata={
                SANDBOX_PROXY_CONFIG_METADATA_KEY: config.proxy_config or {},
                _REPLACEMENT_METADATA_KEY: None,
            },
        )
    sandbox_id = metadata.get("sandbox_id")
    if isinstance(sandbox_id, str):
        SANDBOX_CONNECTIONS.pop(sandbox_id, None)
    return await ensure_sandbox_for_thread(
        thread_id,
        workspace_slug=workspace_slug,
        github_proxy_repositories=github_proxy_repositories,
        github_proxy_permissions=github_proxy_permissions,
    )


class SandboxRecreationStopError(RuntimeError):
    def __init__(self, old_sandbox_id: str, new_sandbox_id: str, reason: str) -> None:
        super().__init__(reason)
        self.old_sandbox_id = old_sandbox_id
        self.new_sandbox_id = new_sandbox_id


async def recreate_sandbox_for_thread(
    thread_id: str,
    *,
    workspace_slug: str | None = None,
    source: SandboxSource = "workspace",
) -> tuple[str, str]:
    """Bind a fresh sandbox, then raise with its IDs if stopping the old one failed."""
    cached = SANDBOX_BACKENDS.get(thread_id)
    metadata = await get_sandbox_metadata(thread_id)
    if metadata.get("task_id") is not None:
        raise PermissionError(
            "Task workers cannot recreate the shared sandbox; the coordinator must recover it"
        )
    raw_sandbox_id = metadata.get("sandbox_id")
    metadata_sandbox_id = raw_sandbox_id if isinstance(raw_sandbox_id, str) else None
    old_sandbox_id = cached.id if cached is not None and cached.has_backend else metadata_sandbox_id
    if not old_sandbox_id:
        raise ValueError(f"Thread {thread_id} has no sandbox to recreate")
    if Bridge.bridge_id_of(old_sandbox_id) is not None:
        raise ValueError("A thread bridged to a local machine cannot be given a cloud sandbox")
    if metadata.get("sandbox_owner") == "mda":
        raise ValueError("Managed Deep Agents owns this thread's sandbox; only it may replace it")

    from openswe.sandboxes.providers.langsmith import get_async_sandbox_client

    stop_error = None
    try:
        async with asyncio.timeout(10):
            if ENV.SANDBOX_TYPE.get() != "langsmith":
                stop_error = "Stopping this sandbox provider is not supported"
                logger.warning(
                    "Stopping this sandbox provider is not supported",
                    extra={"sandbox_id": old_sandbox_id, "thread_id": thread_id},
                )
            else:
                async with get_async_sandbox_client() as sandbox_client:
                    await sandbox_client.stop_sandbox(old_sandbox_id)
    except Exception as exc:
        stop_error = (
            "Stopping the old sandbox timed out after 10 seconds"
            if isinstance(exc, TimeoutError)
            else str(exc)
        )
        logger.warning(
            "Failed to stop old sandbox before recreation",
            extra={"sandbox_id": old_sandbox_id, "thread_id": thread_id},
            exc_info=True,
        )
    else:
        if stop_error is None:
            logger.info(
                "Stopped old sandbox before recreation", extra={"sandbox_id": old_sandbox_id}
            )

    repositories, permissions = await _thread_proxy_scope(thread_id, metadata)
    new_sandbox = await _create_sandbox_with_proxy(
        thread_id=thread_id,
        github_proxy_repositories=repositories,
        github_proxy_permissions=permissions,
        workspace_slug=workspace_slug,
        source=source,
    )
    if new_sandbox.id == old_sandbox_id:
        raise RuntimeError("Sandbox provider did not create a distinct sandbox")

    await configure_git_identity(new_sandbox)
    sandbox_metadata: dict[str, Any] = {"sandbox_id": new_sandbox.id}
    base_proxy_config = get_recorded_proxy_base_config(thread_id)
    if base_proxy_config is not None:
        sandbox_metadata[SANDBOX_PROXY_CONFIG_METADATA_KEY] = base_proxy_config
    await client.threads.update(
        thread_id=thread_id,
        metadata=sandbox_metadata,
    )
    set_sandbox_backend(thread_id, new_sandbox)
    from openswe.sandboxes.tool_access import provision_tool_url

    await provision_tool_url(thread_id, new_sandbox)
    logger.info(
        "Rebound thread %s from sandbox %s to sandbox %s",
        thread_id,
        old_sandbox_id,
        new_sandbox.id,
    )
    if stop_error is not None:
        raise SandboxRecreationStopError(old_sandbox_id, new_sandbox.id, stop_error)
    return old_sandbox_id, new_sandbox.id


def get_cached_sandbox_backend(
    thread_id: str,
    *,
    reconnect: Callable[[], Awaitable[SandboxBackendProtocol]] | None = None,
) -> SandboxBackendProxy:
    return get_or_create_sandbox_backend_proxy(thread_id, reconnect=reconnect)
