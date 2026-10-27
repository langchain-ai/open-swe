"""Inbound Trigger message acknowledgement and run observation."""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import json
import os
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from importlib import import_module
from typing import (
    TYPE_CHECKING,
    Literal,
    Never,
    NoReturn,
    NotRequired,
    Protocol,
    TypedDict,
    TypeVar,
    cast,
)

from managed_deepagents._agent_auth_http import missing_workspace_connection_slug
from managed_deepagents._channels.binding import binding_with_messages
from managed_deepagents._channels.context import (
    normalized_user_prompt_response,
    trigger_channel_binding,
)
from managed_deepagents._channels.reactions import (
    DEFAULT_REACTION_EMOJI,
    ReactionChoice,
    resolve_reaction_emoji,
)
from managed_deepagents._channels.run_input import run_start_options
from managed_deepagents._channels.trigger.client import (
    LANGSMITH_MDA_TRIGGER_SERVER_URL_ENV,
    TriggerClientError,
    TriggerClientLike,
    create_trigger_client,
)
from managed_deepagents._channels.trigger.identity_context import (
    IdentityContextKey,
    IdentityContextUnavailableError,
    InvalidIdentityContextError,
    principal_id_from_identity_context,
)
from managed_deepagents._channels.trigger.ingress import (
    cancel_ingress_thread_runs,
    conversation_thread_id,
    ingress_resume_target,
    ingress_thread_exists,
    ingress_thread_interrupts,
    newer_ingress_run,
    observe_ingress_agent,
    resume_ingress_agent,
    start_ingress_agent,
)
from managed_deepagents._channels.trigger.tracing_project import tracer_session_id
from managed_deepagents._channels.trigger.types import (
    TRIGGER_SERVICE_KEY_HEADER,
    TriggerChannelAddress,
    TriggerChannelEvent,
    TriggerChannelInterrupt,
    TriggerChannelMessageEvent,
    TriggerChannelResume,
    TriggerChannelRunCancelEvent,
    TriggerChannelRunEvent,
    TriggerChannelRuntimeEventRequest,
    TriggerChannelRuntimeEventType,
    TriggerChannelRuntimeStatus,
    TriggerChannelUserPromptResponseEvent,
    parse_trigger_channel_event,
    trigger_event_actor,
)
from managed_deepagents._ingress import ScheduleFn, schedule_ingress_work
from managed_deepagents._ingress_runs import lookup_status, rejected_as_busy
from managed_deepagents._log import logger
from managed_deepagents._telemetry.otel import set_log_attributes
from managed_deepagents.run_context import INTERRUPT_CORRELATION_KEY

if TYPE_CHECKING:
    from langgraph_sdk.schema import Interrupt

    from managed_deepagents._channels.actor import ChannelActor
    from managed_deepagents._channels.binding import ChannelBinding
    from managed_deepagents._channels.http.types import (
        HttpFinish,
        HttpFinishedDelivery,
        HttpOnFinish,
        HttpSkipReason,
    )
    from managed_deepagents._channels.run_input import NormalizedRunInput
    from managed_deepagents._channels.trigger.ingress import (
        ObservedIngressRun,
        PendingInterrupt,
        ResumeTarget,
    )
    from managed_deepagents._channels.types import JsonValue
    from managed_deepagents._ingress_runs import MultitaskStrategy, StartedIngressRun

ASSISTANT_ID_ENV = "MDA_ASSISTANT_ID"

ObserveAgentFn = Callable[..., Awaitable["ObservedIngressRun"]]
# Provider hook for a message event's run input; defaults to `input.messages`.
MessageInputFn = Callable[[TriggerChannelMessageEvent], list[dict[str, object]]]

_T = TypeVar("_T")


class TriggerChannelThread(TypedDict):
    """The thread a message event belongs to, before any run exists."""

    id: str
    source_thread_key: str


@dataclass(frozen=True)
class TriggerMessageDecision:
    """What a message event starts.

    The run input a provider's handler returned (``input``), no run
    (``skipped``), or nothing because the handler failed (``failed``).
    """

    type: Literal["input", "skipped", "failed"]
    input: NormalizedRunInput | None = None


@dataclass(frozen=True)
class TriggerMessageBackground:
    """A decision that may take longer than Trigger Server waits, such as an event endpoint's.

    Trigger Server then gets its answer first, and a failure is only logged.
    """

    decide: Callable[[], Awaitable[TriggerMessageDecision]]


class TriggerFinishedEvent(TypedDict):
    """What a finished message's ``raw_event`` holds: the Trigger delivery, without
    its identity token."""

    delivery: JsonValue
    actor: NotRequired[dict[str, JsonValue]]
    """Who sent it, as Trigger Server named them."""


TriggerMessageRoute = TriggerMessageDecision | TriggerMessageBackground
"""What a provider decides a message starts: at once, or in the background."""

DecideMessageFn = Callable[
    [TriggerChannelMessageEvent, TriggerChannelThread, "ChannelActor | None"],
    Awaitable[TriggerMessageRoute | None],
]
"""Decide what a message event starts, before its run; ``None`` starts it with
``message_input``'s messages. Interrupt resumes never ask."""

DecideInteractionFn = Callable[
    [TriggerChannelUserPromptResponseEvent, TriggerChannelThread, "ChannelActor | None"],
    Awaitable[TriggerMessageRoute | None],
]
"""Decide what a prompt response that answers no interrupt starts, as
``DecideMessageFn`` does a message; ``None`` starts a run with no new messages,
which reads the response from ``runtime.channel.event``."""


