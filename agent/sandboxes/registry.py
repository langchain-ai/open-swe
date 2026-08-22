"""Which sandbox handle belongs to which thread, for this process.

The registry is a cache, not the source of truth: the thread's ``sandbox_id``
metadata is, and a handle that has lost its backend reconnects from it. Server,
middleware and tools all reach a thread's sandbox through here.
"""

import logging
from collections.abc import Callable
from typing import Any

from deepagents.backends.protocol import SandboxBackendProtocol
from langgraph.config import get_config
from langgraph_sdk import get_client

from .providers import create_sandbox
from .proxy import Reconnect, SandboxBackendProxy

logger = logging.getLogger(__name__)

# Thread ID -> stable SandboxBackendProxy, shared between the graphs and middleware.
SANDBOX_BACKENDS: dict[str, SandboxBackendProxy] = {}


async def get_sandbox_metadata(thread_id: str) -> dict[str, Any]:
    """The thread metadata its sandbox binding lives in.

    The run config's inline copy is used when it already names a sandbox; it
    otherwise falls back to the live thread, which a previous run may have bound
    since this one was scheduled.
    """
    try:
        config = get_config()
        metadata = config.get("metadata", {})
        if isinstance(metadata, dict) and isinstance(metadata.get("sandbox_id"), str):
            return metadata
    except Exception:
        logger.debug(
            "Failed to read inline thread metadata for sandbox; falling back to live lookup",
            exc_info=True,
        )

    try:
        client = get_client()
        thread = await client.threads.get(thread_id)
    except Exception:
        logger.exception("Failed to fetch live thread metadata for sandbox")
        return {}

    metadata = thread.get("metadata", {}) if isinstance(thread, dict) else {}
    return metadata if isinstance(metadata, dict) else {}


async def get_sandbox_id_from_metadata(thread_id: str) -> str | None:
    """Fetch sandbox_id from thread metadata."""
    sandbox_id = (await get_sandbox_metadata(thread_id)).get("sandbox_id")
    return sandbox_id if isinstance(sandbox_id, str) else None


def _reconnect_from_metadata(thread_id: str) -> Reconnect:
    """The reconnect a handle falls back on when no run registered a richer one."""

    async def reconnect() -> SandboxBackendProtocol:
        sandbox_id = await get_sandbox_id_from_metadata(thread_id)
        if not sandbox_id:
            raise ValueError(f"Missing sandbox_id in thread metadata for {thread_id}")
        logger.info("Reconnecting sandbox backend for thread %s from metadata", thread_id)
        return await create_sandbox(sandbox_id)

    return reconnect


def _publisher(thread_id: str) -> Callable[[SandboxBackendProxy], None]:
    def publish(proxy: SandboxBackendProxy) -> None:
        SANDBOX_BACKENDS[thread_id] = proxy

    return publish


def set_sandbox_backend(
    thread_id: str,
    sandbox_backend: SandboxBackendProtocol,
) -> SandboxBackendProxy:
    if isinstance(sandbox_backend, SandboxBackendProxy):
        SANDBOX_BACKENDS[thread_id] = sandbox_backend
        return sandbox_backend

    proxy = SANDBOX_BACKENDS.get(thread_id)
    if proxy is not None:
        proxy.replace_backend(sandbox_backend)
        return proxy

    proxy = _new_proxy(thread_id, backend=sandbox_backend)
    SANDBOX_BACKENDS[thread_id] = proxy
    return proxy


def get_or_create_sandbox_backend_proxy(
    thread_id: str,
    *,
    reconnect: Reconnect | None = None,
) -> SandboxBackendProxy:
    proxy = SANDBOX_BACKENDS.get(thread_id)
    if proxy is not None:
        # Callers that only want the handle pass no callback; keep the one the
        # run registered rather than dropping it to the metadata fallback.
        if reconnect is not None:
            proxy.set_reconnect(reconnect)
        return proxy

    proxy = _new_proxy(thread_id, reconnect=reconnect)
    SANDBOX_BACKENDS[thread_id] = proxy
    return proxy


def clear_sandbox_backend(thread_id: str) -> None:
    proxy = SANDBOX_BACKENDS.pop(thread_id, None)
    if proxy is not None:
        proxy.cancel_startup()


async def get_sandbox_backend(thread_id: str) -> SandboxBackendProxy:
    """The thread's handle, connected: reconnects from metadata if it has no backend."""
    proxy = get_or_create_sandbox_backend_proxy(thread_id)
    await proxy.ready()
    return proxy


def _new_proxy(
    thread_id: str,
    *,
    backend: SandboxBackendProtocol | None = None,
    reconnect: Reconnect | None = None,
) -> SandboxBackendProxy:
    return SandboxBackendProxy(
        backend,
        thread_id=thread_id,
        reconnect=reconnect or _reconnect_from_metadata(thread_id),
        publish=_publisher(thread_id),
    )
