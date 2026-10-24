"""HTTP channel run payloads: starting, observing and cancelling a channel's runs."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict, cast

from managed_deepagents._channels.types import ChannelError
from managed_deepagents._ingress import (
    _create_ingress_loopback_client,
    _has_loopback_credential,
    build_ingress_run_context,
)
from managed_deepagents._ingress_runs import (
    DELIVERY_KEY_METADATA,
    StartedIngressRun,
    cancel_ingress_runs,
    ingress_thread_state,
    lookup_status,
    observe_ingress_run,
    record_thread_delivery,
    start_ingress_run,
    wait_for_idle_thread,
)

if TYPE_CHECKING:
    from langgraph_sdk.client import LangGraphClient
    from langgraph_sdk.schema import Interrupt, MultitaskStrategy

    from managed_deepagents._channels.binding import ChannelBinding
    from managed_deepagents._channels.http.types import HttpRunStatus
    from managed_deepagents._channels.run_input import NormalizedRunInput
    from managed_deepagents._channels.types import JsonValue, MessageContent

_LOG = logging.getLogger("mda")


class HttpIngressOutcome(TypedDict):
    """How a channel run ended, with LangGraph's run status; the run posts its
    own answer."""

    status: HttpRunStatus
    interrupts: list[Interrupt]
    """What the thread waits on once the run stopped on interrupts."""
    assistant_id: NotRequired[str]


class SkippedIngressRun(TypedDict):
    """Why a thread took no run: ``ignored`` when there is nothing to start."""

    skipped: Literal["paused", "duplicate", "busy", "rejected", "ignored"]
    assistant_id: NotRequired[str]
    """The paused thread's assistant, for links into LangSmith Studio."""
    interrupts: NotRequired[list[Interrupt]]
    """What the paused thread waits on."""


PrepareInput = Callable[[], Awaitable["NormalizedRunInput | None"]]
"""What a new run starts with when there is no content, asked only then."""


async def wait_for_http_ingress_thread(
    *, user_id: str, source_thread_key: str, source_provider: str, thread_id: str
) -> None:
    """Wait until the thread's queued and running runs have finished."""
    client = _create_ingress_loopback_client(
        user_id=user_id,
        source_thread_key=source_thread_key,
        source_provider=source_provider,
    )
    await wait_for_idle_thread(client=client, thread_id=thread_id)


class ThreadWaitsOn(TypedDict):
    """What a thread waits on now, and its assistant, for links into LangSmith Studio."""

    interrupts: list[Interrupt]
    assistant_id: NotRequired[str]


async def http_ingress_thread_waits_on(
    *, user_id: str, source_thread_key: str, source_provider: str, thread_id: str
) -> ThreadWaitsOn:
    """What the thread waits on now."""
    client = _create_ingress_loopback_client(
        user_id=user_id,
        source_thread_key=source_thread_key,
        source_provider=source_provider,
    )
    read = await ingress_thread_state(client=client, thread_id=thread_id)
    waits_on: ThreadWaitsOn = {"interrupts": read["interrupts"]}
    if "assistant_id" in read:
        waits_on["assistant_id"] = read["assistant_id"]
    return waits_on


_RUN_STATUSES: frozenset[str] = frozenset({"success", "error", "timeout", "interrupted"})


async def cancel_http_ingress_runs(
    *, user_id: str, source_thread_key: str, source_provider: str, thread_id: str
) -> list[str]:
    """Cancel the thread's queued and running runs, keeping their progress.

    Returns the IDs of the runs it cancelled.
    """
    if not _has_loopback_credential():
        raise ChannelError(
            "channel_run_failed", "managed loopback authentication is not configured"
        )
    client = _create_ingress_loopback_client(
        user_id=user_id,
        source_thread_key=source_thread_key,
        source_provider=source_provider,
    )
    return await cancel_ingress_runs(client=client, thread_id=thread_id)


async def start_http_ingress_agent(
    *,
    user_id: str,
    source_thread_key: str,
    binding: ChannelBinding,
    thread_id: str,
    assistant_id: str,
    source_provider: str,
    input: MessageContent | None = None,
    prepare: PrepareInput | None = None,
    delivery_key: str | None = None,
    multitask_strategy: MultitaskStrategy = "enqueue",
) -> StartedIngressRun | SkippedIngressRun:
    if not _has_loopback_credential():
        raise ChannelError(
            "channel_run_failed", "managed loopback authentication is not configured"
        )
    client = _create_ingress_loopback_client(
        user_id=user_id,
        source_thread_key=source_thread_key,
        source_provider=source_provider,
    )
    read = await ingress_thread_state(client=client, thread_id=thread_id, delivery_key=delivery_key)
    state = read["state"]
    if state == "paused":
        # A new turn would abandon the decision the thread waits on.
        paused: SkippedIngressRun = {"skipped": "paused", "interrupts": read["interrupts"]}
        if "assistant_id" in read:
            paused["assistant_id"] = read["assistant_id"]
        return paused
    skipped = _no_new_turn(state, multitask_strategy)
    if skipped is not None:
        return skipped
    prepared, started_binding = await _turn_input(input, prepare, binding)
    if prepared is None:
        return {"skipped": "ignored"}
    # The handler's metadata goes under MDA's own.
    metadata: dict[str, JsonValue] = {**prepared.get("metadata", {})}
    if delivery_key is not None:
        metadata[DELIVERY_KEY_METADATA] = delivery_key
    try:
        started = await start_ingress_run(
            client=client,
            thread_id=thread_id,
            assistant_id=assistant_id,
            messages=prepared["messages"],
            state=prepared["state"] or None,
            metadata=metadata or None,
            webhook=prepared.get("webhook"),
            multitask_strategy=multitask_strategy,
            **build_ingress_run_context(binding=started_binding, context=prepared.get("context")),
        )
    except Exception as error:
        # A run started meanwhile: `reject` leaves the thread to it.
        if multitask_strategy != "reject" or lookup_status(error) != HTTPStatus.CONFLICT:
            raise
        return {"skipped": "rejected"}
    await _record_delivery(client, thread_id, delivery_key, multitask_strategy)
    return {"thread_id": started["thread_id"], "run_id": started["run_id"]}


