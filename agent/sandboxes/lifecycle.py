"""Get-or-create lifecycle for the sandbox bound to a thread.

Creation, reconnection, proxy-credential refresh, git identity, and the
recreate rebind. The registry itself lives in ``state``.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any, Literal

from deepagents.backends.protocol import SandboxBackendProtocol
from langgraph_sdk import get_client

from agent.bridge.backend import BridgeSandboxBackend
from agent.bridge.store import Bridge
from agent.config import ENV
from agent.github.proxy import get_recorded_proxy_base_config, record_proxy_token_expiry
from agent.github.sandbox_access import SandboxGitHubAccess, workspace_token
from agent.github.token_scope import token_repositories_from_metadata
from agent.sandboxes.providers.langsmith import configure_sandbox_proxy, get_sandbox_proxy_config
from agent.sandboxes.providers.registry import SandboxGoneError, create_sandbox
from agent.sandboxes.state import (
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
from agent.sandboxes.tool_access import SANDBOX_HOST_THREAD_KEY, SANDBOX_PROXY_CONFIG_METADATA_KEY
from agent.tasks.store import load_context
from agent.users import User
from agent.utils.authorship import OPEN_SWE_BOT_EMAIL, OPEN_SWE_BOT_NAME
from agent.utils.startup_trace import aphase
from agent.workspaces.refresh import is_snapshot_stale, maybe_start_update
from agent.workspaces.store import (
    SandboxResources,
    Workspace,
    load_workspace,
)

logger = logging.getLogger(__name__)

client = get_client()


def _owner_login(metadata: dict[str, Any]) -> str | None:
    owner = metadata.get("owner_login")
    return owner.strip() if isinstance(owner, str) and owner.strip() else None


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
        owner_login: str | None = None,
    ) -> SandboxCreateConfig:
        async def workspace_for_source() -> Workspace | None:
            # An absent slug is not "no workspace": load_workspace falls back to the
            # `default` workspace, so "base" has to skip the lookup outright.
            return None if source == "base" else await load_workspace(workspace_slug)

        workspace, preserve_memory = await asyncio.gather(
            workspace_for_source(), cls._owner_preserves_memory(owner_login)
        )
        if workspace is not None and workspace.inherit_default_sandbox:
            workspace = await load_workspace(None)
        create_params = workspace.sandbox_create_params() if workspace is not None else {}
        if preserve_memory:
            create_params = {**create_params, "preserve_memory_on_stop": True}
        if workspace is None:
            return cls(snapshot_id=None, create_params=create_params)
        return cls(
            snapshot_id=workspace.ready_snapshot_id,
            resources=workspace.sandbox_resources(),
            create_params=create_params,
            workspace=workspace,
        )

    @staticmethod
    async def _owner_preserves_memory(owner_login: str | None) -> bool:
        if not owner_login:
            return False
        try:
            preferences = await User.preferences_for_login(owner_login)
        except Exception:
            # A preference only picks how the box stops; it must not block the boot.
            logger.warning(
                "Could not load sandbox memory preference",
                exc_info=True,
                extra={"owner_login": owner_login},
            )
            return False
        return preferences.preserve_sandbox_memory

    @property
    def proxy_config(self) -> dict[str, Any] | None:
        return get_sandbox_proxy_config(self.create_params)

    async def boot(self) -> SandboxBackendProtocol:
        if self.create_params:
            return await create_sandbox(
                snapshot_id=self.snapshot_id,
                create_params=self.create_params,
                **self.resources,
            )
        return await create_sandbox(snapshot_id=self.snapshot_id, **self.resources)


async def _create_sandbox_with_proxy(
    *,
    thread_id: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    workspace_slug: str | None = None,
    source: SandboxSource = "workspace",
    owner_login: str | None = None,
    record_stale_boot: bool = False,
) -> SandboxBackendProtocol:
    """Create a new sandbox with GitHub proxy auth configured."""
    async with aphase(thread_id, "sandbox.resolve_snapshot"):
        config = await SandboxCreateConfig.resolve(
            workspace_slug, source=source, owner_login=owner_login
        )
    async with aphase(thread_id, "sandbox.boot", snapshot_id=config.snapshot_id):
        sandbox_backend = await config.boot()

    async with git_identity(thread_id, sandbox_backend):
        if ENV.SANDBOX_TYPE.get() == "langsmith":
            async with aphase(thread_id, "sandbox.proxy_token"):
                access = await workspace_token(
                    workspace_slug, repositories=github_proxy_repositories
                )
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
    base_proxy_config: dict[str, Any] | None = None,
    workspace_slug: str | None = None,
) -> None:
    """Refresh managed proxy credentials for reused LangSmith sandboxes."""
    if ENV.SANDBOX_TYPE.get() != "langsmith":
        return

    async with aphase(thread_id, "sandbox.proxy_token"):
        access = await workspace_token(workspace_slug, repositories=github_proxy_repositories)

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
        workspace_slug=workspace_slug,
        base_proxy_config=base_proxy_config,
    )


async def _refresh_github_proxy_or_fail(
    sandbox_backend: SandboxBackendProtocol,
    thread_id: str,
    github_proxy_repositories: Sequence[str] | None = None,
    base_proxy_config: dict[str, Any] | None = None,
    workspace_slug: str | None = None,
) -> SandboxBackendProtocol:
    """Refresh proxy credentials; a sandbox we can't reconfigure is unreachable."""
    try:
        await _refresh_github_proxy(
            sandbox_backend,
            thread_id=thread_id,
            github_proxy_repositories=github_proxy_repositories,
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


async def configure_git_identity(sandbox_backend: SandboxBackendProtocol) -> None:
    await sandbox_backend.aexecute(
        f"git config --global user.name '{OPEN_SWE_BOT_NAME}' && "
        f"git config --global user.email '{OPEN_SWE_BOT_EMAIL}'",
    )


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
        )
    return refreshed


