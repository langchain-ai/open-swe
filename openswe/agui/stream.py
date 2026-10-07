"""Start and follow dashboard thread runs as AG-UI event streams."""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Literal

from ag_ui.core import (
    BaseEvent,
    MessagesSnapshotEvent,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    UserMessage,
)
from ag_ui.encoder import EventEncoder
from fastapi import HTTPException
from langgraph_sdk.errors import NotFoundError
from pydantic import BaseModel, ConfigDict, ValidationError

from openswe.agui.messages import HumanTurn, agui_messages, human_turn
from openswe.agui.translator import AgUiTranslator
from openswe.threads.proxy import proxy_dashboard_thread_commands
from openswe.threads.summary import assert_thread_readable
from openswe.utils.json_types import thread_metadata
from openswe.utils.streaming import TERMINAL_LIFECYCLE_EVENTS, root_lifecycle
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_ASSISTANT_ID = "agent"
_QUIET_SECONDS = 15.0

type RunOutcome = Literal["completed", "failed", "interrupted"]

_OUTCOMES: dict[str, RunOutcome] = {
    "completed": "completed",
    "success": "completed",
    "interrupted": "interrupted",
    "failed": "failed",
    "error": "failed",
    "timeout": "failed",
}


class RunConfigurable(BaseModel):
    """The run settings the dashboard composer picks, forwarded as ``configurable``."""

    model_config = ConfigDict(extra="forbid")

    agent_model_id: str | None = None
    agent_effort: str | None = None
    model_selection: Literal["auto", "explicit"] | None = None
    model_selection_changed: bool | None = None
    model_selection_action_id: str | None = None
    repo: str | None = None
    repo_explicitly_none: bool | None = None
    environment: str | None = None


class ForwardedProps(BaseModel):
    model_config = ConfigDict(extra="ignore")

    configurable: RunConfigurable = RunConfigurable()
    multitask_strategy: Literal["enqueue"] | None = None


class _RunStartInput(BaseModel):
    messages: list[HumanTurn]


class _RunStartConfig(BaseModel):
    configurable: RunConfigurable


class _RunStartParams(BaseModel):
    input: _RunStartInput
    config: _RunStartConfig
    multitask_strategy: Literal["enqueue"] | None = None


class _RunStartCommand(BaseModel):
    id: int = 1
    method: Literal["run.start"] = "run.start"
    params: _RunStartParams


class _CommandResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # A message steered into a run started outside the dashboard can come back without one.
    run_id: str | None = None


class _CommandResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["success"]
    result: _CommandResult


def _encode(events: list[BaseEvent], encoder: EventEncoder) -> str:
    return "".join(encoder.encode(event) for event in events)


async def _snapshot(thread_id: str) -> MessagesSnapshotEvent:
    state = await langgraph_client().threads.get_state(thread_id)
    return MessagesSnapshotEvent(messages=agui_messages(state.get("values")))


async def _run_outcome(thread_id: str, run_id: str) -> RunOutcome | None:
    run = await langgraph_client().runs.get(thread_id, run_id)
    return _OUTCOMES.get(run.get("status") or "")


async def _follow(
    thread_id: str, run_id: str, translator: AgUiTranslator
) -> AsyncIterator[list[BaseEvent] | RunOutcome | None]:
    """Translated events for ``run_id`` until it ends, then how it ended.

    ``None`` marks a quiet stretch, so the caller can keep idle proxies from
    closing the response. A terminal lifecycle event can be missed when the run
    ends before the subscription opens, so quiet also means asking LangGraph.
    """
    async with langgraph_client().threads.stream(
        thread_id, assistant_id=_ASSISTANT_ID
    ) as thread_stream:
        events = aiter(thread_stream.subscribe(["lifecycle", "messages", "tools"]))
        # Never cancel a pending ``anext``: that closes the subscription's generator.
        next_event = asyncio.ensure_future(anext(events))
        try:
            while True:
                done, _ = await asyncio.wait({next_event}, timeout=_QUIET_SECONDS)
                if not done:
                    if outcome := await _run_outcome(thread_id, run_id):
                        yield outcome
                        return
                    yield None
                    continue
                try:
                    event = next_event.result()
                except StopAsyncIteration:
                    yield await _run_outcome(thread_id, run_id) or "interrupted"
                    return
                next_event = asyncio.ensure_future(anext(events))
                lifecycle = root_lifecycle(event)
                if (
                    lifecycle is not None
                    and lifecycle[0] == run_id
                    and lifecycle[1] in TERMINAL_LIFECYCLE_EVENTS
                ):
                    yield _OUTCOMES[lifecycle[1]]
                    return
                if translated := translator.translate(event):
                    yield translated
        finally:
            next_event.cancel()


