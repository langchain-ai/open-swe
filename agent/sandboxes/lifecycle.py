"""Get-or-create lifecycle for the sandbox bound to a thread.

Creation, reconnection, proxy-credential refresh, git identity, and the
recreate rebind. The registry itself lives in ``state``.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any, Literal

from deepagents.backends.protocol import SandboxBackendProtocol
from langgraph_sdk import get_client

from agent.config import ENV
from agent.github.proxy import get_recorded_proxy_base_config, record_proxy_token_expiry
from agent.github.sandbox_access import SandboxGitHubAccess, workspace_token
from agent.sandboxes.providers.langsmith import configure_github_proxy, get_sandbox_proxy_config
from agent.sandboxes.providers.registry import SandboxGoneError, create_sandbox
from agent.sandboxes.state import (
    SANDBOX_BACKENDS,
    SANDBOX_CONNECTIONS,
    SandboxBackendProxy,
    SandboxUnreachableError,
    get_or_create_sandbox_backend_proxy,
    get_sandbox_id_from_metadata,
    get_sandbox_metadata,
    set_sandbox_backend,
    unwrap_sandbox_backend,
)
from agent.utils.authorship import OPEN_SWE_BOT_EMAIL, OPEN_SWE_BOT_NAME
from agent.utils.startup_trace import aphase
from agent.workspaces.refresh import is_snapshot_stale, maybe_start_update
from agent.workspaces.store import (
    SandboxResources,
    Workspace,
    load_workspace,
    sandbox_update_timeout,
    script_command,
)

logger = logging.getLogger(__name__)

client = get_client()

_SANDBOX_PROXY_CONFIG_METADATA_KEY = "sandbox_base_proxy_config"


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
        # An absent slug is not "no workspace": load_workspace falls back to the
        # `default` workspace, so "base" has to skip the lookup outright.
        workspace = None if source == "base" else await load_workspace(workspace_slug)
        if workspace is None:
            return cls(snapshot_id=None)
        return cls(
            snapshot_id=workspace.ready_snapshot_id,
            resources=workspace.sandbox_resources(),
            create_params=workspace.sandbox_create_params(),
            workspace=workspace,
        )

    @property
    def proxy_config(self) -> dict[str, Any] | None:
        return get_sandbox_proxy_config(self.create_params)

    async def run_update_script(
        self, sandbox_backend: SandboxBackendProtocol, thread_id: str | None
    ) -> None:
        """Freshen this box's checkouts when the snapshot it booted from has aged out.

        Awaited before the first model call, on purpose: refreshing only the
        snapshot in the background never helps the run that triggered it, and
        with sparse traffic every run is a triggering run — so the first run
        after a quiet spell would otherwise work against a checkout as old as
        the last nightly rebuild. Bounded by a short timeout, and never fatal:
        the image is already usable, so a failed pull costs freshness, not the
        run.
        """
        workspace = self.workspace
        if workspace is None or not is_snapshot_stale(workspace):
            return
        try:
            async with aphase(thread_id, "sandbox.update_script"):
                result = await sandbox_backend.aexecute(
                    script_command(workspace.update_script, "update"),
                    timeout=sandbox_update_timeout(),
                )
        except Exception:
            # "Never fatal" has to cover the execute itself: it can raise past
            # its own retries when a freshly booted box is briefly unreachable,
            # and losing the whole sandbox over a skipped `git pull` is worse
            # than starting from the snapshot as captured.
            logger.warning(
                "Workspace update script could not run in sandbox %s",
                sandbox_backend.id,
                exc_info=True,
                extra={"workspace": workspace.slug},
            )
            return
        if result.exit_code != 0:
            logger.warning(
                "Workspace update script exited %s in sandbox %s",
                result.exit_code,
                sandbox_backend.id,
                extra={
                    "workspace": workspace.slug,
                    "exit_code": result.exit_code,
                    "log_tail": (result.output or "")[-2000:],
                },
            )

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
) -> SandboxBackendProtocol:
    """Create a new sandbox with GitHub proxy auth configured."""
    async with aphase(thread_id, "sandbox.resolve_snapshot"):
        config = await SandboxCreateConfig.resolve(workspace_slug, source=source)
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

    # This run gets fresh checkouts now; the background capture makes the *next*
    # creation skip the step entirely.
    await config.run_update_script(sandbox_backend, thread_id)
    _fire_and_forget(maybe_start_update(config.workspace), "workspace update trigger")
    return sandbox_backend


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
    await configure_github_proxy(sandbox_id, access.token, **kwargs)


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


async def ensure_sandbox_for_thread(
    thread_id: str,
    *,
    github_proxy_repositories: Sequence[str] | None = None,
    workspace_slug: str | None = None,
    allow_replacement: bool = False,
) -> SandboxBackendProtocol:
    """Get-or-create a healthy sandbox bound to ``thread_id``.

    Three cases (dispatch uses ``multitask_strategy="interrupt"``, so a thread
    never provisions two sandboxes concurrently — no cross-process sentinel is
    needed):

    1. Metadata has an id -> reuse this process's connection to that sandbox if
       it has one, else reconnect; then refresh proxy.
    2. No sandbox at all -> create one and persist the id.

    A sandbox that exists but can't be reached raises ``SandboxUnreachableError``
    instead of being replaced, because a replacement is empty and swapping one in
    silently destroys whatever the agent had not yet committed. A *deleted* one
    (``SandboxGoneError``) is always replaced: it holds nothing, and the stale id
    in thread metadata is what every later run keeps reconnecting to, so refusing
    would brick the thread permanently.

    ``allow_replacement`` extends replacement to merely unreachable sandboxes,
    for callers whose sandbox holds nothing but a re-derivable checkout — the
    read-only reviewer, which re-preps the repo every run.

    For LangSmith sandboxes, also refreshes the GitHub App proxy auth. Newly
    created sandboxes boot from the workspace's snapshot when one is ready,
    otherwise LangSmith's own base snapshot.
    Re-applies git identity every run because reused/reconnected sandboxes can
    lose their ``--global`` config, and Vercel preview deploys reject commits
    whose author email can't be resolved to a GitHub account.
    """
    async with aphase(thread_id, "sandbox.thread_metadata"):
        sandbox_metadata = await get_sandbox_metadata(thread_id)
    raw_sandbox_id = sandbox_metadata.get("sandbox_id")
    sandbox_id = raw_sandbox_id if isinstance(raw_sandbox_id, str) else None
    metadata_proxy_config = sandbox_metadata.get(_SANDBOX_PROXY_CONFIG_METADATA_KEY)
    base_proxy_config = (
        metadata_proxy_config
        if isinstance(metadata_proxy_config, dict)
        else get_recorded_proxy_base_config(thread_id)
    )
    created = False
    created_proxy_config: dict[str, Any] | None = None

    if sandbox_id is None:
        logger.info("Creating new sandbox for thread %s", thread_id)
        sandbox_backend = await _create_sandbox_with_proxy(
            thread_id=thread_id,
            github_proxy_repositories=github_proxy_repositories,
            workspace_slug=workspace_slug,
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
        sandbox_metadata: dict[str, Any] = {"sandbox_id": sandbox_backend.id}
        if created_proxy_config is not None:
            sandbox_metadata[_SANDBOX_PROXY_CONFIG_METADATA_KEY] = created_proxy_config
        async with aphase(thread_id, "sandbox.bind_thread"):
            await client.threads.update(thread_id=thread_id, metadata=sandbox_metadata)

    # Publishing last is what makes a failure above visible. Callers reach the
    # proxy's cached backend without awaiting the startup task that produced it,
    # so a backend published before this point would be used by the rest of the
    # run while the initialization that failed is only logged.
    from agent.sandboxes.tool_access import provision_tool_url

    await provision_tool_url(thread_id, sandbox_backend)
    return set_sandbox_backend(thread_id, sandbox_backend)


async def recreate_sandbox_for_thread(
    thread_id: str,
    *,
    workspace_slug: str | None = None,
    source: SandboxSource = "workspace",
) -> tuple[str, str]:
    """Bind a thread to a fresh sandbox while preserving its previous sandbox.

    ``workspace`` boots the thread's workspace snapshot; ``base`` boots the base
    snapshot with deployment defaults, as a thread with no workspace would.
    """
    cached = SANDBOX_BACKENDS.get(thread_id)
    metadata_sandbox_id = await get_sandbox_id_from_metadata(thread_id)
    old_sandbox_id = cached.id if cached is not None and cached.has_backend else metadata_sandbox_id
    if not old_sandbox_id:
        raise ValueError(f"Thread {thread_id} has no sandbox to recreate")

    new_sandbox = await _create_sandbox_with_proxy(
        thread_id=thread_id,
        workspace_slug=workspace_slug,
        source=source,
    )
    if new_sandbox.id == old_sandbox_id:
        raise RuntimeError("Sandbox provider did not create a distinct sandbox")

    await configure_git_identity(new_sandbox)
    sandbox_metadata: dict[str, Any] = {"sandbox_id": new_sandbox.id}
    base_proxy_config = get_recorded_proxy_base_config(thread_id)
    if base_proxy_config is not None:
        sandbox_metadata[_SANDBOX_PROXY_CONFIG_METADATA_KEY] = base_proxy_config
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
    return old_sandbox_id, new_sandbox.id


def get_cached_sandbox_backend(
    thread_id: str,
    *,
    reconnect: Callable[[], Awaitable[SandboxBackendProtocol]] | None = None,
) -> SandboxBackendProxy:
    return get_or_create_sandbox_backend_proxy(thread_id, reconnect=reconnect)
