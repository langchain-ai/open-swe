"""The thread's record of the LangSmith sandboxes that hold its work.

A thread and its sandbox are one unit. MDA records each sandbox it creates or
adopts for a thread in the thread's ``ls_mda_sandbox_binding`` metadata. When a
bound sandbox is gone, or another sandbox now holds its name, the open fails
with ``SandboxMissingError``: MDA never creates a replacement.

The record is keyed by sandbox name, because one thread can hold one sandbox
per set of per-user mounts. A thread without a record is new, so MDA creates
its sandbox.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime

import httpx

from managed_deepagents._log import logger

SANDBOX_BINDING_METADATA = "ls_mda_sandbox_binding"
_BINDING_VERSION = 1
#: How long a sandbox open waits on the Agent Server for the thread's record.
_THREAD_API_TIMEOUT_S = 30.0


def bound_sandboxes(metadata: object) -> dict[str, str | None]:
    """The sandboxes a thread's metadata binds it to, as ``{name: id}``."""
    if not isinstance(metadata, Mapping):
        return {}
    binding = metadata.get(SANDBOX_BINDING_METADATA)
    sandboxes = binding.get("sandboxes") if isinstance(binding, Mapping) else None
    if not isinstance(sandboxes, Mapping):
        return {}
    bound: dict[str, str | None] = {}
    for name, entry in sandboxes.items():
        if not isinstance(name, str) or not name:
            continue
        sandbox_id = entry.get("id") if isinstance(entry, Mapping) else None
        bound[name] = sandbox_id if isinstance(sandbox_id, str) and sandbox_id else None
    return bound


def binding_with(metadata: object, name: str, sandbox_id: str | None) -> dict[str, object]:
    """The thread's binding metadata with ``name`` bound to ``sandbox_id``."""
    binding = metadata.get(SANDBOX_BINDING_METADATA) if isinstance(metadata, Mapping) else None
    sandboxes = binding.get("sandboxes") if isinstance(binding, Mapping) else None
    merged = dict(sandboxes) if isinstance(sandboxes, Mapping) else {}
    merged[name] = {"id": sandbox_id, "bound_at": datetime.now(UTC).isoformat()}
    return {"v": _BINDING_VERSION, "sandboxes": merged}


def _thread_request(thread_id: str) -> tuple[str, dict[str, str]]:
    """The thread's URL and the headers of a sandbox binding grant for it.

    The grant reads and writes the thread whoever owns it, because the run's
    caller may not own it. Raises ``ValueError`` without an authenticated Agent Server.
    """
    from managed_deepagents._schedule_auth import (  # noqa: PLC0415
        SANDBOX_BINDING_HEADER,
        _handoff_request_config,
    )

    base, headers = _handoff_request_config(SANDBOX_BINDING_HEADER, {"thread": thread_id})
    return f"{base}/threads/{thread_id}", headers


def _thread_client() -> httpx.Client:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return httpx.Client(timeout=_THREAD_API_TIMEOUT_S, follow_redirects=False)
    # A blocking call to the Agent Server from its own event loop can't be served.
    raise RuntimeError("open the sandbox with the async API inside an event loop")


def _get_thread(client: httpx.Client, url: str, headers: Mapping[str, str]) -> object:
    response = client.get(url, headers=headers)
    response.raise_for_status()
    return response.json()


def _read_error(thread_id: str, error: Exception) -> RuntimeError:
    return RuntimeError(
        f"Could not read thread {thread_id}'s sandbox binding; retry the run "
        f"({logger.safe_error_label(error)})"
    )


def read_bound_sandboxes(thread_id: str, *, deployed: bool) -> dict[str, str | None] | None:
    """The thread's bound sandboxes; ``None`` outside a deployed Agent Server.

    A failed read raises: without the record, MDA can't tell a new thread from
    one whose sandbox is gone.
    """
    try:
        url, headers = _thread_request(thread_id)
    except ValueError as error:
        if not deployed:
            return None
        raise _read_error(thread_id, error) from error
    try:
        with _thread_client() as client:
            thread = _get_thread(client, url, headers)
    except Exception as error:
        raise _read_error(thread_id, error) from error
    return bound_sandboxes(thread.get("metadata") if isinstance(thread, Mapping) else None)


def record_bound_sandbox(
    thread_id: str,
    name: str,
    sandbox_id: str | None,
    bound: Mapping[str, str | None],
) -> None:
    """Bind the thread to the sandbox, unless it already is.

    A failed write raises: an unbound thread could later get a new sandbox.
    """
    if name in bound and bound[name] == sandbox_id:
        return
    try:
        url, headers = _thread_request(thread_id)
        with _thread_client() as client:
            thread = _get_thread(client, url, headers)
            metadata = thread.get("metadata") if isinstance(thread, Mapping) else None
            binding = binding_with(metadata, name, sandbox_id)
            client.patch(
                url, headers=headers, json={"metadata": {SANDBOX_BINDING_METADATA: binding}}
            ).raise_for_status()
    except Exception as error:
        raise RuntimeError(
            f"Could not bind thread {thread_id} to sandbox {name}; retry the run "
            f"({logger.safe_error_label(error)})"
        ) from error


__all__ = [
    "SANDBOX_BINDING_METADATA",
    "binding_with",
    "bound_sandboxes",
    "read_bound_sandboxes",
    "record_bound_sandbox",
]