async def _attach_task_worker_sandbox(
    thread_id: str,
    metadata: Mapping[str, object],
    *,
    github_proxy_repositories: Sequence[str] | None,
    workspace_slug: str | None,
) -> SandboxBackendProtocol:
    context = await load_context(thread_id)
    host_id = metadata.get(SANDBOX_HOST_THREAD_KEY)
    if (
        context is None
        or not isinstance(host_id, str)
        or context.membership.role != "worker"
        or str(context.task.id) != metadata.get("task_id")
        or host_id != context.task.coordinator_thread_id
        or host_id == thread_id
    ):
        raise PermissionError("The shared sandbox host does not match this worker's task")
    host_context = await load_context(host_id)
    if (
        host_context is None
        or host_context.task.id != context.task.id
        or host_context.membership.role != "coordinator"
    ):
        raise PermissionError("The shared sandbox host is not this task's coordinator")
    host = await get_sandbox_metadata(host_id)
    owner = metadata.get("owner_login")
    host_owner = host.get("owner_login")
    if (
        host.get(SANDBOX_HOST_THREAD_KEY)
        or host.get("task_id")
        or metadata.get("owner_type") != "user"
        or host.get("owner_type") != "user"
        or not isinstance(owner, str)
        or not owner.strip()
        or not isinstance(host_owner, str)
        or owner.strip().lower() != host_owner.strip().lower()
        or metadata.get("workspace") != context.task.workspace
        or (host.get("workspace") or host.get("environment") or "default") != context.task.workspace
        or (workspace_slug is not None and workspace_slug != context.task.workspace)
        or metadata.get("admin_thread", False) != host.get("admin_thread", False)
        or metadata.get("visibility", "public") != host.get("visibility", "public")
    ):
        raise PermissionError("The shared sandbox host has different ownership or permissions")
    host_repositories = token_repositories_from_metadata(host)
    worker_repositories = narrowed_repositories(
        github_proxy_repositories, token_repositories_from_metadata(metadata)
    )
    if worker_repositories is not None and (
        host_repositories is None
        or not {repo.lower() for repo in host_repositories}.issubset(
            repo.lower() for repo in worker_repositories
        )
    ):
        raise PermissionError("The shared sandbox grants repositories outside this worker's scope")
    backend = await ensure_sandbox_for_thread(
        host_id, workspace_slug=context.task.workspace, require_existing=True
    )
    current_host = await get_sandbox_metadata(host_id)
    if current_host.get("sandbox_id") != backend.id:
        raise RuntimeError("The coordinator's sandbox changed while the worker was attaching")
    await client.threads.update(
        thread_id=thread_id,
        metadata={
            "sandbox_id": backend.id,
            SANDBOX_PROXY_CONFIG_METADATA_KEY: current_host.get(SANDBOX_PROXY_CONFIG_METADATA_KEY),
        },
    )
    from agent.utils.background_task_state import RUNNING_BACKGROUND_TASKS_KEY

    published = set_sandbox_backend(thread_id, unwrap_sandbox_backend(backend))
    if metadata.get(RUNNING_BACKGROUND_TASKS_KEY):
        from agent.background_tasks import reconcile_background_tasks

        # Ensuring the host's sandbox reconciles only its own commands; reconcile this worker's.
        _fire_and_forget(reconcile_background_tasks(thread_id), "background task reconcile")
    return published


async def ensure_sandbox_for_thread(
    thread_id: str,
    *,
    github_proxy_repositories: Sequence[str] | None = None,
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
            return set_sandbox_backend(
                thread_id, await BridgeSandboxBackend.connect(thread_id, bridge_id)
            )
    metadata_proxy_config = sandbox_metadata.get(SANDBOX_PROXY_CONFIG_METADATA_KEY)
    base_proxy_config = (
        metadata_proxy_config
        if isinstance(metadata_proxy_config, dict)
        else get_recorded_proxy_base_config(thread_id)
    )
    owner_login = _owner_login(sandbox_metadata)
    created = False
    created_proxy_config: dict[str, Any] | None = None
    async with aphase(thread_id, "sandbox.token_scope"):
        github_proxy_repositories = narrowed_repositories(
            github_proxy_repositories, await thread_token_repositories(thread_id)
        )

    if sandbox_id is None:
        logger.info("Creating new sandbox for thread %s", thread_id)
        sandbox_backend = await _create_sandbox_with_proxy(
            thread_id=thread_id,
            github_proxy_repositories=github_proxy_repositories,
            workspace_slug=workspace_slug,
            owner_login=owner_login,
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
                    workspace_slug=workspace_slug,
                    owner_login=owner_login,
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
    from agent.sandboxes.tool_access import provision_tool_url
    from agent.utils.background_task_state import RUNNING_BACKGROUND_TASKS_KEY

    await provision_tool_url(thread_id, sandbox_backend)
    published = set_sandbox_backend(thread_id, sandbox_backend)
    if sandbox_metadata.get(RUNNING_BACKGROUND_TASKS_KEY):
        from agent.background_tasks import reconcile_background_tasks

        # A runner killed with its sandbox never calls back; this is where its task turns up lost.
        _fire_and_forget(reconcile_background_tasks(thread_id), "background task reconcile")
    return published


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

    from agent.sandboxes.providers.langsmith import get_async_sandbox_client

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

    new_sandbox = await _create_sandbox_with_proxy(
        thread_id=thread_id,
        github_proxy_repositories=await thread_token_repositories(thread_id),
        workspace_slug=workspace_slug,
        source=source,
        owner_login=_owner_login(metadata),
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
    from agent.sandboxes.tool_access import provision_tool_url

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