class StartAgentFn(Protocol):
    async def __call__(
        self,
        *,
        thread_id: str,
        assistant_id: str,
        messages: list[dict[str, object]],
        source_provider: str,
        source_thread_key: str,
        user_id: str,
        context: Mapping[str, object],
        binding: ChannelBinding,
    ) -> StartedIngressRun: ...


class ResumeAgentFn(Protocol):
    async def __call__(
        self,
        *,
        thread_id: str,
        assistant_id: str,
        resume: TriggerChannelResume,
        source_provider: str,
        source_thread_key: str,
        user_id: str,
        context: Mapping[str, object],
        binding: ChannelBinding,
    ) -> StartedIngressRun: ...


class ThreadInterruptsFn(Protocol):
    async def __call__(
        self,
        *,
        thread_id: str,
        source_provider: str,
        source_thread_key: str,
        user_id: str,
    ) -> list[PendingInterrupt]: ...


class ThreadExistsFn(Protocol):
    async def __call__(
        self,
        *,
        thread_id: str,
        source_provider: str,
        source_thread_key: str,
        user_id: str,
    ) -> bool: ...


class NewerRunFn(Protocol):
    async def __call__(
        self,
        *,
        thread_id: str,
        run_id: str,
        source_provider: str,
        source_thread_key: str,
        user_id: str,
    ) -> bool: ...


OnlyContinuesThreadFn = Callable[[TriggerChannelMessageEvent], Awaitable[bool]]
"""Whether a message may only continue a thread the agent already ran on: then,
on a thread no run ever started on, it starts nothing, and ``decide_message``
isn't asked."""


class ResumeTargetFn(Protocol):
    async def __call__(
        self,
        *,
        thread_id: str,
        assistant_id: str,
        interrupt_ids: list[str],
        source_provider: str,
        source_thread_key: str,
        user_id: str,
    ) -> ResumeTarget: ...


class CancelThreadRunsFn(Protocol):
    async def __call__(
        self,
        *,
        thread_id: str,
        source_provider: str,
        source_thread_key: str,
        user_id: str,
    ) -> int: ...


class ConversationThreadFn(Protocol):
    async def __call__(
        self,
        *,
        source_provider: str,
        source_thread_key: str,
        user_id: str,
    ) -> str: ...


def _assert_never(_event: Never) -> NoReturn:
    """Reject an unsupported event at the provider boundary."""
    raise ValueError("unsupported channel event")


# Raised when the thread cannot say which interrupt an answer belongs to. An app
# opts out of this by carrying a correlation id; see INTERRUPT_CORRELATION_KEY.
class _AmbiguousInterruptError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def _interrupt_answered_by(
    *,
    thread_id: str,
    source_provider: str,
    source_thread_key: str,
    user_id: str,
    correlation_id: str | None,
    thread_interrupts: ThreadInterruptsFn | None,
) -> str | None:
    """The interrupt a forwarded answer belongs to, or ``None`` for a fresh turn.

    Trigger Server forwards the interaction uncorrelated, because it did not
    render the prompt. An app that carries a correlation id on both the prompt
    and its ``interrupt()`` value gets an exact match; otherwise the thread can
    only speak when exactly one interrupt is parked.
    """
    pending = await (thread_interrupts or ingress_thread_interrupts)(
        thread_id=thread_id,
        source_provider=source_provider,
        source_thread_key=source_thread_key,
        user_id=user_id,
    )
    if correlation_id is not None:
        matched = [
            candidate
            for candidate in pending
            if _interrupt_correlation_id(candidate.get("value")) == correlation_id
        ]
        if len(matched) > 1:
            raise _AmbiguousInterruptError(f"{len(matched)} interrupts share one correlation id")
        if matched:
            return matched[0]["id"]
        if pending:
            # Starting a turn here would abandon the parked checkpoints.
            raise _AmbiguousInterruptError("correlation id matched no pending interrupt")
        return None
    # A tool-call review is answered from its own prompt (``interrupt_resume``):
    # a click on one of the agent's buttons, such as a rating, is no decision.
    answerable = [
        candidate for candidate in pending if not _is_tool_call_review(candidate.get("value"))
    ]
    if len(answerable) > 1:
        # Interrupt ids carry no ordering, so without a correlation id there is
        # nothing to choose by.
        raise _AmbiguousInterruptError(f"{len(answerable)} interrupts pending")
    if not answerable and pending:
        # Starting a turn here would abandon the review.
        raise _AmbiguousInterruptError(
            "the thread waits on a tool-call review, which a click doesn't answer"
        )
    return answerable[0]["id"] if answerable else None


def _is_tool_call_review(value: object) -> bool:
    """Whether an interrupt is a tool-call review, as ``HumanInTheLoopMiddleware`` raises one."""
    return isinstance(value, Mapping) and isinstance(value.get("action_requests"), list)


