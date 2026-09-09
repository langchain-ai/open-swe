"""Get-or-create lifecycle for the sandbox bound to a thread.

Creation, reconnection, proxy-credential refresh, git identity, and the
reset/recreate rebinds. The registry itself lives in ``state``.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any

from deepagents.backends.protocol import SandboxBackendProtocol
from langgraph_sdk import get_client

from agent.config import ENV
from agent.dashboard.environments import SandboxResources, resolve_environment
from agent.dashboard.sandbox_settings import get_admin_base_snapshot_id
from agent.dashboard.team_credentials import LangSmithCredentials
from agent.github.app import get_github_app_installation_token_with_expiry
from agent.github.proxy import get_recorded_proxy_base_config, record_proxy_token_expiry
from agent.sandboxes.providers.langsmith import (
    _configure_github_proxy,
    _get_sandbox_proxy_config,
    create_langsmith_sandbox_from_params,
)
from agent.sandboxes.providers.registry import SandboxGoneError, create_sandbox
from agent.sandboxes.state import (
    SANDBOX_BACKENDS,
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

logger = logging.getLogger(__name__)

client = get_client()

_SANDBOX_PROXY_CONFIG_METADATA_KEY = "sandbox_base_proxy_config"


async def _resolve_proxy_token(
    github_proxy_token: str | None,
) -> tuple[str | None, str | None, None]:
    """Resolve the proxy token and its expiry."""
    if github_proxy_token:
        return github_proxy_token, None, None
    token, expires_at = await get_github_app_installation_token_with_expiry()
    return token, expires_at, None


@dataclass(frozen=True, slots=True)
class SandboxCreateConfig:
    """What a new sandbox boots from: snapshot, VM sizing, provider create params."""

    snapshot_id: str | None
    resources: SandboxResources = field(default_factory=SandboxResources)
    create_params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    async def resolve(cls, environment_slug: str | None = None) -> SandboxCreateConfig:
        environment = await resolve_environment(environment_slug)
        if environment is None:
            return cls(snapshot_id=await get_admin_base_snapshot_id())
        return cls(
            snapshot_id=environment.ready_snapshot_id or await get_admin_base_snapshot_id(),
            resources=environment.sandbox_resources(),
            create_params=environment.sandbox_create_params(),
        )

    @property
    def proxy_config(self) -> dict[str, Any] | None:
        return _get_sandbox_proxy_config(self.create_params)

    async def boot(self) -> SandboxBackendProtocol:
        if self.create_params:
            return await create_sandbox(
                snapshot_id=self.snapshot_id,
                create_params=self.create_params,
                **self.resources,
            )
        return await create_sandbox(snapshot_id=self.snapshot_id, **self.resources)


async def _create_sandbox_with_proxy(
    github_proxy_token: str | None = None,
    *,
    thread_id: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    environment_slug: str | None = None,
    langsmith_credentials: LangSmithCredentials | None = None,
) -> SandboxBackendProtocol:
    """Create a new sandbox with GitHub proxy auth configured."""
    async with aphase(thread_id, "sandbox.resolve_snapshot"):
        config = await SandboxCreateConfig.resolve(environment_slug)
    async with aphase(thread_id, "sandbox.boot", snapshot_id=config.snapshot_id):
        sandbox_backend = await config.boot()

    async with git_identity(thread_id, sandbox_backend):
        if ENV.SANDBOX_TYPE.get() == "langsmith":
            async with aphase(thread_id, "sandbox.proxy_token"):
                token, expires_at, permissions = await _resolve_proxy_token(github_proxy_token)
            if not token:
                msg = "Cannot configure proxy: GitHub App installation token is unavailable"
                logger.error(msg)
                raise ValueError(msg)
            proxy_config = config.proxy_config
            async with aphase(thread_id, "sandbox.proxy_configure"):
                await _configure_proxy(
                    sandbox_backend.id,
                    token,
                    proxy_config,
                    langsmith_credentials=langsmith_credentials,
                )
            record_proxy_token_expiry(
                thread_id,
                expires_at,
                repositories=github_proxy_repositories,
                permissions=permissions,
                base_proxy_config=proxy_config,
            )

    return sandbox_backend


async def _configure_proxy(
    sandbox_id: str,
    token: str,
    base_proxy_config: dict[str, Any] | None,
    *,
    langsmith_credentials: LangSmithCredentials | None = None,
) -> None:
    kwargs: dict[str, Any] = {}
    if base_proxy_config is not None:
        kwargs["base_proxy_config"] = base_proxy_config
    if langsmith_credentials is not None:
        kwargs["langsmith_credentials"] = langsmith_credentials
    await _configure_github_proxy(sandbox_id, token, **kwargs)


async def _refresh_github_proxy(
    sandbox_backend: SandboxBackendProtocol,
    github_proxy_token: str | None = None,
    *,
    thread_id: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    base_proxy_config: dict[str, Any] | None = None,
    langsmith_credentials: LangSmithCredentials | None = None,
) -> None:
    """Refresh managed proxy credentials for reused LangSmith sandboxes."""
    if ENV.SANDBOX_TYPE.get() != "langsmith":
        return

    async with aphase(thread_id, "sandbox.proxy_token"):
        token, expires_at, permissions = await _resolve_proxy_token(github_proxy_token)
    if not token:
        raise ValueError("Cannot configure proxy: GitHub App installation token is unavailable")

    current_backend = unwrap_sandbox_backend(sandbox_backend)
    async with aphase(thread_id, "sandbox.proxy_refresh"):
        await _configure_proxy(
            current_backend.id,
            token,
            base_proxy_config,
            langsmith_credentials=langsmith_credentials,
        )
    record_proxy_token_expiry(
        thread_id,
        expires_at,
        repositories=github_proxy_repositories,
        permissions=permissions,
        base_proxy_config=base_proxy_config,
    )


async def _refresh_github_proxy_or_fail(
    sandbox_backend: SandboxBackendProtocol,
    thread_id: str,
    github_proxy_token: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    base_proxy_config: dict[str, Any] | None = None,
    langsmith_credentials: LangSmithCredentials | None = None,
) -> SandboxBackendProtocol:
    """Refresh proxy credentials; a sandbox we can't reconfigure is unreachable."""
    try:
        await _refresh_github_proxy(
            sandbox_backend,
            github_proxy_token,
            thread_id=thread_id,
            github_proxy_repositories=github_proxy_repositories,
            base_proxy_config=base_proxy_config,
            langsmith_credentials=langsmith_credentials,
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
    github_proxy_token: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    base_proxy_config: dict[str, Any] | None = None,
    langsmith_credentials: LangSmithCredentials | None = None,
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
        refresh_kwargs: dict[str, Any] = {}
        if langsmith_credentials is not None:
            refresh_kwargs["langsmith_credentials"] = langsmith_credentials
        refreshed = await _refresh_github_proxy_or_fail(
            sandbox_backend,
            thread_id,
            github_proxy_token,
            github_proxy_repositories,
            base_proxy_config,
            **refresh_kwargs,
        )
    return refreshed


async def ensure_sandbox_for_thread(
    thread_id: str,
    *,
    github_proxy_token: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    environment_slug: str | None = None,
    allow_replacement: bool = False,
    langsmith_credentials: LangSmithCredentials | None = None,
) -> SandboxBackendProtocol:
    """Get-or-create a healthy sandbox bound to ``thread_id``.

    Three cases (dispatch uses ``multitask_strategy="interrupt"``, so a thread
    never provisions two sandboxes concurrently — no cross-process sentinel is
    needed):

    1. Cached in memory -> ping, then refresh proxy.
    2. Metadata has an id -> reconnect, then refresh proxy.
    3. No sandbox at all -> create one and persist the id.

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
    created sandboxes boot from the environment's snapshot when one is ready,
    otherwise the base snapshot (admin setting, else
    ``DEFAULT_SANDBOX_SNAPSHOT_ID``).
    Re-applies git identity every run because reused/reconnected sandboxes can
    lose their ``--global`` config, and Vercel preview deploys reject commits
    whose author email can't be resolved to a GitHub account.
    """
    cached_proxy = SANDBOX_BACKENDS.get(thread_id)
    sandbox_backend = (
        unwrap_sandbox_backend(cached_proxy)
        if cached_proxy is not None and cached_proxy.has_backend
        else None
    )
    async with aphase(thread_id, "sandbox.thread_metadata"):
        sandbox_id = await get_sandbox_id_from_metadata(thread_id)
        sandbox_metadata = await get_sandbox_metadata(thread_id) if sandbox_id is not None else {}
    metadata_proxy_config = sandbox_metadata.get(_SANDBOX_PROXY_CONFIG_METADATA_KEY)
    base_proxy_config = (
        metadata_proxy_config
        if isinstance(metadata_proxy_config, dict)
        else get_recorded_proxy_base_config(thread_id)
    )
    created_proxy_config: dict[str, Any] | None = None
    create_kwargs = (
        {"langsmith_credentials": langsmith_credentials}
        if langsmith_credentials is not None
        else {}
    )

    if sandbox_backend is None and sandbox_id is None:
        logger.info("Creating new sandbox for thread %s", thread_id)
        sandbox_backend = await _create_sandbox_with_proxy(
            github_proxy_token,
            thread_id=thread_id,
            github_proxy_repositories=github_proxy_repositories,
            environment_slug=environment_slug,
            **create_kwargs,
        )
        created_proxy_config = get_recorded_proxy_base_config(thread_id)
        logger.info("Sandbox created: %s", sandbox_backend.id)
    else:
        try:
            sandbox_backend = await _connect_existing_sandbox(
                thread_id,
                cached=sandbox_backend,
                sandbox_id=sandbox_id,
                github_proxy_token=github_proxy_token,
                github_proxy_repositories=github_proxy_repositories,
                base_proxy_config=base_proxy_config,
                langsmith_credentials=langsmith_credentials,
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
                    github_proxy_token,
                    thread_id=thread_id,
                    github_proxy_repositories=github_proxy_repositories,
                    environment_slug=environment_slug,
                    **create_kwargs,
                )
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
    if sandbox_id != sandbox_backend.id:
        sandbox_metadata: dict[str, Any] = {"sandbox_id": sandbox_backend.id}
        if created_proxy_config is not None:
            sandbox_metadata[_SANDBOX_PROXY_CONFIG_METADATA_KEY] = created_proxy_config
        async with aphase(thread_id, "sandbox.bind_thread"):
            await client.threads.update(thread_id=thread_id, metadata=sandbox_metadata)

    # Publishing last is what makes a failure above visible. Callers reach the
    # proxy's cached backend without awaiting the startup task that produced it,
    # so a backend published before this point would be used by the rest of the
    # run while the initialization that failed is only logged.
    return set_sandbox_backend(thread_id, sandbox_backend)


async def reset_sandbox_for_thread(
    thread_id: str,
    create_params: dict[str, Any],
    *,
    langsmith_credentials: LangSmithCredentials | None = None,
) -> tuple[str, str]:
    """Bind a thread to a fresh sandbox created from raw provider options."""
    if ENV.SANDBOX_TYPE.get() != "langsmith":
        raise ValueError("sandbox_reset is only supported by the LangSmith sandbox provider")

    cached = SANDBOX_BACKENDS.get(thread_id)
    metadata_sandbox_id = await get_sandbox_id_from_metadata(thread_id)
    old_sandbox_id = cached.id if cached is not None and cached.has_backend else metadata_sandbox_id
    if not old_sandbox_id:
        raise ValueError(f"Thread {thread_id} has no sandbox to reset")

    new_sandbox = await create_langsmith_sandbox_from_params(create_params)
    if new_sandbox.id == old_sandbox_id:
        raise RuntimeError("Sandbox provider did not create a distinct sandbox")

    proxy_config = _get_sandbox_proxy_config(create_params)
    token, expires_at, permissions = await _resolve_proxy_token(None)
    if not token:
        raise ValueError("Cannot configure proxy: GitHub App installation token is unavailable")
    await _configure_proxy(
        new_sandbox.id,
        token,
        proxy_config,
        langsmith_credentials=langsmith_credentials,
    )
    await configure_git_identity(new_sandbox)
    sandbox_metadata: dict[str, Any] = {
        "sandbox_id": new_sandbox.id,
        _SANDBOX_PROXY_CONFIG_METADATA_KEY: proxy_config,
    }
    await client.threads.update(thread_id=thread_id, metadata=sandbox_metadata)
    set_sandbox_backend(thread_id, new_sandbox)
    record_proxy_token_expiry(
        thread_id,
        expires_at,
        permissions=permissions,
        base_proxy_config=proxy_config,
    )
    logger.info(
        "Reset thread %s from sandbox %s to sandbox %s",
        thread_id,
        old_sandbox_id,
        new_sandbox.id,
    )
    return old_sandbox_id, new_sandbox.id


async def recreate_sandbox_for_thread(
    thread_id: str,
    *,
    environment_slug: str | None = None,
    langsmith_credentials: LangSmithCredentials | None = None,
) -> tuple[str, str]:
    """Bind a thread to a fresh sandbox while preserving its previous sandbox."""
    cached = SANDBOX_BACKENDS.get(thread_id)
    metadata_sandbox_id = await get_sandbox_id_from_metadata(thread_id)
    old_sandbox_id = cached.id if cached is not None and cached.has_backend else metadata_sandbox_id
    if not old_sandbox_id:
        raise ValueError(f"Thread {thread_id} has no sandbox to recreate")

    create_kwargs: dict[str, Any] = {}
    if langsmith_credentials is not None:
        create_kwargs["langsmith_credentials"] = langsmith_credentials
    new_sandbox = await _create_sandbox_with_proxy(
        thread_id=thread_id,
        environment_slug=environment_slug,
        **create_kwargs,
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