async def _stream(
    thread_id: str,
    run: RunStartedEvent,
    head: list[BaseEvent],
    follow_run_id: str | None,
    translator: AgUiTranslator,
) -> AsyncIterator[str]:
    encoder = EventEncoder()
    yield _encode([run, *head], encoder)
    if follow_run_id is None:
        yield _encode([RunFinishedEvent(thread_id=run.thread_id, run_id=run.run_id)], encoder)
        return
    outcome: RunOutcome = "interrupted"
    try:
        async for item in _follow(thread_id, follow_run_id, translator):
            if item is None:
                yield ": keepalive\n\n"
            elif isinstance(item, list):
                yield _encode(item, encoder)
            else:
                outcome = item
        final: list[BaseEvent] = [*translator.close(), await _snapshot(thread_id)]
    except Exception:
        logger.exception(
            "AG-UI thread stream failed",
            extra={"thread_id": thread_id, "langgraph_run_id": follow_run_id},
        )
        failure = RunErrorEvent(message="Lost the run's event stream.", code="STREAM_FAILED")
        yield _encode([*translator.close(), failure], encoder)
        return
    if outcome == "failed":
        final.append(RunErrorEvent(message="The run failed.", code="RUN_FAILED"))
    else:
        final.append(RunFinishedEvent(thread_id=run.thread_id, run_id=run.run_id))
    yield _encode(final, encoder)


def _latest_user_message(input: RunAgentInput) -> UserMessage:
    for message in reversed(input.messages):
        if isinstance(message, UserMessage):
            return message
    raise HTTPException(400, "an AG-UI run needs a user message")


async def start_run(input: RunAgentInput, login: str, email: str | None) -> AsyncIterator[str]:
    """Start, queue, or steer a run through the dashboard's command path, then stream it."""
    try:
        props = ForwardedProps.model_validate(input.forwarded_props or {})
    except ValidationError as exc:
        raise HTTPException(422, "forwardedProps are not dashboard run settings") from exc
    command = _RunStartCommand(
        params=_RunStartParams(
            input=_RunStartInput(messages=[human_turn(_latest_user_message(input))]),
            config=_RunStartConfig(configurable=props.configurable),
            multitask_strategy=props.multitask_strategy,
        )
    )
    status, content, _ = await proxy_dashboard_thread_commands(
        input.thread_id,
        login,
        command.model_dump_json(exclude_none=True).encode(),
        email=email,
    )
    if status >= 400:
        raise HTTPException(status, content.decode(errors="replace"))
    try:
        run_id = _CommandResponse.model_validate_json(content).result.run_id
    except ValidationError as exc:
        raise HTTPException(502, "LangGraph did not start a run") from exc
    run_id = run_id or await _running_run_id(input.thread_id)
    translator = AgUiTranslator({message.id for message in input.messages})
    run = RunStartedEvent(thread_id=input.thread_id, run_id=input.run_id)
    # With no run left to follow, the snapshot is the only way the reply reaches the client.
    head: list[BaseEvent] = [] if run_id else [await _snapshot(input.thread_id)]
    return _stream(input.thread_id, run, head, run_id, translator)


async def _no_events() -> AsyncIterator[str]:
    return
    yield


async def _running_run_id(thread_id: str) -> str | None:
    running = await langgraph_client().runs.list(thread_id, status="running", limit=1)
    return running[0]["run_id"] if running else None


async def connect(thread_id: str, login: str, email: str | None) -> AsyncIterator[str]:
    """The thread's transcript, then its live run (if any) until that run ends."""
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except NotFoundError:
        # The client mints a new thread's id before its first run creates it.
        return _no_events()
    assert_thread_readable(thread_metadata(thread), login, email)
    live_run_id = await _running_run_id(thread_id)
    snapshot = await _snapshot(thread_id)
    translator = AgUiTranslator({message.id for message in snapshot.messages})
    run = RunStartedEvent(thread_id=thread_id, run_id=live_run_id or f"connect-{thread_id}")
    return _stream(thread_id, run, [snapshot], live_run_id, translator)