def _interrupt_correlation_id(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    correlation_id = value.get(INTERRUPT_CORRELATION_KEY)
    if isinstance(correlation_id, str) and correlation_id.strip():
        return correlation_id
    return None


async def _may_start_in(
    event: TriggerChannelMessageEvent,
    *,
    thread_id: str,
    user_id: str,
    source_provider: str,
    only_continues_thread: OnlyContinuesThreadFn | None,
    thread_exists: ThreadExistsFn | None,
) -> bool:
    """Whether a message may start a run on its thread: always, unless it may
    only continue a thread the agent already ran on and none ran there. A
    failed lookup is logged and lets it start, as before the check."""
    if only_continues_thread is None or not await only_continues_thread(event):
        return True
    try:
        return await (thread_exists or ingress_thread_exists)(
            thread_id=thread_id,
            source_provider=source_provider,
            source_thread_key=event["source_thread_key"],
            user_id=user_id,
        )
    except Exception as error:  # noqa: BLE001 - a failed lookup lets the message start, as before
        logger.warning(
            "channel thread lookup failed; the message starts as before (%s)",
            logger.safe_error_label(error),
        )
        return True


async def _start_event_run(
    *,
    event: TriggerChannelRunEvent,
    thread_id: str,
    assistant_id: str,
    source_provider: str,
    channel_name: str,
    start_agent: StartAgentFn | None,
    resume_agent: ResumeAgentFn | None,
    user_id: str,
    resume_target: ResumeTargetFn | None = None,
    message_input: MessageInputFn | None = None,
    decided: NormalizedRunInput | None = None,
    multitask_strategy: MultitaskStrategy | None = None,
    answered_interrupt: str | None = None,
) -> StartedIngressRun:
    """Start or resume the delivery's run.

    A prompt response resumes ``answered_interrupt``, the interrupt it answers,
    and otherwise starts a turn, as a message does.
    """
    context: dict[str, object] = {}
    binding = trigger_channel_binding(provider=source_provider, name=channel_name, event=event)
    if event["type"] == "user_prompt_response" and answered_interrupt is not None:
        resume = resume_agent or resume_ingress_agent
        return await resume(
            thread_id=thread_id,
            assistant_id=assistant_id,
            resume={answered_interrupt: normalized_user_prompt_response(event)},
            source_provider=source_provider,
            source_thread_key=event["source_thread_key"],
            user_id=user_id,
            context=context,
            binding=binding,
        )
    if event["type"] == "message" or event["type"] == "user_prompt_response":
        start = start_agent or start_ingress_agent
        options: dict[str, object] = {}
        if multitask_strategy is not None:
            options["multitask_strategy"] = multitask_strategy
        if decided is None:
            if event["type"] == "user_prompt_response":
                messages = []
            elif message_input is not None:
                messages = message_input(event)
            else:
                messages = event["input"]["messages"]
        else:
            # Its messages replace the delivery's in the binding, as the run's
            # own input; its context sits beside the binding.
            messages = cast("list[dict[str, object]]", decided["messages"])
            options.update(
                (key, value)
                for key, value in run_start_options(decided).items()
                if key != "messages"
            )
            binding = binding_with_messages(binding, decided["messages"])
            context = dict(decided.get("context", {}))
        return await start(
            thread_id=thread_id,
            assistant_id=assistant_id,
            messages=messages,
            source_provider=source_provider,
            source_thread_key=event["source_thread_key"],
            user_id=user_id,
            context=context,
            binding=binding,
            **options,
        )
    if event["type"] == "interrupt_resume":
        target = await (resume_target or ingress_resume_target)(
            thread_id=thread_id,
            assistant_id=assistant_id,
            interrupt_ids=list(event["interaction"]["resume"]),
            source_provider=source_provider,
            source_thread_key=event["source_thread_key"],
            user_id=user_id,
        )
        resume = resume_agent or resume_ingress_agent
        return await resume(
            thread_id=target["thread_id"],
            assistant_id=target["assistant_id"],
            resume=event["interaction"]["resume"],
            source_provider=source_provider,
            source_thread_key=event["source_thread_key"],
            user_id=user_id,
            context=context,
            binding=binding,
        )
    _assert_never(event)


async def handle_trigger_channel_event_request(  # noqa: C901, PLR0911, PLR0912, PLR0915
    request: object,
    *,
    source_provider: str,
    channel_name: str,
    assistant_id: str | None = None,
    raw_body: str | bytes | None = None,
    schedule: ScheduleFn | None = None,
    start_agent: StartAgentFn | None = None,
    resume_agent: ResumeAgentFn | None = None,
    thread_interrupts: ThreadInterruptsFn | None = None,
    thread_exists: ThreadExistsFn | None = None,
    newer_run: NewerRunFn | None = None,
    resume_target: ResumeTargetFn | None = None,
    conversation_thread: ConversationThreadFn | None = None,
    observe_agent: ObserveAgentFn | None = None,
    cancel_runs: CancelThreadRunsFn | None = None,
    trigger_client: TriggerClientLike | None = None,
    reactions: ReactionChoice = DEFAULT_REACTION_EMOJI,
    started_status: TriggerChannelRuntimeStatus | None = None,
    message_input: MessageInputFn | None = None,
    only_continues_thread: OnlyContinuesThreadFn | None = None,
    decide_message: DecideMessageFn | None = None,
    decide_interaction: DecideInteractionFn | None = None,
    multitask_strategy: MultitaskStrategy | None = None,
    on_finish: HttpOnFinish | None = None,
    identity_context_key: IdentityContextKey | None = None,
    identity_context_issuer: str | None = None,
) -> object:
    """Validate a Trigger message and enqueue its Agent Server run."""
    # TODO: Validate Trigger's platform-signed x-service-key JWT (not presence).
    if not _nonblank_header(request, TRIGGER_SERVICE_KEY_HEADER):
        return _json_response({"error": "unauthorized"}, status_code=401)

    try:
        event = parse_trigger_channel_event(json.loads(await _request_body(request, raw_body)))
    except (TypeError, ValueError, UnicodeDecodeError):
        logger.warning("invalid channel event")
        return _json_response({"error": "invalid channel event"}, status_code=400)

    try:
        user_id = await _principal_for_event(
            event,
            identity_context_key=identity_context_key,
            identity_context_issuer=identity_context_issuer,
        )
    except _MissingIdentityContextError:
        logger.warning("missing channel identity_context")
        return _json_response({"error": "missing channel identity_context"}, status_code=400)
    except IdentityContextUnavailableError as error:
        logger.error(
            "channel identity_context unavailable (%s)",
            logger.safe_error_label(error),
            error=error,
        )
        return _json_response(
            {"error": "identity_context verification unavailable"},
            status_code=503,
        )
    except InvalidIdentityContextError as error:
        logger.warning(
            "invalid channel identity_context (%s)",
            logger.safe_error_label(error),
        )
        return _json_response({"error": "invalid channel identity_context"}, status_code=400)
    # Who the delivery came from, as Trigger Server names them; never logged.
    # Only as trusted as the service key, which is checked for presence alone.
    actor = trigger_event_actor(event)

    set_log_attributes(
        {
            "event_type": event["type"],
            "event_id": event["event_id"],
            "delivery_id": event["delivery_id"],
            "channel_id": event["channel_id"],
        }
    )
    logger.info(
        "Channel incoming event (type=%s, event_id=%s, delivery_id=%s, channel_id=%s)",
        event["type"],
        logger.log_identifier(event["event_id"]),
        logger.log_identifier(event["delivery_id"]),
        logger.log_identifier(event["channel_id"]),
    )

    if event["type"] == "run_cancel":
        return await _cancel_event_runs(
            event,
            source_provider=source_provider,
            user_id=user_id,
            cancel_runs=cancel_runs,
            conversation_thread=conversation_thread,
        )

    try:
        client = (
            trigger_client
            if trigger_client is not None
            else create_trigger_client(user_id=_header(request, "x-user-id"))
        )
    except ValueError as error:
        logger.error(
            "channel transport is not configured (%s)",
            logger.safe_error_label(error),
            error=error,
        )
        client = None
    else:
        if client is None:
            logger.error(
                "channel transport is not configured "
                "(%s and LANGSMITH_API_KEY or LANGCHAIN_API_KEY are required)",
                LANGSMITH_MDA_TRIGGER_SERVER_URL_ENV,
            )
    if client is None:
        return _json_response({"error": "channel transport is not configured"}, status_code=500)

    try:
        resolved_assistant_id = _resolve_assistant_id(assistant_id)
    except RuntimeError:
        logger.error("%s is not configured", ASSISTANT_ID_ENV)
        return _json_response({"error": "channel runtime is not configured"}, status_code=500)

    try:
        thread_id = await (conversation_thread or conversation_thread_id)(
            source_provider=source_provider,
            source_thread_key=event["source_thread_key"],
            user_id=user_id,
        )
    except Exception as error:  # noqa: BLE001 - never expose Agent Server details in the response
        logger.error("channel thread could not be found (%s)", logger.safe_error_label(error))
        return _start_failed_response()
    thread: TriggerChannelThread = {
        "id": thread_id,
        "source_thread_key": event["source_thread_key"],
    }

    # The interrupt a prompt response answers; one that answers none starts a
    # turn, which the provider may decide.
    answered_interrupt: str | None = None
    if event["type"] == "user_prompt_response":
        try:
            answered_interrupt = await _interrupt_answered_by(
                thread_id=thread["id"],
                source_provider=source_provider,
                source_thread_key=event["source_thread_key"],
                user_id=user_id,
                correlation_id=normalized_user_prompt_response(event).get("correlation_id"),
                thread_interrupts=thread_interrupts,
            )
        except _AmbiguousInterruptError as ambiguous:
            # Trigger Server never reads the body, so the log is the only place this surfaces.
            logger.warning(
                "channel user prompt response could not be matched to an interrupt "
                "(%s, delivery_id=%s, event_id=%s)",
                ambiguous.reason,
                logger.log_identifier(event["delivery_id"]),
                logger.log_identifier(event["event_id"]),
            )
            return _accepted_response(event)
        except Exception as error:  # noqa: BLE001 - never expose Agent Server details in the response
            logger.error(
                "channel run could not be started (%s)",
                logger.safe_error_label(error),
            )
            return _start_failed_response()
    # Whether the delivery starts a turn of its own, which finishes: a message,
    # or a prompt response that answers no interrupt. Resumes don't.
    starts_turn = event["type"] == "message" or (
        event["type"] == "user_prompt_response" and answered_interrupt is None
    )

    async def start(decided: NormalizedRunInput | None) -> StartedIngressRun | None:
        """Start the delivery's run; ``None`` when ``reject`` left the thread to its run."""
        try:
            return await _start_event_run(
                event=event,
                thread_id=thread["id"],
                assistant_id=resolved_assistant_id,
                source_provider=source_provider,
                channel_name=channel_name,
                start_agent=start_agent,
                resume_agent=resume_agent,
                resume_target=resume_target,
                user_id=user_id,
                message_input=message_input,
                decided=decided,
                multitask_strategy=multitask_strategy,
                answered_interrupt=answered_interrupt,
            )
        except Exception as error:
            if starts_turn and rejected_as_busy(multitask_strategy, error):
                # A run already active on the thread keeps it under `reject`.
                logger.info(
                    "channel %s rejected: its thread is busy (delivery_id=%s)",
                    event["type"],
                    logger.log_identifier(event["delivery_id"]),
                )
                return None
            raise

    async def finish(finished: HttpFinish) -> None:
        """Tell the provider a turn is done, once, as ``http_channel``'s ``on_finish`` hears it.

        The delivery's ``raw_event`` is a ``TriggerFinishedEvent``; a failure is logged.
        """
        if not starts_turn or on_finish is None:
            return
        message = {key: value for key, value in event.items() if key != "identity_context"}
        raw: TriggerFinishedEvent = {"delivery": cast("JsonValue", message)}
        if actor is not None:
            raw["actor"] = {
                key: value for key, value in dataclasses.asdict(actor).items() if value is not None
            }
        delivery = cast(
            "HttpFinishedDelivery",
            {"thread_id": thread["id"], "raw_event": cast("JsonValue", raw), **finished},
        )
        try:
            await on_finish(_trigger_target(event), delivery)
        except Exception as error:  # noqa: BLE001 - author hooks may raise anything
            logger.error("channel finish notice failed (%s)", logger.safe_error_label(error))

    def finish_later(finished: HttpFinish) -> None:
        if on_finish is not None:
            schedule_ingress_work(
                lambda: finish(finished), label="Channel finish notice", schedule=schedule
            )

    async def observe(started: StartedIngressRun) -> HttpFinish:
        return await _observe_accepted_run(
            event=event,
            started=started,
            client=client,
            observe_agent=observe_agent,
            source_provider=source_provider,
            source_thread_key=event["source_thread_key"],
            user_id=user_id,
            reactions=reactions,
            started_status=started_status,
            multitask_strategy=multitask_strategy,
            newer_run=newer_run,
        )

    if event["type"] == "message" and not await _may_start_in(
        event,
        thread_id=thread["id"],
        user_id=user_id,
        source_provider=source_provider,
        only_continues_thread=only_continues_thread,
        thread_exists=thread_exists,
    ):
        logger.info(
            "channel message started no run: the agent never ran on its thread (delivery_id=%s)",
            logger.log_identifier(event["delivery_id"]),
        )
        finish_later(_skipped("ignored"))
        return _accepted_response(event)

    decision: TriggerMessageRoute | None = None
    if event["type"] == "message" and decide_message is not None:
        decision = await decide_message(event, thread, actor)
    elif starts_turn and event["type"] == "user_prompt_response" and decide_interaction is not None:
        decision = await decide_interaction(event, thread, actor)
    if isinstance(decision, TriggerMessageBackground):
        decide = decision.decide

        async def decide_then_start() -> None:
            decided = await decide()
            if decided.type != "input":
                _log_no_run(event, decided)
                await finish(_skipped("ignored"))
                return
            try:
                started = await start(decided.input)
            except Exception as error:  # noqa: BLE001 - never log Agent Server details
                logger.error(
                    "channel run could not be started (%s)",
                    logger.safe_error_label(error),
                )
                await finish({"type": "failed", "reason": "start", "interrupts": []})
                return
            await finish(await observe(started) if started is not None else _skipped("rejected"))

        # Trigger Server only waits briefly, so it hears first; a failure from
        # here on is logged, and nothing redelivers the message.
        schedule_ingress_work(decide_then_start, label="Channel event decision", schedule=schedule)
        return _accepted_response(event)
    if decision is not None and decision.type == "skipped":
        _log_no_run(event, decision)
        finish_later(_skipped("ignored"))
        return _accepted_response(event)
    # Trigger Server may deliver it again, which finishes on its own.
    if decision is not None and decision.type == "failed":
        return _start_failed_response()

    try:
        started = await start(decision.input if decision is not None else None)
    except _AmbiguousInterruptError as ambiguous:
        # Trigger Server never reads the body, so the log is the only place this surfaces.
        logger.warning(
            "channel user prompt response could not be matched to an interrupt "
            "(%s, delivery_id=%s, event_id=%s)",
            ambiguous.reason,
            logger.log_identifier(event["delivery_id"]),
            logger.log_identifier(event["event_id"]),
        )
        return _accepted_response(event)
    except Exception as error:  # noqa: BLE001 - never expose Agent Server details in the response
        logger.error(
            "channel run could not be started (%s)",
            logger.safe_error_label(error),
            error=error,
        )
        return _start_failed_response()
    if started is None:
        finish_later(_skipped("rejected"))
        return _accepted_response(event)

    async def observe_then_finish() -> None:
        await finish(await observe(started))

    schedule_ingress_work(
        observe_then_finish,
        label="Channel run observation",
        schedule=schedule,
    )
    return _accepted_response(event)


def _trigger_target(event: TriggerChannelEvent) -> JsonValue:
    """Where a delivery's replies go, as the channel binding names it."""
    return cast(
        "JsonValue",
        {
            "channel_id": event["channel_id"],
            "address": event["address"],
            "source_thread_key": event["source_thread_key"],
        },
    )


def _skipped(reason: HttpSkipReason) -> HttpFinish:
    return {"type": "skipped", "reason": reason, "interrupts": []}


def _log_no_run(event: TriggerChannelEvent, decision: TriggerMessageDecision) -> None:
    """Log an event that started no run; a failed handler logged its own error."""
    logger.info(
        "channel %s started no run (%sdelivery_id=%s)",
        event["type"],
        "its handler failed, " if decision.type == "failed" else "",
        logger.log_identifier(event["delivery_id"]),
    )


def _start_failed_response() -> object:
    return _json_response({"error": "channel run could not be started"}, status_code=500)


async def _cancel_event_runs(
    event: TriggerChannelRunCancelEvent,
    *,
    source_provider: str,
    user_id: str,
    cancel_runs: CancelThreadRunsFn | None,
    conversation_thread: ConversationThreadFn | None,
) -> object:
    """Stop the source thread's runs; each run's observer reports ``run.cancelled``.

    Runs are cancelled as the thread's channel principal, so the managed owner
    filter confines the cancel to this source conversation.
    """
    try:
        thread_id = await (conversation_thread or conversation_thread_id)(
            source_provider=source_provider,
            source_thread_key=event["source_thread_key"],
            user_id=user_id,
        )
        cancelled = await (cancel_runs or cancel_ingress_thread_runs)(
            thread_id=thread_id,
            source_provider=source_provider,
            source_thread_key=event["source_thread_key"],
            user_id=user_id,
        )
    except Exception as error:  # noqa: BLE001 - never expose Agent Server details in the response
        logger.error(
            "channel runs could not be cancelled (%s)",
            logger.safe_error_label(error),
        )
        return _json_response({"error": "channel runs could not be cancelled"}, status_code=500)
    logger.info(
        "Channel runs cancelled (count=%s, delivery_id=%s)",
        cancelled,
        logger.log_identifier(event["delivery_id"]),
    )
    return _accepted_response(event)


def _accepted_response(event: TriggerChannelEvent) -> object:
    return _json_response(
        {"status": "accepted", "delivery_id": event["delivery_id"]},
        status_code=202,
    )


async def _observe_accepted_run(
    *,
    event: TriggerChannelEvent,
    started: StartedIngressRun,
    client: TriggerClientLike,
    observe_agent: ObserveAgentFn | None,
    source_provider: str | None = None,
    source_thread_key: str | None = None,
    user_id: str,
    reactions: ReactionChoice = DEFAULT_REACTION_EMOJI,
    started_status: TriggerChannelRuntimeStatus | None = None,
    multitask_strategy: MultitaskStrategy | None = None,
    newer_run: NewerRunFn | None = None,
) -> HttpFinish:
    """Observe a started run and say how the delivery finished; the run itself
    posts its answer (``_channels/delivery.py``)."""
    was_replaced = functools.partial(
        _replaced_by_newer_message,
        event,
        started,
        multitask_strategy=multitask_strategy,
        source_provider=source_provider,
        source_thread_key=source_thread_key,
        user_id=user_id,
        newer_run=newer_run,
    )
    # A run a newer message replaced ended ``interrupted``, as the HTTP channel
    # reports one.
    replaced: HttpFinish = {
        "type": "run",
        "run": {"id": started["run_id"], "status": "interrupted"},
        "interrupts": [],
    }

    async def lifecycle(
        event_type: TriggerChannelRuntimeEventType,
        message: str | None = None,
        status: TriggerChannelRuntimeStatus | None = None,
    ) -> None:
        logger.info(
            "Channel outgoing event (type=%s, delivery_id=%s, channel_id=%s, run_id=%s)",
            event_type,
            logger.log_identifier(event["delivery_id"]),
            logger.log_identifier(event["channel_id"]),
            logger.log_identifier(started["run_id"]),
        )
        request = _runtime_event(
            event_type,
            event,
            started,
            message=message,
            tracer_session_id=await tracer_session_id(),
            status=status,
        )
        await _best_effort(client.post_runtime_event(event["channel_id"], request))

    await lifecycle("run.started", status=started_status)
    # Not awaited here, so the reaction never delays the reply. The `finally`
    # settles it so it cannot outlive this work item.
    reaction = asyncio.create_task(
        _react_to_trigger_message(
            event,
            client,
            started["run_id"],
            reactions=reactions,
        )
    )
    try:
        try:
            observed = await _observe_run(
                started=started,
                observe_agent=observe_agent,
                source_provider=source_provider,
                source_thread_key=source_thread_key,
                user_id=user_id,
            )
        except Exception as error:  # noqa: BLE001 - only known-safe details leave runtime
            if await was_replaced(error):
                return replaced
            await lifecycle("run.failed", message=_missing_connection_failure_message(error))
            return {"type": "failed", "reason": "observe", "interrupts": []}

        def ended(interrupts: list[TriggerChannelInterrupt] | None = None) -> HttpFinish:
            return {
                "type": "run",
                "run": {"id": started["run_id"], "status": observed["status"]},
                "interrupts": cast("list[Interrupt]", interrupts or []),
            }

        # Stopped from Slack or Studio: the run ended ``interrupted``, with
        # nothing waiting on a decision.
        if _is_cancelled_run(observed):
            if not await was_replaced(None):
                await lifecycle("run.cancelled")
            return ended()

        # The run itself posts its answer, or shows its interrupts, as it ends.
        if _is_interrupted_run(observed):
            return ended(_forwarded_interrupts(observed["values"]))

        if observed["status"] != "success":
            await lifecycle("run.failed")
            return ended()

        await lifecycle("run.completed")
        return ended()
    finally:
        await reaction


async def _react_to_trigger_message(
    event: TriggerChannelEvent,
    client: TriggerClientLike,
    run_id: str,
    *,
    reactions: ReactionChoice,
) -> None:
    """React to the message that started this run.

    Only ``message`` events carry one. The target is ``message.id``, not
    ``address.thread_id``, which is the thread root whenever a reply started the
    run.
    """
    if reactions is False or event["type"] != "message":
        return
    # `message` is provider-owned and opaque to the event schema, so read it
    # defensively rather than pinning its shape.
    message = event.get("message")
    if not isinstance(message, Mapping):
        return
    message_id = message.get("id")
    text = message.get("text")
    if not isinstance(message_id, str) or not message_id.strip():
        return
    if not isinstance(text, str):
        return

    emoji = await resolve_reaction_emoji(reactions, {"text": text})
    if emoji is None:
        return
    logger.info(
        "Channel outgoing action (type=react, delivery_id=%s, channel_id=%s, run_id=%s)",
        logger.log_identifier(event["delivery_id"]),
        logger.log_identifier(event["channel_id"]),
        logger.log_identifier(run_id),
    )
    await _best_effort(
        client.post_action(
            event["channel_id"],
            {
                "type": "react",
                "action_id": event["event_id"],
                "address": event["address"],
                "source_thread_key": event["source_thread_key"],
                "reaction": {"emoji": emoji, "message_id": message_id},
            },
        )
    )


async def _observe_run(
    *,
    started: StartedIngressRun,
    observe_agent: ObserveAgentFn | None,
    source_provider: str | None,
    source_thread_key: str | None,
    user_id: str,
) -> ObservedIngressRun:
    if observe_agent is not None:
        return await observe_agent(
            **_observation_kwargs(
                started=started,
                source_provider=source_provider,
                source_thread_key=source_thread_key,
                user_id=user_id,
            )
        )
    if source_provider is None or source_thread_key is None:
        raise RuntimeError("channel observation context is not configured")
    return await observe_ingress_agent(
        thread_id=started["thread_id"],
        run_id=started["run_id"],
        source_provider=source_provider,
        source_thread_key=source_thread_key,
        user_id=user_id,
    )


def _observation_kwargs(
    *,
    started: StartedIngressRun,
    source_provider: str | None,
    source_thread_key: str | None,
    user_id: str,
) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "thread_id": started["thread_id"],
        "run_id": started["run_id"],
        "user_id": user_id,
    }
    if source_provider is None or source_thread_key is None:
        return kwargs
    kwargs["source_provider"] = source_provider
    kwargs["source_thread_key"] = source_thread_key
    return kwargs


