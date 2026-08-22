"""One sandbox per thread: get-or-create it, keep it reachable, hand it out.

Shared by every graph that runs commands somewhere — the coding agent, the
reviewer, the analyzer — and by the tools that rebind a thread to a fresh
sandbox. Per-thread state lives in the sandbox itself plus thread metadata
(``sandbox_id``); this module owns the rules for moving between them.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from deepagents.backends.protocol import SandboxBackendProtocol
from langgraph_sdk import get_client

from ..github.authorship import OPEN_SWE_BOT_EMAIL, OPEN_SWE_BOT_NAME
from ..github.proxy import configure_proxy_for_sandbox, get_recorded_proxy_base_config
from ..sandboxes.providers import (
    SandboxGoneError,
    SandboxResources,
    SandboxUnreachableError,
    create_sandbox,
    current_sandbox_provider,
    sandbox_provider_supports_snapshots,
)
from ..sandboxes.proxy import SandboxBackendProxy, unwrap_sandbox_backend
from ..sandboxes.registry import (
    SANDBOX_BACKENDS,
    get_or_create_sandbox_backend_proxy,
    get_sandbox_id_from_metadata,
    get_sandbox_metadata,
    set_sandbox_backend,
)
from ..settings.environments import (
    environment_sandbox_create_params,
    environment_sandbox_resources,
    environment_snapshot_id,
    resolve_environment,
)
from ..settings.repo_snapshots import resolve_repo_snapshot_id
from ..settings.sandbox_settings import get_admin_base_snapshot_id
from ..settings.team_settings import get_team_default_repo

logger = logging.getLogger(__name__)

client = get_client()

# Thread metadata key for the proxy config the bound sandbox was created with.
SANDBOX_PROXY_CONFIG_METADATA_KEY = "sandbox_base_proxy_config"


def environment_slug(configurable: Mapping[str, Any] | None) -> str | None:
    """The environment this thread selected, if any."""
    slug = (configurable or {}).get("environment")
    return slug.strip() or None if isinstance(slug, str) else None


async def resolve_default_repo(configurable: Mapping[str, Any]) -> dict[str, str] | None:
    """The repo this run works in: the run's own choice, else the team default."""
    repo_config = configurable.get("repo")
    if isinstance(repo_config, dict):
        owner = repo_config.get("owner")
        name = repo_config.get("name")
        if isinstance(owner, str) and isinstance(name, str):
            return {"owner": owner, "name": name}

    if configurable.get("repo_explicitly_none") is True:
        return None

    try:
        return await get_team_default_repo()
    except Exception:
        logger.debug("Failed to load team default repo for prompt", exc_info=True)
        return None


@dataclass(frozen=True)
class SandboxCreateConfig:
    """What a thread's new sandbox boots as: its snapshot, sizing and create params."""

    snapshot_id: str | None
    resources: SandboxResources
    create_params: dict[str, Any]


async def _resolve_sandbox_create_config(
    repo: dict[str, str] | None,
    environment: str | None = None,
) -> SandboxCreateConfig:
    """Resolve the snapshot a new sandbox boots from, plus the environment's sizing.

    The run's environment (its selection, else ``default``) wins, then the repo's
    built snapshot, then the admin-configured base snapshot. Never raises: any
    failure resolves to ``None`` so sandbox creation falls back to the configured
    ``DEFAULT_SANDBOX_SNAPSHOT_ID``.

    The snapshot resolves to ``None`` outright on a provider that cannot boot
    from one: the ids stored here are only meaningful to the provider that
    captured them, and handing one to another provider is an error rather than
    a preference. Sizing and create params are passed through regardless; the
    provider rejects what it cannot honour.
    """
    environment_record = await resolve_environment(environment)
    resources = environment_sandbox_resources(environment_record)
    create_params = environment_sandbox_create_params(environment_record)
    snapshot_id = (
        await _resolve_snapshot_id(repo, environment_record)
        if sandbox_provider_supports_snapshots()
        else None
    )
    return SandboxCreateConfig(snapshot_id, resources, create_params)


async def _resolve_snapshot_id(
    repo: dict[str, str] | None, environment_record: dict[str, Any] | None
) -> str | None:
    environment_snapshot = environment_snapshot_id(environment_record)
    if environment_snapshot:
        return environment_snapshot
    if repo:
        try:
            repo_snapshot_id = await resolve_repo_snapshot_id(repo.get("owner"), repo.get("name"))
        except Exception:  # noqa: BLE001
            logger.debug("Failed to resolve repo-scoped snapshot", exc_info=True)
            repo_snapshot_id = None
        if repo_snapshot_id:
            return repo_snapshot_id
    return await get_admin_base_snapshot_id()


async def _create_sandbox_with_proxy(
    github_proxy_token: str | None = None,
    *,
    thread_id: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    repo: dict[str, str] | None = None,
    environment_slug: str | None = None,
) -> SandboxBackendProtocol:
    """Create a new sandbox with GitHub proxy auth configured."""
    config = await _resolve_sandbox_create_config(repo, environment_slug)
    sandbox_backend = await create_sandbox(
        snapshot_id=config.snapshot_id,
        resources=config.resources,
        create_params=config.create_params,
    )

    provider = current_sandbox_provider()
    if provider.uses_github_proxy and not await configure_proxy_for_sandbox(
        sandbox_backend,
        thread_id=thread_id,
        github_token=github_proxy_token,
        repositories=github_proxy_repositories,
        base_proxy_config=provider.proxy_config(config.create_params),
    ):
        msg = "Cannot configure proxy: GitHub App installation token is unavailable"
        logger.error(msg)
        raise ValueError(msg)

    return sandbox_backend


