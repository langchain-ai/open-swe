"""Trigger ingress authentication and run wrappers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.schema import Interrupt

from managed_deepagents._channels.types import ChannelError
from managed_deepagents._identity_auth import (
    AGENT_AUTH_PRINCIPAL_HEADER,
    LANGCHAIN_API_KEY_ENV,
    SOURCE_PROVIDER_HEADER,
    SOURCE_THREAD_HEADER,
    USER_HEADER,
)
from managed_deepagents._ingress import build_ingress_run_context
from managed_deepagents._ingress_runs import (
    observe_ingress_run,
    resume_ingress_run,
    start_ingress_run,
)
from managed_deepagents._loopback import resolve_loopback_api_url

if TYPE_CHECKING:
    from managed_deepagents._channels.binding import ChannelBinding
    from managed_deepagents._ingress_runs import StartedIngressRun


#: Terminal subset of ``langgraph_sdk.schema.RunStatus`` a joined run may report;
#: ``pending`` / ``running`` are rejected as unknown after ``runs.join``.
IngressRunStatus = Literal["error", "success", "timeout", "interrupted"]


class ObservedIngressRun(TypedDict):
    values: dict[str, Any]
    status: IngressRunStatus


class _IngressSource(TypedDict):
    provider: str
    thread_key: str
    user_id: str


#: One parked interrupt; ``value`` is what the app passed to ``interrupt()``.
PendingInterrupt = Interrupt


async def ingress_thread_interrupts(
    *,
    thread_id: str,
    source_provider: str,
    source_thread_key: str,
    user_id: str,
) -> list[PendingInterrupt]:
    """Parked interrupts on a thread. A thread that does not exist has none.

    Any other lookup failure propagates: reporting "nothing parked" would start a
    fresh turn and abandon the checkpoint the answer was meant to resume.
    """
    client = _create_trigger_ingress_loopback_client(
        {
            "provider": source_provider,
            "thread_key": source_thread_key,
            "user_id": user_id,
        }
    )
    try:
        thread = await client.threads.get(thread_id)
    except Exception as error:
        if _lookup_status(error) == HTTPStatus.NOT_FOUND:
            return []
        raise
    # The SDK types ``Thread.interrupts``; the checks below still guard the wire.
    grouped = thread.get("interrupts") if isinstance(thread, Mapping) else None
    if not isinstance(grouped, Mapping):
        return []
    pending: list[PendingInterrupt] = []
    seen: set[str] = set()
    # Keyed by task id; resume keys on the interrupt id alone.
    for entries in grouped.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            interrupt_id = entry.get("id")
            if isinstance(interrupt_id, str) and interrupt_id.strip() and interrupt_id not in seen:
                seen.add(interrupt_id)
                pending.append(Interrupt(id=interrupt_id, value=entry.get("value")))
    return pending


def _lookup_status(error: Exception) -> int | None:
    """HTTP status behind a failed loopback read.

    Duck-typed rather than matching ``httpx.HTTPStatusError``: ``httpx`` reaches
    this package only as a transitive dependency of the lazily imported client.
    """
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    status = getattr(error, "status_code", None)
    return status if isinstance(status, int) else None


async def start_ingress_agent(
    *,
    thread_id: str,
    assistant_id: str,
    messages: list[dict[str, object]],
    source_provider: str,
    source_thread_key: str,
    user_id: str,
    context: Mapping[str, object] | None = None,
    binding: ChannelBinding,
) -> StartedIngressRun:
    """Enqueue a Trigger run as its identity_context principal."""
    client = _create_trigger_ingress_loopback_client(
        {"provider": source_provider, "thread_key": source_thread_key, "user_id": user_id}
    )
    return await start_ingress_run(
        client=client,
        thread_id=thread_id,
        assistant_id=assistant_id,
        messages=messages,
        **build_ingress_run_context(binding=binding, context=context),
    )


async def resume_ingress_agent(
    *,
    thread_id: str,
    assistant_id: str,
    resume: dict[str, object],
    source_provider: str,
    source_thread_key: str,
    user_id: str,
    context: Mapping[str, object] | None = None,
    binding: ChannelBinding,
) -> StartedIngressRun:
    """Resume a Trigger run as its identity_context principal."""
    client = _create_trigger_ingress_loopback_client(
        {"provider": source_provider, "thread_key": source_thread_key, "user_id": user_id}
    )
    return await resume_ingress_run(
        client=client,
        thread_id=thread_id,
        assistant_id=assistant_id,
        resume=resume,
        **build_ingress_run_context(binding=binding, context=context),
    )


async def observe_ingress_agent(
    *,
    thread_id: str,
    run_id: str,
    source_provider: str,
    source_thread_key: str,
    user_id: str,
) -> ObservedIngressRun:
    """Join an accepted Trigger run as the principal that owns its thread."""
    client = _create_trigger_ingress_loopback_client(
        {"provider": source_provider, "thread_key": source_thread_key, "user_id": user_id}
    )
    observed = await observe_ingress_run(client=client, thread_id=thread_id, run_id=run_id)
    status = observed["status"]
    if not isinstance(status, str) or status not in {"error", "success", "timeout", "interrupted"}:
        raise ChannelError("channel_run_failed", "Agent Server returned an unknown run status")
    return {"values": observed["values"], "status": status}


def _create_trigger_ingress_loopback_client(
    source: _IngressSource,
) -> LangGraphClient:
    api_key = os.environ.get(LANGCHAIN_API_KEY_ENV, "").strip()
    if not api_key:
        raise ChannelError(
            "channel_run_failed",
            "managed loopback authentication is not configured",
        )
    user_id = source["user_id"].strip()
    if not user_id:
        raise ChannelError(
            "channel_run_failed",
            "channel run requires an identity_context principal",
        )
    headers = {
        USER_HEADER: user_id,
        AGENT_AUTH_PRINCIPAL_HEADER: user_id,
        SOURCE_PROVIDER_HEADER: source["provider"],
        SOURCE_THREAD_HEADER: source["thread_key"],
    }
    return get_client(
        url=resolve_loopback_api_url(),
        api_key=api_key,
        headers=headers,
    )