async def _replaced_by_newer_message(
    event: TriggerChannelEvent,
    started: StartedIngressRun,
    error: Exception | None,
    *,
    multitask_strategy: MultitaskStrategy | None,
    source_provider: str | None,
    source_thread_key: str | None,
    user_id: str,
    newer_run: NewerRunFn | None,
) -> bool:
    """Whether a newer message's run took the thread over from a cancelled or
    vanished run. It reports to Slack, so this run says nothing that would stop
    or fail the thread's status."""
    # Only these strategies let a newer message cancel a run.
    if multitask_strategy not in ("interrupt", "rollback"):
        return False
    if error is not None:
        # ``rollback`` cancels, then deletes, the run a newer message replaced.
        replaced = lookup_status(error) == HTTPStatus.NOT_FOUND
    elif source_provider is None or source_thread_key is None:
        replaced = False
    else:
        try:
            replaced = await (newer_run or newer_ingress_run)(
                thread_id=started["thread_id"],
                run_id=started["run_id"],
                source_provider=source_provider,
                source_thread_key=source_thread_key,
                user_id=user_id,
            )
        except Exception as lookup_error:  # noqa: BLE001 - never log Agent Server details
            logger.warning(
                "channel run lookup failed; the run counts as stopped (%s)",
                logger.safe_error_label(lookup_error),
            )
            replaced = False
    if replaced:
        logger.info(
            "channel run was replaced by a newer message (delivery_id=%s, run_id=%s)",
            logger.log_identifier(event["delivery_id"]),
            logger.log_identifier(started["run_id"]),
        )
    return replaced


