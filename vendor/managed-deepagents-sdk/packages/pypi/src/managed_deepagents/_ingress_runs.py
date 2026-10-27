"""Transport-neutral creation and observation of managed ingress runs.

Internal transports supply an authenticated client and normalized payloads here.
Each transport owns authentication, terminal-status policy, and reply extraction.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict, cast, get_args

from langgraph_sdk.schema import (
    Command,
    Config,
    Context,
    Interrupt,
    MultitaskStrategy,
    RunStatus,
)

from managed_deepagents._channels.types import ChannelError
from managed_deepagents._telemetry.otel import (
    TRACE_ID_METADATA_KEY,
    current_log_context,
    set_log_attributes,
)
from managed_deepagents._thread_record import (
    next_thread_record,
    parse_thread_record,
    thread_record_metadata,
)

if TYPE_CHECKING:
    from langgraph_sdk.client import LangGraphClient

    from managed_deepagents._channels.binding import ChannelBinding
    from managed_deepagents._thread_record import ThreadRecord


class StartedIngressRun(TypedDict):
    thread_id: str
    run_id: str


class IngressThreadOrigin(TypedDict):
    """The channel message a run starts from, for the thread record."""

    binding: ChannelBinding
    #: The sender's principal id, as the run's ``x-mda-user-id``.
    sender: str


class IngressRunObservation(TypedDict):
    values: dict[str, Any]
    #: ``None`` when the server omitted a status; each transport validates it.
    status: RunStatus | None


class _RunOptions(TypedDict, total=False):
    """Optional ``runs.create`` keywords, sent only when the caller set them."""

    context: Context
    config: Config
    metadata: dict[str, Any]


def _run_options(
    context: Context | None,
    config: Config | None,
    metadata: Mapping[str, Any] | None = None,
) -> _RunOptions:
    options: _RunOptions = {}
    if context is not None:
        options["context"] = context
    if config is not None:
        options["config"] = config
    if metadata is not None:
        options["metadata"] = dict(metadata)
    log_context = current_log_context()
    if log_context is not None:
        options["metadata"] = {
            **options.get("metadata", {}),
            TRACE_ID_METADATA_KEY: log_context.trace_id,
        }
    return options


DELIVERY_KEY_METADATA = "mda_delivery_key"
"""Run metadata naming the provider delivery that started the run."""
DELIVERY_KEYS_METADATA = "mda_delivery_keys"
"""Thread metadata listing the thread's latest deliveries.

``rollback`` deletes the runs it cancels, with their metadata, but keeps the
thread's.
"""
_DELIVERY_LOOKBACK_RUNS = 100
"""How many of a thread's latest runs are checked for a repeated delivery."""


def _thread_delivery_keys(metadata: object) -> list[str]:
    """The delivery keys recorded on a thread."""
    keys = metadata.get(DELIVERY_KEYS_METADATA) if isinstance(metadata, Mapping) else None
    if not isinstance(keys, list):
        return []
    return [key for key in cast("list[object]", keys) if isinstance(key, str)]


async def record_thread_delivery(
    *, client: LangGraphClient, thread_id: str, delivery_key: str
) -> None:
    """Record a delivery on its thread.

    A redelivery then stays a duplicate after ``rollback`` deleted the run it
    started. Keeps the latest ``_DELIVERY_LOOKBACK_RUNS`` keys.
    """
    thread = await client.threads.get(thread_id)
    record = thread if isinstance(thread, Mapping) else {}
    keys = [key for key in _thread_delivery_keys(record.get("metadata")) if key != delivery_key]
    keys.append(delivery_key)
    await client.threads.update(
        thread_id, metadata={DELIVERY_KEYS_METADATA: keys[-_DELIVERY_LOOKBACK_RUNS:]}
    )


class IngressThreadState(TypedDict):
    """Whether a thread takes a new run, read from the thread once.

    ``state`` is ``paused`` while it waits on an interrupt, so a new turn would
    abandon the pending decision; ``duplicate`` when a recent run came from the
    same provider delivery; ``busy`` while a run is active, because that run may
    still stop for a decision. A missing thread is ``ready``.
    """

    state: Literal["ready", "paused", "duplicate", "busy"]
    #: The assistant that last ran on it, from its metadata.
    assistant_id: NotRequired[str]
    #: Its pending interrupts, as LangGraph lists them: non-empty while it is
    #: ``paused``.
    interrupts: list[Interrupt]


