"""Get-or-create lifecycle for the sandbox bound to a thread.

Creation, reconnection, credential install, git identity, and the reset/recreate
rebinds. What a new sandbox boots from and which credentials it gets are
injected: the registry itself lives in ``state``.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from deepagents.backends.protocol import SandboxBackendProtocol
from langgraph_sdk import get_client

from coding_agent.config import ENV
from coding_agent.sandboxes.credentials import SandboxCredentials
from coding_agent.sandboxes.git_identity import GitIdentity
from coding_agent.sandboxes.providers.langsmith import (
    create_langsmith_sandbox_from_params,
    get_sandbox_proxy_config,
)
from coding_agent.sandboxes.providers.registry import SandboxGoneError, create_sandbox
from coding_agent.sandboxes.resources import SandboxResources
from coding_agent.sandboxes.state import (
    SANDBOX_BACKENDS,
    SandboxBackendProxy,
    SandboxUnreachableError,
    get_or_create_sandbox_backend_proxy,
    get_sandbox_id_from_metadata,
    get_sandbox_metadata,
    set_sandbox_backend,
    unwrap_sandbox_backend,
)
from coding_agent.utils.startup_trace import aphase

logger = logging.getLogger(__name__)

client = get_client()

_SANDBOX_PROXY_CONFIG_METADATA_KEY = "sandbox_base_proxy_config"


@dataclass(frozen=True, slots=True)
class SandboxCreateConfig:
    """What a new sandbox boots from: snapshot, VM sizing, provider create params."""

    snapshot_id: str | None
    resources: SandboxResources = field(default_factory=SandboxResources)
    create_params: dict[str, Any] = field(default_factory=dict)

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


type CreateConfigResolver = Callable[[str | None], Awaitable[SandboxCreateConfig]]


class SandboxLifecycle:
    """A platform's sandboxes: what they boot from, and what credentials they carry."""

    def __init__(
        self,
        *,
        resolve_create_config: CreateConfigResolver,
        credentials: SandboxCredentials,
        git_identity: GitIdentity,
    ) -> None:
        self._resolve_create_config = resolve_create_config
        self._credentials = credentials
        self._git_identity = git_identity

    def with_credentials(self, credentials: SandboxCredentials) -> SandboxLifecycle:
        """The same lifecycle, installing ``credentials`` instead of the default ones."""
        return SandboxLifecycle(
            resolve_create_config=self._resolve_create_config,
            credentials=credentials,
            git_identity=self._git_identity,
        )

    async def _create(
        self,
        *,
        thread_id: str | None = None,
        environment_slug: str | None = None,
    ) -> tuple[SandboxBackendProtocol, dict[str, Any] | None]:
        """Create a new sandbox with credentials installed and the identity written."""
        async with aphase(thread_id, "sandbox.resolve_snapshot"):
            config = await self._resolve_create_config(environment_slug)
        async with aphase(thread_id, "sandbox.boot", snapshot_id=config.snapshot_id):
            sandbox_backend = await config.boot()

        async with self._git_identity.applied(thread_id, sandbox_backend):
            installed = await self._credentials.install(
                sandbox_backend.id,
                thread_id=thread_id,
                base_proxy_config=config.proxy_config,
            )
            await installed.bind(thread_id)

        return sandbox_backend, installed.base_config

    async def _connect(
        self,
        thread_id: str,
        *,
        cached: SandboxBackendProtocol | None,
        sandbox_id: str | None,
        base_proxy_config: dict[str, Any] | None = None,
    ) -> SandboxBackendProtocol:
        """Reuse the sandbox already bound to ``thread_id``, or fail unreachable.

        A ``SandboxGoneError`` propagates untouched so the caller recreates. Nothing
        pings the box first: reinstalling credentials below has to reach it anyway,
        and raises the same unreachable error when it cannot.
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

        async with self._git_identity.applied(thread_id, sandbox_backend):
            try:
                installed = await self._credentials.install(
                    unwrap_sandbox_backend(sandbox_backend).id,
                    thread_id=thread_id,
                    base_proxy_config=base_proxy_config,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to reinstall credentials on sandbox %s for thread %s",
                    sandbox_backend.id,
                    thread_id,
                    exc_info=True,
                )
                raise SandboxUnreachableError(thread_id, sandbox_backend.id, str(exc)) from exc
            await installed.bind(thread_id)

        return sandbox_backend

    async def ensure_for_thread(
        self,
        thread_id: str,
        *,
        environment_slug: str | None = None,
        allow_replacement: bool = False,
    ) -> SandboxBackendProtocol:
        """Get-or-create a healthy sandbox bound to ``thread_id``.

        Three cases (dispatch uses ``multitask_strategy="interrupt"``, so a thread
        never provisions two sandboxes concurrently — no cross-process sentinel is
        needed):

        1. Cached in memory -> reinstall credentials.
        2. Metadata has an id -> reconnect, then reinstall credentials.
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

        Newly created sandboxes boot from whatever the injected resolver picks.
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
            thread_metadata = (
                await get_sandbox_metadata(thread_id) if sandbox_id is not None else {}
            )
        recorded_proxy_config = thread_metadata.get(_SANDBOX_PROXY_CONFIG_METADATA_KEY)
        base_proxy_config = (
            recorded_proxy_config
            if isinstance(recorded_proxy_config, dict)
            else await self._credentials.recorded_base_config(thread_id)
        )
        created_proxy_config: dict[str, Any] | None = None

        if sandbox_backend is None and sandbox_id is None:
            logger.info("Creating new sandbox for thread %s", thread_id)
            sandbox_backend, created_proxy_config = await self._create(
                thread_id=thread_id,
                environment_slug=environment_slug,
            )
            logger.info("Sandbox created: %s", sandbox_backend.id)
        else:
            try:
                sandbox_backend = await self._connect(
                    thread_id,
                    cached=sandbox_backend,
                    sandbox_id=sandbox_id,
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
                    sandbox_backend, created_proxy_config = await self._create(
                        thread_id=thread_id,
                        environment_slug=environment_slug,
                    )
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

    async def reset_for_thread(
        self,
        thread_id: str,
        create_params: dict[str, Any],
    ) -> tuple[str, str]:
        """Bind a thread to a fresh sandbox created from raw provider options."""
        if ENV.SANDBOX_TYPE.get() != "langsmith":
            raise ValueError("sandbox_reset is only supported by the LangSmith sandbox provider")

        cached = SANDBOX_BACKENDS.get(thread_id)
        metadata_sandbox_id = await get_sandbox_id_from_metadata(thread_id)
        old_sandbox_id = (
            cached.id if cached is not None and cached.has_backend else metadata_sandbox_id
        )
        if not old_sandbox_id:
            raise ValueError(f"Thread {thread_id} has no sandbox to reset")

        new_sandbox = await create_langsmith_sandbox_from_params(create_params)
        if new_sandbox.id == old_sandbox_id:
            raise RuntimeError("Sandbox provider did not create a distinct sandbox")

        proxy_config = get_sandbox_proxy_config(create_params)
        installed = await self._credentials.install(
            new_sandbox.id,
            thread_id=thread_id,
            base_proxy_config=proxy_config,
        )
        await self._git_identity.apply(new_sandbox)
        sandbox_metadata: dict[str, Any] = {
            "sandbox_id": new_sandbox.id,
            _SANDBOX_PROXY_CONFIG_METADATA_KEY: proxy_config,
        }
        await client.threads.update(thread_id=thread_id, metadata=sandbox_metadata)
        set_sandbox_backend(thread_id, new_sandbox)
        await installed.bind(thread_id)
        logger.info(
            "Reset thread %s from sandbox %s to sandbox %s",
            thread_id,
            old_sandbox_id,
            new_sandbox.id,
        )
        return old_sandbox_id, new_sandbox.id

    async def recreate_for_thread(
        self,
        thread_id: str,
        *,
        environment_slug: str | None = None,
    ) -> tuple[str, str]:
        """Bind a thread to a fresh sandbox while preserving its previous sandbox."""
        cached = SANDBOX_BACKENDS.get(thread_id)
        metadata_sandbox_id = await get_sandbox_id_from_metadata(thread_id)
        old_sandbox_id = (
            cached.id if cached is not None and cached.has_backend else metadata_sandbox_id
        )
        if not old_sandbox_id:
            raise ValueError(f"Thread {thread_id} has no sandbox to recreate")

        new_sandbox, _created_proxy_config = await self._create(
            thread_id=thread_id,
            environment_slug=environment_slug,
        )
        if new_sandbox.id == old_sandbox_id:
            raise RuntimeError("Sandbox provider did not create a distinct sandbox")

        await self._git_identity.apply(new_sandbox)
        sandbox_metadata: dict[str, Any] = {"sandbox_id": new_sandbox.id}
        base_proxy_config = await self._credentials.recorded_base_config(thread_id)
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

    def cached_backend(
        self,
        thread_id: str,
        *,
        reconnect: Callable[[], Awaitable[SandboxBackendProtocol]] | None = None,
    ) -> SandboxBackendProxy:
        return get_or_create_sandbox_backend_proxy(thread_id, reconnect=reconnect)