def _is_cancelled_run(observed: ObservedIngressRun) -> bool:
    # A cancelled run ends "interrupted" with nothing parked for a person to
    # answer; reporting it as an interrupt would post an empty approval prompt.
    return observed["status"] == "interrupted" and not _has_interrupt(observed["values"])


def _is_interrupted_run(observed: ObservedIngressRun) -> bool:
    # Agent Server can report status "success" after join while values still
    # carry `__interrupt__`. Trust either signal.
    return observed["status"] == "interrupted" or _has_interrupt(observed["values"])


def _has_interrupt(values: object) -> bool:
    return bool(_interrupt_entries(values))


def _forwarded_interrupts(values: object) -> list[TriggerChannelInterrupt]:
    """The resumable interrupts in ``values``, with their ``response_schema``."""
    interrupts: list[TriggerChannelInterrupt] = []
    for entry in _interrupt_entries(values):
        if not isinstance(entry, Mapping) or "value" not in entry:
            continue
        interrupt_id = entry.get("id")
        if not isinstance(interrupt_id, str) or not interrupt_id.strip():
            continue
        interrupt: TriggerChannelInterrupt = {"id": interrupt_id, "value": entry["value"]}
        schema = entry.get("response_schema")
        if isinstance(schema, dict):
            interrupt["response_schema"] = schema
        interrupts.append(interrupt)
    return interrupts


