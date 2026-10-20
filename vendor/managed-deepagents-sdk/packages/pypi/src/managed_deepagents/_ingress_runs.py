"""Transport-neutral creation and observation of managed ingress runs.

Internal transports supply an authenticated client and normalized payloads here.
Each transport owns authentication, terminal-status policy, and reply extraction.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph_sdk.schema import Command, Config, Context, RunStatus

from managed_deepagents._channels.types import ChannelError

if TYPE_CHECKING:
    from langgraph_sdk.client import LangGraphClient


class StartedIngressRun(TypedDict):
    thread_id: str
    run_id: str


class IngressRunObservation(TypedDict):
    values: dict[str, Any]
    #: ``None`` when the server omitted a status; each transport validates it.
    status: RunStatus | None


class _RunOptions(TypedDict, total=False):
    """Optional ``runs.create`` keywords, sent only when the caller set them."""

    context: Context
    config: Config


def _run_options(context: Context | None, config: Config | None) -> _RunOptions:
    options: _RunOptions = {}
    if context is not None:
        options["context"] = context
    if config is not None:
        options["config"] = config
    return options


async def start_ingress_run(
    *,
    client: LangGraphClient,
    thread_id: str,
    assistant_id: str,
    messages: list[dict[str, object]],
    context: Context | None = None,
    config: Config | None = None,
) -> StartedIngressRun:
    """Create or reuse a thread and enqueue a run with an authenticated client."""
    await client.threads.create(thread_id=thread_id, if_exists="do_nothing")
    # JSON message dicts; ``Input`` cannot express ``object``-valued entries.
    run_input: dict[str, Any] = {"messages": messages}
    run = await client.runs.create(
        thread_id,
        assistant_id,
        input=run_input,
        multitask_strategy="enqueue",
        **_run_options(context, config),
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
) -> StartedIngressRun:
    """Enqueue a keyed interrupt response without creating a thread."""
    run = await client.runs.create(
        thread_id,
        assistant_id,
        command=Command(resume=resume),
        multitask_strategy="enqueue",
        **_run_options(context, config),
    )
    return _started_run(thread_id, run)


def _started_run(thread_id: str, run: Mapping[str, object]) -> StartedIngressRun:
    # The server response is untrusted input even though the SDK types it as ``Run``.
    run_id = run.get("run_id") if isinstance(run, Mapping) else None
    if not isinstance(run_id, str) or not run_id.strip():
        raise ChannelError("channel_run_failed", "Agent Server did not return run_id")
    return {"thread_id": thread_id, "run_id": run_id}


async def observe_ingress_run(
    *, client: LangGraphClient, thread_id: str, run_id: str
) -> IngressRunObservation:
    """Join an accepted run and read its status for the transport to validate."""
    values = await client.runs.join(thread_id, run_id)
    run = await client.runs.get(thread_id, run_id)
    status = run.get("status") if isinstance(run, Mapping) else None
    return {"values": values, "status": status}