async def _record_delivery(
    client: LangGraphClient,
    thread_id: str,
    delivery_key: str | None,
    multitask_strategy: MultitaskStrategy,
) -> None:
    """Under ``rollback``, record a started run's delivery on its thread.

    A later message deletes the run with its delivery key, but the thread keeps
    the key, so a redelivery stays a duplicate.
    """
    if delivery_key is None or multitask_strategy != "rollback":
        return
    try:
        await record_thread_delivery(client=client, thread_id=thread_id, delivery_key=delivery_key)
    except Exception as error:  # noqa: BLE001
        # The run started; only a redelivery after its rollback would repeat it.
        _LOG.warning(
            "channel delivery was not recorded on its thread %s",
            json.dumps({"threadId": thread_id, "status": lookup_status(error)}),
        )


async def _turn_input(
    input: MessageContent | None,
    prepare: PrepareInput | None,
    binding: ChannelBinding,
) -> tuple[NormalizedRunInput | None, ChannelBinding]:
    """What a new turn starts with, and the binding that describes it."""
    if input is not None:
        prepared: NormalizedRunInput | None = {
            "messages": [cast("dict[str, JsonValue]", normalize_http_message(input))],
            "state": {},
        }
        # The binding already holds a message's content.
        started_binding = binding
    else:
        prepared = None if prepare is None else await prepare()
        started_binding = binding
        if prepared is not None:
            started_binding = cast(
                "ChannelBinding",
                {
                    **binding,
                    "event": {"type": "message", "messages": list(prepared["messages"])},
                },
            )
    return prepared, started_binding


def _no_new_turn(state: str, multitask_strategy: MultitaskStrategy) -> SkippedIngressRun | None:
    """Why the thread takes no new turn, or ``None`` when it takes one.

    A busy thread takes one when the strategy cancels its run (``interrupt``
    keeps the run's progress, ``rollback`` discards it); ``reject`` drops it,
    and ``enqueue`` waits for the run.
    """
    if state == "ready":
        return None
    if state == "busy" and multitask_strategy == "reject":
        return {"skipped": "rejected"}
    if state == "busy" and multitask_strategy in ("interrupt", "rollback"):
        return None
    return {"skipped": cast("Literal['paused', 'duplicate', 'busy']", state)}


async def observe_http_ingress_agent(
    *,
    user_id: str,
    source_thread_key: str,
    source_provider: str,
    thread_id: str,
    run_id: str,
) -> HttpIngressOutcome | None:
    """How the run ended, or ``None`` when it hasn't, as observing it stopped while
    it was still ``pending`` or ``running``: it may still answer."""
    client = _create_ingress_loopback_client(
        user_id=user_id,
        source_thread_key=source_thread_key,
        source_provider=source_provider,
    )
    try:
        observed = await observe_ingress_run(client=client, thread_id=thread_id, run_id=run_id)
    except Exception as error:
        # `rollback` deletes the run a newer message replaced; it has no reply.
        if lookup_status(error) != HTTPStatus.NOT_FOUND:
            raise
        _LOG.info("channel run was replaced %s", json.dumps({"threadId": thread_id}))
        # It was cancelled; what the thread waits on now is the newer run's.
        return _waiting_on(
            "interrupted", await ingress_thread_state(client=client, thread_id=thread_id)
        )
    status = observed["status"]
    if status not in _RUN_STATUSES:
        _LOG.warning(
            "channel run had not ended when observed %s",
            json.dumps({"threadId": thread_id, "status": status}),
        )
        return None
    ended = cast("HttpRunStatus", status)
    values = observed["values"]
    state = values if isinstance(values, Mapping) else {}
    raised = state.get("__interrupt__")
    # A run that stops on `interrupt()` can still end `success`; a cancelled
    # run ends `interrupted` with nothing pending.
    if ended != "interrupted" and not (isinstance(raised, (list, Mapping)) and raised):
        return {"status": ended, "interrupts": []}
    return _waiting_on(ended, await ingress_thread_state(client=client, thread_id=thread_id))


def _waiting_on(status: HttpRunStatus, read: Mapping[str, Any]) -> HttpIngressOutcome:
    """The run's outcome, with what its thread waits on now."""
    outcome: HttpIngressOutcome = {"status": status, "interrupts": read["interrupts"]}
    if "assistant_id" in read:
        outcome["assistant_id"] = read["assistant_id"]
    return outcome


def normalize_http_message(content: MessageContent) -> dict[str, object]:
    return {
        "role": "human",
        "content": content,
        "response_metadata": {} if isinstance(content, str) else {"output_version": "v1"},
    }