def _interrupt_entries(values: object) -> list[object]:
    """``__interrupt__`` arrives as a flat list, or keyed by task id."""
    if not isinstance(values, Mapping):
        return []
    interrupts = values.get("__interrupt__")
    if isinstance(interrupts, list):
        return list(interrupts)
    if not isinstance(interrupts, Mapping):
        return []
    entries: list[object] = []
    for value in interrupts.values():
        if isinstance(value, list):
            entries.extend(value)
        else:
            entries.append(value)
    return entries


class RuntimeEventRoute(TypedDict):
    """Where a runtime event goes: the delivery and its conversation."""

    delivery_id: str
    source_thread_key: str
    address: TriggerChannelAddress


def _runtime_event(
    event_type: TriggerChannelRuntimeEventType,
    event: RuntimeEventRoute,
    started: StartedIngressRun,
    interrupts: list[TriggerChannelInterrupt] | None = None,
    message: str | None = None,
    *,
    tracer_session_id: str | None = None,
    status: TriggerChannelRuntimeStatus | None = None,
) -> TriggerChannelRuntimeEventRequest:
    request: TriggerChannelRuntimeEventRequest = {
        "id": str(uuid.uuid4()),
        "type": event_type,
        "delivery_id": event["delivery_id"],
        "source_thread_key": event["source_thread_key"],
        "address": event["address"],
        "run_thread_id": started["thread_id"],
        "run_id": started["run_id"],
    }
    if tracer_session_id:
        request["tracer_session_id"] = tracer_session_id
    if message:
        request["message"] = {"text": message}
    if interrupts:
        request["interrupts"] = interrupts
    # The status a channel set, on `run.started`.
    if status is not None:
        if "message" in status:
            request["message"] = status["message"]
        if "steps" in status:
            request["steps"] = status["steps"]
        if "display" in status:
            request["display"] = status["display"]
    return request