async def _refresh_github_proxy_or_fail(
    sandbox_backend: SandboxBackendProtocol,
    thread_id: str,
    github_proxy_token: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    base_proxy_config: Mapping[str, Any] | None = None,
) -> SandboxBackendProtocol:
    """Refresh proxy credentials; a sandbox we can't reconfigure is unreachable."""
    try:
        await configure_proxy_for_sandbox(
            sandbox_backend,
            thread_id=thread_id,
            github_token=github_proxy_token,
            repositories=github_proxy_repositories,
            base_proxy_config=base_proxy_config,
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


async def _configure_git_identity(sandbox_backend: SandboxBackendProtocol) -> None:
    await sandbox_backend.aexecute(
        f"git config --global user.name '{OPEN_SWE_BOT_NAME}' && "
        f"git config --global user.email '{OPEN_SWE_BOT_EMAIL}'",
    )


async def _connect_existing_sandbox(
    thread_id: str,
    *,
    cached: SandboxBackendProtocol | None,
    sandbox_id: str | None,
    github_proxy_token: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    base_proxy_config: Mapping[str, Any] | None = None,
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
            sandbox_backend = await create_sandbox(str(sandbox_id))
        except SandboxGoneError:
            raise
        except Exception as exc:
            logger.warning("Failed to connect to existing sandbox %s", sandbox_id)
            raise SandboxUnreachableError(thread_id, sandbox_id, str(exc)) from exc
    return await _refresh_github_proxy_or_fail(
        sandbox_backend,
        thread_id,
        github_proxy_token,
        github_proxy_repositories,
        base_proxy_config,
    )


def _sandbox_binding(
    sandbox_id: str, base_proxy_config: Mapping[str, Any] | None
) -> dict[str, Any]:
    """The thread metadata that binds it to a sandbox.

    The proxy config the sandbox was created with travels with the id: a later
    process that reconnects has no in-memory record of it, and refreshing the
    token without it would drop the sandbox's own proxy rules.
    """
    binding: dict[str, Any] = {"sandbox_id": sandbox_id}
    if base_proxy_config is not None:
        binding[SANDBOX_PROXY_CONFIG_METADATA_KEY] = dict(base_proxy_config)
    return binding


async def ensure_sandbox_for_thread(
    thread_id: str,
    *,
    github_proxy_token: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    repo: dict[str, str] | None = None,
    environment_slug: str | None = None,
    allow_replacement: bool = False,
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

    For LangSmith sandboxes, also refreshes the GitHub App proxy auth. When
    ``repo`` has a ``ready`` repo-scoped snapshot, newly created sandboxes boot
    from it; otherwise the base snapshot (admin setting, else
    ``DEFAULT_SANDBOX_SNAPSHOT_ID``) is used.
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
    sandbox_id = await get_sandbox_id_from_metadata(thread_id)
    metadata = await get_sandbox_metadata(thread_id) if sandbox_id is not None else {}
    recorded_proxy_config = metadata.get(SANDBOX_PROXY_CONFIG_METADATA_KEY)
    base_proxy_config = (
        recorded_proxy_config
        if isinstance(recorded_proxy_config, dict)
        else get_recorded_proxy_base_config(thread_id)
    )

    if sandbox_backend is None and sandbox_id is None:
        logger.info("Creating new sandbox for thread %s", thread_id)
        sandbox_backend = await _create_sandbox_with_proxy(
            github_proxy_token,
            thread_id=thread_id,
            github_proxy_repositories=github_proxy_repositories,
            repo=repo,
            environment_slug=environment_slug,
        )
        base_proxy_config = get_recorded_proxy_base_config(thread_id)
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
                    repo=repo,
                    environment_slug=environment_slug,
                )
                base_proxy_config = get_recorded_proxy_base_config(thread_id)
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

    await _configure_git_identity(sandbox_backend)

    # Bind the thread only once the sandbox is created and initialized: a run
    # that dies earlier leaves no id to reconnect to, so the next run creates
    # rather than adopting a half-built box.
    if sandbox_id != sandbox_backend.id:
        await client.threads.update(
            thread_id=thread_id,
            metadata=_sandbox_binding(sandbox_backend.id, base_proxy_config),
        )

    # Publishing last is what makes a failure above visible. Callers reach the
    # proxy's cached backend without awaiting the startup task that produced it,
    # so a backend published before this point would be used by the rest of the
    # run while the initialization that failed is only logged.
    return set_sandbox_backend(thread_id, sandbox_backend)


async def recreate_sandbox_for_thread(
    thread_id: str,
    *,
    repo: dict[str, str] | None = None,
    environment_slug: str | None = None,
) -> tuple[str, str]:
    """Bind a thread to a fresh sandbox while preserving its previous sandbox."""
    cached = SANDBOX_BACKENDS.get(thread_id)
    metadata_sandbox_id = await get_sandbox_id_from_metadata(thread_id)
    old_sandbox_id = cached.id if cached is not None and cached.has_backend else metadata_sandbox_id
    if not old_sandbox_id:
        raise ValueError(f"Thread {thread_id} has no sandbox to recreate")

    new_sandbox = await _create_sandbox_with_proxy(
        thread_id=thread_id,
        repo=repo,
        environment_slug=environment_slug,
    )
    if new_sandbox.id == old_sandbox_id:
        raise RuntimeError("Sandbox provider did not create a distinct sandbox")

    await _configure_git_identity(new_sandbox)
    await client.threads.update(
        thread_id=thread_id,
        metadata=_sandbox_binding(new_sandbox.id, get_recorded_proxy_base_config(thread_id)),
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