async def ingress_thread_state(
    *, client: LangGraphClient, thread_id: str, delivery_key: str | None = None
) -> IngressThreadState:
    """Read whether a thread takes a new run."""
    try:
        thread = await client.threads.get(thread_id)
    except Exception as error:
        if lookup_status(error) == HTTPStatus.NOT_FOUND:
            return {"state": "ready", "interrupts": []}
        raise
    record = thread if isinstance(thread, Mapping) else {}
    by_task = record.get("interrupts")
    interrupts = [
        cast("Interrupt", entry)
        for entries in (
            cast("Mapping[str, object]", by_task).values() if isinstance(by_task, Mapping) else ()
        )
        if isinstance(entries, list)
        for entry in cast("list[object]", entries)
        if isinstance(entry, Mapping)
    ]
    read: IngressThreadState = {"state": "ready", "interrupts": interrupts}
    metadata = record.get("metadata")
    assistant_id = metadata.get("assistant_id") if isinstance(metadata, Mapping) else None
    if isinstance(assistant_id, str):
        read["assistant_id"] = assistant_id
    if delivery_key is not None:
        if delivery_key in _thread_delivery_keys(metadata):
            return {**read, "state": "duplicate"}
        runs = await client.runs.list(thread_id, limit=_DELIVERY_LOOKBACK_RUNS)
        for run in runs if isinstance(runs, list) else []:
            run_metadata = run.get("metadata") if isinstance(run, Mapping) else None
            if (
                isinstance(run_metadata, Mapping)
                and run_metadata.get(DELIVERY_KEY_METADATA) == delivery_key
            ):
                return {**read, "state": "duplicate"}
    # Only pending interrupts wait on a decision: a cancelled run leaves the
    # thread ``interrupted`` with nothing to answer.
    if interrupts:
        return {**read, "state": "paused"}
    return {**read, "state": "busy" if record.get("status") == "busy" else "ready"}


_ACTIVE_LOOKBACK_RUNS = 20
"""How many of a thread's latest runs are checked for active ones."""


async def wait_for_idle_thread(*, client: LangGraphClient, thread_id: str) -> None:
    """Wait until none of the thread's runs is queued or running."""
    while True:
        runs = await client.runs.list(thread_id, limit=_ACTIVE_LOOKBACK_RUNS)
        # Newest first, so the last active run is the one that runs next.
        active = [
            run
            for run in (runs if isinstance(runs, list) else [])
            if isinstance(run, Mapping) and run.get("status") in ("pending", "running")
        ]
        run_id = active[-1].get("run_id") if active else None
        if not isinstance(run_id, str):
            return
        await client.runs.join(thread_id, run_id)


async def cancel_ingress_runs(*, client: LangGraphClient, thread_id: str) -> list[str]:
    """Cancel the thread's queued and running runs, keeping their progress.

    Returns the IDs of the runs it cancelled; a missing thread has none.
    """
    try:
        runs = await client.runs.list(thread_id, limit=_ACTIVE_LOOKBACK_RUNS)
    except Exception as error:
        if lookup_status(error) == HTTPStatus.NOT_FOUND:
            return []
        raise
    run_ids = [
        run_id
        for run in (runs if isinstance(runs, list) else [])
        if isinstance(run, Mapping)
        and run.get("status") in ("pending", "running")
        and isinstance(run_id := run.get("run_id"), str)
    ]
    cancelled = await asyncio.gather(
        *(_cancel_run(client, thread_id, run_id) for run_id in run_ids)
    )
    return [run_id for run_id, done in zip(run_ids, cancelled, strict=True) if done]


async def _cancel_run(client: LangGraphClient, thread_id: str, run_id: str) -> bool:
    try:
        await client.runs.cancel(thread_id, run_id, wait=False, action="interrupt")
    except Exception as error:
        # The run finished meanwhile.
        if lookup_status(error) in (HTTPStatus.NOT_FOUND, HTTPStatus.CONFLICT):
            return False
        raise
    return True


MULTITASK_STRATEGIES: frozenset[str] = frozenset(get_args(MultitaskStrategy))
"""Every LangGraph ``MultitaskStrategy``, for channels that check one."""