def _missing_connection_failure_message(error: Exception) -> str | None:
    slug = missing_workspace_connection_slug(str(error))
    if slug is None:
        return None
    return (
        f"This agent can't respond because the `{slug}` connection isn't configured. "
        "An agent developer can configure it with "
        f"`mda connections create {slug} --project <project>`, then try again."
    )


class _MissingIdentityContextError(ValueError):
    """A message event arrived without a Trigger identity_context token."""


async def _principal_for_event(
    event: TriggerChannelEvent,
    *,
    identity_context_key: IdentityContextKey | None,
    identity_context_issuer: str | None,
) -> str:
    """Resolve the verified principal required on every Trigger delivery."""
    raw_identity_context = event.get("identity_context")
    if not isinstance(raw_identity_context, str) or not raw_identity_context.strip():
        raise _MissingIdentityContextError
    # PyJWKClient fetches the LangSmith JWKS with blocking urllib; run the
    # verification off the event loop.
    return await asyncio.to_thread(
        principal_id_from_identity_context,
        raw_identity_context,
        key=identity_context_key,
        issuer=identity_context_issuer,
    )


def _resolve_assistant_id(override: str | None) -> str:
    if override is not None and (explicit := override.strip()):
        return explicit
    if configured := os.environ.get(ASSISTANT_ID_ENV, "").strip():
        return configured
    raise RuntimeError(f"{ASSISTANT_ID_ENV} is not configured")


