"""Inbound Trigger message acknowledgement and run observation."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Awaitable, Callable, Mapping
from importlib import import_module
from typing import TYPE_CHECKING, Never, NoReturn, Protocol, TypeVar, cast

from managed_deepagents._agent_auth_http import missing_workspace_connection_slug
from managed_deepagents._channels.assistant_text import extract_reply_text, run_start_id
from managed_deepagents._channels.context import (
    normalized_user_prompt_response,
    trigger_channel_binding,
)
from managed_deepagents._channels.reactions import (
    DEFAULT_REACTION_EMOJI,
    ReactionChoice,
    resolve_reaction_emoji,
)
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
    ingress_resume_target,
    ingress_thread_interrupts,
    observe_ingress_agent,
    resume_ingress_agent,
    start_ingress_agent,
)
from managed_deepagents._channels.trigger.tracing_project import tracer_session_id
from managed_deepagents._channels.trigger.transport import TriggerTransport
from managed_deepagents._channels.trigger.types import (
    TRIGGER_SERVICE_KEY_HEADER,
    TriggerChannelEvent,
    TriggerChannelInterrupt,
    TriggerChannelMessageEvent,
    TriggerChannelResume,
    TriggerChannelRuntimeEventRequest,
    TriggerChannelRuntimeEventType,
    parse_trigger_channel_event,
)
from managed_deepagents._ingress import ScheduleFn, schedule_ingress_work
from managed_deepagents._log import logger
from managed_deepagents.run_context import INTERRUPT_CORRELATION_KEY

if TYPE_CHECKING:
    from managed_deepagents._channels.binding import ChannelBinding
    from managed_deepagents._channels.trigger.ingress import (
        ObservedIngressRun,
        PendingInterrupt,
        ResumeTarget,
    )
    from managed_deepagents._channels.types import JsonValue
    from managed_deepagents._ingress_runs import StartedIngressRun

ASSISTANT_ID_ENV = "MDA_ASSISTANT_ID"

ObserveAgentFn = Callable[..., Awaitable["ObservedIngressRun"]]
# Provider hook for a message event's run input; defaults to `input.messages`.
MessageInputFn = Callable[[TriggerChannelMessageEvent], list[dict[str, object]]]

_T = TypeVar("_T")


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
    if len(pending) > 1:
        # Interrupt ids carry no ordering, so without a correlation id there is
        # nothing to choose by.
        raise _AmbiguousInterruptError(f"{len(pending)} interrupts pending")
    return pending[0]["id"] if pending else None


def _interrupt_correlation_id(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    correlation_id = value.get(INTERRUPT_CORRELATION_KEY)
    if isinstance(correlation_id, str) and correlation_id.strip():
        return correlation_id
    return None


async def _start_event_run(
    *,
    event: TriggerChannelEvent,
    assistant_id: str,
    source_provider: str,
    channel_name: str,
    start_agent: StartAgentFn | None,
    resume_agent: ResumeAgentFn | None,
    user_id: str,
    thread_interrupts: ThreadInterruptsFn | None = None,
    resume_target: ResumeTargetFn | None = None,
    message_input: MessageInputFn | None = None,
) -> StartedIngressRun:
    thread_id = _thread_id(event["source_thread_key"])
    context: dict[str, object] = {}
    binding = trigger_channel_binding(provider=source_provider, name=channel_name, event=event)
    if event["type"] == "message":
        start = start_agent or start_ingress_agent
        return await start(
            thread_id=thread_id,
            assistant_id=assistant_id,
            messages=(
                message_input(event) if message_input is not None else event["input"]["messages"]
            ),
            source_provider=source_provider,
            source_thread_key=event["source_thread_key"],
            user_id=user_id,
            context=context,
            binding=binding,
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
    if event["type"] == "user_prompt_response":
        normalized = normalized_user_prompt_response(event)
        interrupt_id = await _interrupt_answered_by(
            thread_id=thread_id,
            source_provider=source_provider,
            source_thread_key=event["source_thread_key"],
            user_id=user_id,
            correlation_id=normalized.get("correlation_id"),
            thread_interrupts=thread_interrupts,
        )
        if interrupt_id is not None:
            resume = resume_agent or resume_ingress_agent
            return await resume(
                thread_id=thread_id,
                assistant_id=assistant_id,
                resume={interrupt_id: normalized},
                source_provider=source_provider,
                source_thread_key=event["source_thread_key"],
                user_id=user_id,
                context=context,
                binding=binding,
            )
        start = start_agent or start_ingress_agent
        return await start(
            thread_id=thread_id,
            assistant_id=assistant_id,
            messages=[],
            source_provider=source_provider,
            source_thread_key=event["source_thread_key"],
            user_id=user_id,
            context=context,
            binding=binding,
        )
    _assert_never(event)


async def handle_trigger_channel_event_request(  # noqa: C901, PLR0911
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
    resume_target: ResumeTargetFn | None = None,
    observe_agent: ObserveAgentFn | None = None,
    trigger_client: TriggerClientLike | None = None,
    reactions: ReactionChoice = DEFAULT_REACTION_EMOJI,
    message_input: MessageInputFn | None = None,
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
        user_id = _principal_for_event(
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

    logger.info(
        "Channel incoming event (type=%s, event_id=%s, delivery_id=%s, channel_id=%s)",
        event["type"],
        logger.log_identifier(event["event_id"]),
        logger.log_identifier(event["delivery_id"]),
        logger.log_identifier(event["channel_id"]),
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
        started = await _start_event_run(
            event=event,
            assistant_id=resolved_assistant_id,
            source_provider=source_provider,
            channel_name=channel_name,
            start_agent=start_agent,
            resume_agent=resume_agent,
            thread_interrupts=thread_interrupts,
            resume_target=resume_target,
            user_id=user_id,
            message_input=message_input,
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
        return _json_response({"error": "channel run could not be started"}, status_code=500)

    async def observe() -> None:
        await _observe_accepted_run(
            event=event,
            started=started,
            client=client,
            observe_agent=observe_agent,
            source_provider=source_provider,
            source_thread_key=event["source_thread_key"],
            user_id=user_id,
            reactions=reactions,
        )

    schedule_ingress_work(
        observe,
        label="Channel run observation",
        schedule=schedule,
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
) -> None:
    async def lifecycle(
        event_type: TriggerChannelRuntimeEventType,
        interrupts: list[TriggerChannelInterrupt] | None = None,
        message: str | None = None,
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
            interrupts,
            message,
            tracer_session_id=await tracer_session_id(),
        )
        await _best_effort(client.post_runtime_event(event["channel_id"], request))

    await lifecycle("run.started")
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
            await lifecycle("run.failed", message=_missing_connection_failure_message(error))
            return

        if _is_interrupted_run(observed):
            await lifecycle("run.interrupted", _forwarded_interrupts(observed["values"]))
            return

        if observed["status"] != "success":
            await lifecycle("run.failed")
            return

        text = extract_reply_text(observed["values"], start_id=run_start_id(observed["values"]))
        if text:
            logger.info(
                "Channel outgoing action (type=post, delivery_id=%s, channel_id=%s, run_id=%s)",
                logger.log_identifier(event["delivery_id"]),
                logger.log_identifier(event["channel_id"]),
                logger.log_identifier(started["run_id"]),
            )
            action = await _best_effort(
                TriggerTransport(client, action_id=event["event_id"]).post(
                    message={"type": "content", "content": text},
                    target=cast(
                        "JsonValue",
                        {
                            "channel_id": event["channel_id"],
                            "address": event["address"],
                            "source_thread_key": event["source_thread_key"],
                        },
                    ),
                )
            )
            if action is None:
                await lifecycle("run.failed")
                return
        await lifecycle("run.completed")
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


def _runtime_event(
    event_type: TriggerChannelRuntimeEventType,
    event: TriggerChannelEvent,
    started: StartedIngressRun,
    interrupts: list[TriggerChannelInterrupt] | None = None,
    message: str | None = None,
    *,
    tracer_session_id: str | None = None,
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


def _thread_id(source_thread_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, source_thread_key))


class _MissingIdentityContextError(ValueError):
    """A message event arrived without a Trigger identity_context token."""


def _principal_for_event(
    event: TriggerChannelEvent,
    *,
    identity_context_key: IdentityContextKey | None,
    identity_context_issuer: str | None,
) -> str:
    """Resolve the verified principal required on every Trigger delivery."""
    raw_identity_context = event.get("identity_context")
    if not isinstance(raw_identity_context, str) or not raw_identity_context.strip():
        raise _MissingIdentityContextError
    return principal_id_from_identity_context(
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
    "handle_trigger_channel_event_request",
]