def rejected_as_busy(multitask_strategy: str | None, error: Exception) -> bool:
    """Whether a run didn't start because a run started on its thread meanwhile.

    ``reject`` then leaves the thread to that run.
    """
    return multitask_strategy == "reject" and lookup_status(error) == HTTPStatus.CONFLICT


def lookup_status(error: Exception) -> int | None:
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


async def start_ingress_run(
    *,
    client: LangGraphClient,
    thread_id: str,
    assistant_id: str,
    messages: Sequence[Mapping[str, object]],
    state: Mapping[str, object] | None = None,
    context: Context | None = None,
    config: Config | None = None,
    metadata: Mapping[str, Any] | None = None,
    webhook: str | None = None,
    multitask_strategy: MultitaskStrategy = "enqueue",
    thread_origin: IngressThreadOrigin | None = None,
) -> StartedIngressRun:
    """Create or reuse a thread and enqueue a run with an authenticated client.

    ``state`` holds other state keys to start with; ``multitask_strategy`` is
    what the run does to a run already active on the thread; the Agent Server
    POSTs the finished run to ``webhook``. ``thread_origin`` updates the thread
    record before the run starts, so the run's metadata holds it.
    """
    created = await client.threads.create(
        thread_id=thread_id,
        if_exists="do_nothing",
        **(
            {"metadata": thread_record_metadata(_next_record(None, thread_origin))}
            if thread_origin is not None
            else {}
        ),
    )
    # An existing thread keeps its metadata: compare what came back.
    await _write_thread_record(client, thread_id, created, thread_origin)
    # JSON message dicts; ``Input`` cannot express ``object``-valued entries.
    run_input: dict[str, Any] = {**(state or {}), "messages": messages}
    run = await client.runs.create(
        thread_id,
        assistant_id,
        input=run_input,
        multitask_strategy=multitask_strategy,
        **_run_options(context, config, metadata),
        **({"webhook": webhook} if webhook is not None else {}),
    )
    return _started_run(thread_id, run)


async def resume_ingress_run(
    *,
    client: LangGraphClient,
    thread_id: str,
    assistant_id: str,
    resume: dict[str, object],
    context: Context | None = None,
    config: Config | None = None,
    thread_origin: IngressThreadOrigin | None = None,
) -> StartedIngressRun:
    """Enqueue a keyed interrupt response without creating a thread."""
    if thread_origin is not None:
        thread = await client.threads.get(thread_id)
        await _write_thread_record(client, thread_id, thread, thread_origin)
    run = await client.runs.create(
        thread_id,
        assistant_id,
        command=Command(resume=resume),
        multitask_strategy="enqueue",
        **_run_options(context, config),
    )
    return _started_run(thread_id, run)


def _next_record(metadata: object, origin: IngressThreadOrigin) -> ThreadRecord:
    return next_thread_record(parse_thread_record(metadata), origin["binding"], origin["sender"])


async def _write_thread_record(
    client: LangGraphClient,
    thread_id: str,
    thread: object,
    origin: IngressThreadOrigin | None,
) -> None:
    """Record the message's origin and sender on the thread, when that changes it.

    Two messages that read the record at once can drop the participant one of
    them adds; that person's next message adds them again.
    """
    if origin is None:
        return
    metadata = thread.get("metadata") if isinstance(thread, Mapping) else None
    record = _next_record(metadata, origin)
    if record != parse_thread_record(metadata):
        await client.threads.update(thread_id, metadata=thread_record_metadata(record))


def _started_run(thread_id: str, run: Mapping[str, object]) -> StartedIngressRun:
    # The server response is untrusted input even though the SDK types it as ``Run``.
    run_id = run.get("run_id") if isinstance(run, Mapping) else None
    if not isinstance(run_id, str) or not run_id.strip():
        raise ChannelError("channel_run_failed", "Agent Server did not return run_id")
    set_log_attributes({"thread_id": thread_id, "run_id": run_id})
    return {"thread_id": thread_id, "run_id": run_id}


async def observe_ingress_run(
    *, client: LangGraphClient, thread_id: str, run_id: str
) -> IngressRunObservation:
    """Join an accepted run and read its status for the transport to validate."""
    values = await client.runs.join(thread_id, run_id)
    run = await client.runs.get(thread_id, run_id)
    status = run.get("status") if isinstance(run, Mapping) else None
    if isinstance(status, str):
        set_log_attributes({"run_status": status})
    return {"values": values, "status": status}