def _header(request: object, name: str) -> str | None:
    headers = getattr(request, "headers", None)
    get = getattr(headers, "get", None)
    value = get(name) if callable(get) else None
    return value if isinstance(value, str) else None


def _nonblank_header(request: object, name: str) -> bool:
    value = _header(request, name)
    return isinstance(value, str) and bool(value.strip())


async def _request_body(request: object, raw_body: str | bytes | None) -> str:
    value: object = raw_body
    if value is None:
        body = getattr(request, "body", None)
        if not callable(body):
            raise ValueError("request body is unavailable")
        value = await body()
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    raise TypeError("request body must be text or bytes")


def _json_response(payload: Mapping[str, object], *, status_code: int) -> object:
    responses = import_module("starlette.responses")
    return responses.JSONResponse(dict(payload), status_code=status_code)


async def _best_effort(value: Awaitable[_T]) -> _T | None:
    try:
        return await value
    except TriggerClientError as error:
        suffix = "" if error.status is None else f" (HTTP {error.status})"
        logger.warning("Channel %s callback failed%s.", error.operation, suffix)
    except Exception:  # noqa: BLE001 - raw callback errors may contain secrets
        logger.warning("Channel callback failed.")
    return None


__all__ = [
    "ASSISTANT_ID_ENV",
    "TRIGGER_SERVICE_KEY_HEADER",
    "DecideMessageFn",
    "NewerRunFn",
    "OnlyContinuesThreadFn",
    "ThreadExistsFn",
    "TriggerChannelThread",
    "TriggerFinishedEvent",
    "TriggerMessageBackground",
    "TriggerMessageDecision",
    "TriggerMessageRoute",
    "handle_trigger_channel_event_request",
]
