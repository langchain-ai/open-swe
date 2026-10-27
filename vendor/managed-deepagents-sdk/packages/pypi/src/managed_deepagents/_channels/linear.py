"""Linear app webhooks and agent sessions as an HTTP channel.

One conversation per issue, and one per agent session.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from importlib import import_module
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict, cast

from langgraph_sdk.schema import MultitaskStrategy
from pydantic import ValidationError
from typing_extensions import TypeIs

from managed_deepagents._channel_icon import validate_channel_icon_path
from managed_deepagents._channels.actor import endpoint_actor
from managed_deepagents._channels.http import HttpChannel, channel_thread_id, http_channel
from managed_deepagents._channels.http.endpoint import is_endpoint_handler
from managed_deepagents._channels.http.provider import (
    EventFinished,
    EventHandlerOutcome,
    channel_name,
    check_event_keys,
    event_snapshot,
    finish_events,
    read_deployment_secret,
    reset_deployment_secrets,
    resolve_endpoints,
    run_event_handler,
    verify_hmac_sha256,
)
from managed_deepagents._channels.http.studio import (
    interrupted_notice,
    pending_decision,
    studio_url,
)
from managed_deepagents._channels.http.types import ChannelName
from managed_deepagents._channels.linear_app_setup import linear_app_setup
from managed_deepagents._channels.linear_connections import (
    LINEAR_TOKEN_SLUG,
    linear_app_connection_slug,
)
from managed_deepagents._channels.linear_graphql import (
    LinearAppUser,
    OutgoingActivity,
    add_linear_session_url,
    comment_in_agent_session,
    create_linear_activity,
    create_linear_comment,
    linear_app_user,
)
from managed_deepagents._channels.linear_schema import (
    SESSION_EVENT_KEYS,
    SUPPORTED_EVENT_KEYS,
    LinearActorPayload,
    LinearAgentSession,
    LinearEventKey,
    LinearPerson,
    LinearTeamPayload,
    is_event_key,
    linear_receipt_adapter,
    validate_payload,
)
from managed_deepagents._channels.linear_types import (
    LinearActor,
    LinearAppOptions,
    LinearChannelThread,
    LinearCommentCreateEvent,
    LinearEndpointFields,
    LinearEvent,
    LinearEventCallback,
    LinearHandlers,
    LinearIssueCreateEvent,
    LinearIssueUpdateEvent,
    LinearSessionCreatedEvent,
    LinearSessionPromptedEvent,
    LinearTeam,
)
from managed_deepagents._channels.runtime import require_text_content
from managed_deepagents._log import logger

if TYPE_CHECKING:
    from managed_deepagents._channels.http.endpoint import EventEndpoint
    from managed_deepagents._channels.http.types import (
        HttpChannelInboundMessage,
        HttpChannelParseResult,
        HttpChannelPostInput,
        HttpChannelRequest,
        HttpDelivery,
        HttpFinishedDelivery,
        HttpPostedMessage,
        JsonValue,
    )
    from managed_deepagents._channels.run_input import ChannelRunResult
    from managed_deepagents._channels.types import ChannelReply


def linear_channel(
    *,
    on_event: LinearHandlers | None = None,
    app: LinearAppOptions | None = None,
    multitask_strategy: MultitaskStrategy = "enqueue",
    reply: ChannelReply = "auto",
    sessions: bool = True,
    ignore_bots: bool = True,
    on_finish: EventFinished[LinearEvent, LinearChannelThread] | None = None,
) -> HttpChannel:
    """Options for ``channels.linear(...)``: the app's scope, event handlers and sessions.

    Runs for the same issue share one thread, and each agent session has its
    own. A run's reply is posted as a comment on the issue, under the comment
    that started it, or in the agent session that asked.

    Args:
        on_event: Event handlers that start runs. Return a prompt (string,
            message, or state), or ``None`` to skip. A handler may also be an
            endpoint, a URL or an ``httpx.Request`` carrying headers: MDA POSTs it
            the event and its thread, and starts the run it answers with.
        app: The Linear app ``mda deploy`` sets up; team allowlist.
        multitask_strategy: What an event that starts a run does while its
            conversation already runs: ``enqueue`` (the default) waits for the
            run and starts after it, ``interrupt`` cancels the run, keeping its
            progress, and starts at once, ``rollback`` cancels it and discards
            its progress, and ``reject`` starts nothing.
        reply: Whether the channel posts the run's final answer, as a comment
            or the session's response: ``"auto"`` (the default) or ``"none"``,
            to leave replying to the agent. Session acknowledgements, Stop and
            error notices still post.
        sessions: Answer Linear agent sessions, which open when someone
            mentions the app or delegates an issue to it. Without a handler, a
            new session runs with Linear's prompt for it (``promptContext``), and
            a reply in the session continues the conversation. Stop cancels the
            session's run. A run that stops on interrupts answers with a link to
            its thread in LangSmith Studio, where they are resumed.
            Experimental: sessions use Linear's Agents API, a Developer
            Preview, so they may change as Linear changes it.
        ignore_bots: Ignore changes made by other apps and integrations (a
            delivery whose actor isn't a person, or the creation of an issue
            or comment with a ``botActor``), so automations don't trigger each
            other. The app's own changes are always ignored.
        on_finish: A delivery of a handled event is done, once, with the
            event, its thread, the run it started and how that run ended, as
            LangGraph names it, and the decisions the thread waits on. A
            delivery that started no run, such as a Stop or one whose handler
            returned ``None``, has no ``run``. Called after the channel's own
            activities, such as the session's elicitation or "Stopped.", in
            the background; failures are logged. See ``http_channel``'s
            ``on_finish`` for its limits.

    Example::

        channels.linear(
            app={"teams": ["ENG"]},
            on_event={
                "Issue.create": lambda e, _: f"Triage {e.payload['data']['identifier']}",
            },
        )
    """
    handlers: Mapping[str, LinearEventCallback] = resolve_endpoints(
        "Linear",
        # By key; the channel calls each only with its own key's events.
        cast("Mapping[str, LinearEventCallback | EventEndpoint]", on_event or {}),
        _endpoint_fields,
    )
    check_event_keys("Linear", handlers, SUPPORTED_EVENT_KEYS)
    app_setup = linear_app_setup(handlers, app, sessions=sessions)
    teams = (
        frozenset(team.strip().lower() for team in app["teams"])
        if app is not None and "teams" in app
        else None
    )
    filters = _Filters(teams=teams, ignore_bots=ignore_bots, sessions=sessions)

    def name() -> str:
        return channel_name(channel)

    async def parse(ctx: HttpChannelRequest) -> HttpChannelParseResult:
        return await _parse(ctx, handlers, name(), filters)

    async def run_input(
        message: HttpChannelInboundMessage, raw_event: JsonValue | None
    ) -> ChannelRunResult | None:
        thread = LinearChannelThread(id=message["thread_id"], channel=ChannelName(name=name()))
        return await _handler_input(handlers, raw_event, thread)

    channel = http_channel(
        provider="linear",
        verify=_verify,
        parse=parse,
        input=run_input,
        post=_post_reply,
        on_error=_report_error,
        on_received=_acknowledge_session,
        on_finish=finish_events(
            _notify_finished,
            on_finish,
            _event,
            lambda delivery: LinearChannelThread(
                id=delivery["thread_id"], channel=ChannelName(name=name())
            ),
        ),
        # mda deploy sets up the Linear app from this.
        provider_config=cast("dict[str, JsonValue]", app_setup),
        multitask_strategy=multitask_strategy,
        reply=reply,
    )
    channel.paused_notice = _notify_paused
    # Deploy provides the app's token at this name.
    channel.agent_connections = [LINEAR_TOKEN_SLUG]
    if app is not None and "icon" in app:
        # Served at /channels/{name}/icon; deploy points the app's icon at it.
        channel.icon = validate_channel_icon_path(app["icon"], "Linear app.icon")
    return channel


class _Filters:
    __slots__ = ("ignore_bots", "sessions", "teams")

    def __init__(self, *, teams: frozenset[str] | None, ignore_bots: bool, sessions: bool) -> None:
        self.teams = teams
        self.ignore_bots = ignore_bots
        self.sessions = sessions


async def _linear_webhook_secret() -> str | None:
    """The app's webhook signing secret.

    ``LINEAR_WEBHOOK_SECRET`` wins; otherwise the agent secret stored at
    ``mda/linear-app/{deployment}/webhook-secret``. Only a missing connection
    reads as ``None``; credential store failures propagate.
    """
    return await read_deployment_secret(
        "LINEAR_WEBHOOK_SECRET",
        lambda deployment_id: linear_app_connection_slug(deployment_id, "webhook-secret"),
        "Linear webhook secret",
    )


def reset_linear_webhook_secret() -> None:
    """Forget the cached webhook secret; for tests."""
    reset_deployment_secrets()


_WEBHOOK_TOLERANCE_SECONDS = 60.0
"""How far a delivery's ``webhookTimestamp`` may be from now. Linear recommends
rejecting older payloads, so a captured delivery can't be replayed."""


async def _verify(ctx: HttpChannelRequest) -> bool:
    # Reject unsigned deliveries before reading the secret from Agent Auth.
    signature = ctx.request.headers.get("linear-signature", "")
    if re.fullmatch(r"[0-9a-fA-F]{64}", signature) is None:
        return False
    secret = await _linear_webhook_secret()
    if not secret:
        return False
    if not verify_hmac_sha256(secret, ctx.raw_body, signature):
        return False
    return _is_fresh(ctx.raw_body)


def _is_fresh(raw_body: bytes) -> bool:
    """Whether a signed delivery was sent within the replay window."""
    try:
        body = json.loads(raw_body)
    except ValueError:
        return False
    timestamp = body.get("webhookTimestamp") if isinstance(body, Mapping) else None
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        return False
    return abs(time.time() - timestamp / 1000) <= _WEBHOOK_TOLERANCE_SECONDS


async def _parse(
    ctx: HttpChannelRequest,
    handlers: Mapping[str, LinearEventCallback],
    channel_name: str,
    filters: _Filters,
) -> HttpChannelParseResult:
    json_response = import_module("starlette.responses").JSONResponse

    receipt = linear_receipt_adapter.validate_python(
        {
            "delivery_id": ctx.request.headers.get("linear-delivery"),
            "payload": await ctx.request.json(),
        },
        strict=True,
    )
    delivery_id = receipt["delivery_id"]
    record = receipt["payload"]
    key = f"{record['type']}.{record['action']}"
    logger.info(
        "linear receipt %s",
        json.dumps(
            {
                "event": logger.log_identifier(key),
                "deliveryId": logger.log_identifier(delivery_id),
            }
        ),
    )
    # Linear expects a 200 for every delivery it should not retry.
    response = json_response({"status": "received"}, status_code=200)
    if key == "OAuthApp.revoked":
        logger.warning("Linear app access was revoked; reinstall it with mda deploy")
        return {"type": "ignore", "response": response}

    # Events nothing handles stop here, before run creation. Sessions have a
    # default handler while they are on.
    if not is_event_key(key) or (
        handlers.get(key) is None and not (filters.sessions and key in SESSION_EVENT_KEYS)
    ):
        return {"type": "ignore", "response": response}
    event = _linear_event(key, record, delivery_id)

    if await _ignored(event, filters):
        return {"type": "ignore", "response": response}

    # Preserve the original event even if a callback modifies its argument.
    snapshot = event_snapshot(event, "Linear")

    # One thread per agent session, so a Stop ends only its own session's work.
    # Other events share one thread per issue, so the agent keeps the
    # conversation.
    conversation = f"{event.organization_id}/{event.issue_id}"
    if isinstance(event, LinearSessionCreatedEvent | LinearSessionPromptedEvent):
        conversation = f"{conversation}/session/{event.session_id}"
    message: HttpChannelInboundMessage = {
        "thread_id": channel_thread_id(channel_name, conversation),
        "user_id": "mda:channel",
        "target": _target(event),
        "delivery_key": delivery_id,
    }
    # An endpoint may take longer to answer than Linear waits.
    if is_endpoint_handler(handlers.get(key)):
        message["background"] = True
    # Stop in a session cancels the session's run.
    if _is_stop(event):
        return {
            "type": "cancel",
            "message": {
                "user_id": message["user_id"],
                "thread_id": message["thread_id"],
                "target": message["target"],
            },
            "raw_event": snapshot,
            "response": response,
        }
    # What the run starts with, or how the event answers an interrupt, is
    # decided once MDA knows whether the thread waits on one.
    return {
        "type": "message",
        "message": message,
        "raw_event": snapshot,
        "response": response,
    }


def _by_bot(
    event: LinearIssueCreateEvent | LinearIssueUpdateEvent | LinearCommentCreateEvent,
) -> bool:
    """Whether another app or an integration made the change.

    The delivery's actor says who acted. An issue's or comment's ``botActor``
    is the bot that created it, so it names the actor only when the event is
    its creation.
    """
    kind = event.actor.type if event.actor else None
    if kind is not None and kind.lower() != "user":
        return True
    if isinstance(event, LinearIssueUpdateEvent):
        return False
    return event.payload["data"].get("botActor") is not None


async def _ignored(event: LinearEvent, filters: _Filters) -> bool:
    """Whether the event starts nothing.

    Another team's issue, another app's change, the app's own activity, or a
    comment an agent session already delivers.
    """
    if filters.teams is not None and (
        event.team is None or event.team.key.lower() not in filters.teams
    ):
        _log_ignored(
            event, "it names no team" if event.team is None else "its team is not in app.teams"
        )
        return True
    if isinstance(event, LinearSessionCreatedEvent | LinearSessionPromptedEvent):
        return False
    if filters.ignore_bots and _by_bot(event):
        return True
    # Linear reports the app's comments as its user's, not as a bot's.
    app_user = await linear_app_user()
    if event.actor is not None and event.actor.id == app_user["id"]:
        return True
    return isinstance(event, LinearCommentCreateEvent) and await _comment_ignored(
        event, app_user, sessions=filters.sessions
    )


async def _comment_ignored(
    event: LinearCommentCreateEvent, app_user: LinearAppUser, *, sessions: bool
) -> bool:
    """Whether a comment is the app's own, or one its agent session delivers."""
    comment = event.payload["data"]
    user = comment.get("user")
    if (user["id"] if user else comment.get("userId")) == app_user["id"]:
        return True
    if not sessions:
        return False
    reason = await _session_comment(comment["body"], comment.get("parentId"), app_user)
    if reason is not None:
        _log_ignored(event, f"its agent session delivers it: {reason}")
    return reason is not None


def _log_ignored(event: LinearEvent, reason: str) -> None:
    logger.info(
        "linear event ignored: %s %s",
        reason,
        json.dumps(
            {
                "event": logger.log_identifier(event.key),
                "deliveryId": logger.log_identifier(event.delivery_id),
            }
        ),
    )


async def _session_comment(body: str, parent_id: str | None, app_user: LinearAppUser) -> str | None:
    """Why a comment is part of an agent session, which delivers it too.

    One that mentions the app opens a session, and a reply in a session's
    thread prompts it. Only a reply without a mention asks Linear.
    """
    if _mentions_app(body, app_user["display_name"]):
        return "it mentions the app, which opens a session"
    if parent_id and await comment_in_agent_session(parent_id):
        return "it replies in a session's thread"
    return None


def _mentions_app(body: str, display_name: str) -> bool:
    if not display_name:
        return False
    pattern = rf"(^|[^\w@])@{re.escape(display_name)}(?![\w-])"
    return re.search(pattern, body, re.IGNORECASE) is not None


def _linear_event(key: LinearEventKey, record: object, delivery_id: str) -> LinearEvent:
    """The event a payload validated against its key's schema describes."""
    if key == "AgentSessionEvent.created":
        created = validate_payload(key, record)
        session = created["agentSession"]
        creator = session.get("creator")
        return LinearSessionCreatedEvent(
            payload=created,
            delivery_id=delivery_id,
            organization_id=created["organizationId"],
            issue_id=_session_issue_id(session),
            team=_team(_session_team(session)),
            actor=_person(creator["id"] if creator else session.get("creatorId"), creator),
            session_id=session["id"],
        )
    if key == "AgentSessionEvent.prompted":
        prompted = validate_payload(key, record)
        session = prompted["agentSession"]
        activity = prompted["agentActivity"]
        user = activity.get("user")
        return LinearSessionPromptedEvent(
            payload=prompted,
            delivery_id=delivery_id,
            organization_id=prompted["organizationId"],
            issue_id=_session_issue_id(session),
            team=_team(_session_team(session)),
            actor=_person(user["id"] if user else activity.get("userId"), user),
            session_id=session["id"],
        )
    if key == "Comment.create":
        comment = validate_payload(key, record)
        issue = comment["data"].get("issue")
        return LinearCommentCreateEvent(
            payload=comment,
            delivery_id=delivery_id,
            organization_id=comment["organizationId"],
            issue_id=comment["data"]["issueId"],
            team=_team(issue.get("team") if issue else None),
            actor=_actor(comment.get("actor")),
        )
    if key == "Issue.update":
        update = validate_payload(key, record)
        return LinearIssueUpdateEvent(
            payload=update,
            delivery_id=delivery_id,
            organization_id=update["organizationId"],
            issue_id=update["data"]["id"],
            team=_team(update["data"].get("team")),
            actor=_actor(update.get("actor")),
        )
    created_issue = validate_payload(key, record)
    return LinearIssueCreateEvent(
        payload=created_issue,
        delivery_id=delivery_id,
        organization_id=created_issue["organizationId"],
        issue_id=created_issue["data"]["id"],
        team=_team(created_issue["data"].get("team")),
        actor=_actor(created_issue.get("actor")),
    )


def _session_issue_id(session: LinearAgentSession) -> str:
    issue = session.get("issue")
    issue_id = issue["id"] if issue else session.get("issueId")
    # Linear opens sessions on issues; one without could not be answered.
    if not issue_id:
        raise TypeError("Linear agent session has no issue")
    return issue_id


def _session_team(session: LinearAgentSession) -> LinearTeamPayload | None:
    issue = session.get("issue")
    return issue.get("team") if issue else None


def _team(team: LinearTeamPayload | None) -> LinearTeam | None:
    return None if team is None else LinearTeam(id=team["id"], key=team["key"])


def _endpoint_fields(event: LinearEvent) -> LinearEndpointFields:
    """The fields of a Linear event its endpoints are sent beside key, delivery ID
    and payload: the one place they're listed, as TypeScript's
    ``LINEAR_ENDPOINT_EVENT_FIELDS``."""
    return {"actor": endpoint_actor(event.actor)} if event.actor is not None else {}


def _actor(actor: LinearActorPayload | None) -> LinearActor | None:
    if actor is None:
        return None
    return LinearActor(id=actor["id"], type=actor.get("type"), name=actor.get("name"))


def _person(person_id: str | None, person: LinearPerson | None) -> LinearActor | None:
    """Who started a session or replied in it."""
    if not person_id:
        return None
    return LinearActor(id=person_id, name=person.get("name") if person else None)


class _LinearTarget(TypedDict):
    """Where replies go: the issue, its comment thread, or the session that asked."""

    organizationId: str
    issueId: str
    identifier: NotRequired[str]
    commentId: NotRequired[str]
    sessionId: NotRequired[str]


def _target(event: LinearEvent) -> dict[str, JsonValue]:
    """Where replies go: the issue, its comment thread, or the session that asked."""
    target: dict[str, JsonValue] = {
        "organizationId": event.organization_id,
        "issueId": event.issue_id,
    }
    if isinstance(event, LinearSessionCreatedEvent | LinearSessionPromptedEvent):
        issue = event.payload["agentSession"].get("issue")
        identifier = issue.get("identifier") if issue else None
        target["sessionId"] = event.session_id
    elif isinstance(event, LinearCommentCreateEvent):
        data = event.payload["data"]
        issue = data.get("issue")
        identifier = issue.get("identifier") if issue else None
        # Replies go into the comment's thread; Linear threads one level deep.
        target["commentId"] = data.get("parentId") or data["id"]
    else:
        identifier = event.payload["data"]["identifier"]
    if identifier:
        target["identifier"] = identifier
    return target


async def _handler_input(
    handlers: Mapping[str, LinearEventCallback],
    raw_event: JsonValue | None,
    thread: LinearChannelThread,
) -> ChannelRunResult | None:
    """What the event's handler starts a run with; ``None`` when it skips or fails."""
    event = _event(raw_event)
    if event is None:
        return None
    handler = handlers.get(event.key)
    outcome = (
        _session_input(event)
        if handler is None
        else await run_event_handler(
            "Linear", event.key, event.delivery_id, lambda: handler(event, thread)
        )
    )
    if outcome.type == "input":
        return outcome.input
    # A session that gets no run would wait for an answer that never comes,
    # whether it just opened or this is a follow-up in it. A handler that
    # raised failed; one that returned nothing declined.
    if isinstance(event, LinearSessionCreatedEvent | LinearSessionPromptedEvent):
        await _say(
            event.session_id,
            {"type": "error", "body": _ERROR_MESSAGE}
            if outcome.type == "failed"
            else {"type": "response", "body": "I'm not set up to work on this request."},
        )
    return None


def _session_input(event: LinearEvent) -> EventHandlerOutcome:
    """What a session event starts a run with when no handler decides."""
    texts: list[str | None] = []
    if isinstance(event, LinearSessionCreatedEvent):
        session = event.payload["agentSession"]
        comment = session.get("comment")
        issue = session.get("issue")
        texts = [
            event.payload.get("promptContext"),
            comment.get("body") if comment else None,
            issue.get("title") if issue else None,
        ]
    elif isinstance(event, LinearSessionPromptedEvent):
        texts = [event.payload["agentActivity"]["content"].get("body")]
    for text in texts:
        if text and text.strip():
            return EventHandlerOutcome("input", text.strip())
    return EventHandlerOutcome("skipped")


def _event(raw_event: JsonValue | None) -> LinearEvent | None:
    """The parsed event a delivery carries, checked against its schema again.

    The HTTP layer hands over a copy, which callbacks may change.
    """
    if not isinstance(raw_event, Mapping):
        return None
    key = raw_event.get("key")
    delivery_id = raw_event.get("delivery_id")
    if not is_event_key(key) or not isinstance(delivery_id, str):
        return None
    try:
        return _linear_event(key, raw_event.get("payload"), delivery_id)
    except (ValidationError, TypeError):
        return None


async def _say(
    session_id: str, content: OutgoingActivity, *, ephemeral: bool | None = None
) -> None:
    """Say something in a session; a failure is logged, never raised."""
    try:
        await create_linear_activity(
            agent_session_id=session_id, content=content, ephemeral=ephemeral
        )
    except Exception as error:  # noqa: BLE001
        logger.error("linear session activity failed (%s)", logger.safe_error_label(error))


async def _post_reply(post: HttpChannelPostInput) -> HttpPostedMessage:
    """Post the agent's reply.

    As the session's response when a session asked, otherwise as a comment in
    the thread of the comment that started the run. A post that starts a new
    thread, such as a scheduled one, comments on the issue itself.
    """
    target = post["target"]
    if not _is_linear_target(target):
        raise ValueError("invalid Linear target")
    message = {key: value for key, value in post.items() if key not in ("target", "new_thread")}
    body = require_text_content(message)
    new_thread = bool(post.get("new_thread"))
    session_id = target.get("sessionId")
    if session_id and not new_thread:
        return await create_linear_activity(
            agent_session_id=session_id, content={"type": "response", "body": body}
        )
    return await create_linear_comment(
        issue_id=target["issueId"],
        body=body,
        parent_id=None if new_thread else target.get("commentId"),
    )


async def _acknowledge_session(target: JsonValue, delivery: HttpDelivery) -> None:
    """Acknowledge a session at once, and link a new one to its thread in Studio.

    Linear marks a session unresponsive without an activity within seconds,
    even while the run waits behind another one.
    """
    if not _is_linear_target(target) or "sessionId" not in target:
        return
    session_id = target["sessionId"]
    await _say(session_id, {"type": "thought", "body": "Working on it."}, ephemeral=True)
    url = studio_url(delivery)
    if url is None or not isinstance(_event(delivery.get("raw_event")), LinearSessionCreatedEvent):
        return
    try:
        await add_linear_session_url(session_id, label="LangSmith Studio", url=url)
    except Exception as error:  # noqa: BLE001
        logger.error("linear session link failed (%s)", logger.safe_error_label(error))


async def _notify_finished(target: JsonValue, delivery: HttpFinishedDelivery) -> None:
    """What the conversation hears once a delivery is done.

    Besides the run's own answer, its own paused notice and ``_report_error``:
    that a message found the thread waiting on a decision, or, after Stop, the
    final word Linear waits for.
    """
    if delivery["type"] == "skipped" and delivery["reason"] == "cancel":
        await _confirm_stopped(target)
        return
    # A session would keep "working" on a run that never started.
    # One Linear retries (``retry``) may still start, so it hears nothing yet.
    if delivery["type"] == "failed" and delivery["reason"] == "start":
        await _report_error(RuntimeError("channel run could not be started"), target)
        return
    pending = pending_decision(delivery)
    if pending is not None:
        await _notify_interrupted(target, delivery, pending)


async def _notify_paused(target: JsonValue, delivery: HttpDelivery) -> None:
    """A run stopped on interrupts, as it ended."""
    await _notify_interrupted(target, delivery, "interrupted")


def _is_stop(event: LinearEvent | None) -> bool:
    """A Stop pressed in a session; it cancels the session's run."""
    return (
        isinstance(event, LinearSessionPromptedEvent)
        and event.payload["agentActivity"].get("signal") == "stop"
    )


async def _confirm_stopped(target: JsonValue) -> None:
    """After Stop, Linear waits for a final word from the agent."""
    if _is_linear_target(target) and "sessionId" in target:
        await _say(target["sessionId"], {"type": "response", "body": "Stopped."})


_ERROR_MESSAGE = "Something went wrong while working on this."


async def _report_error(_error: Exception, target: JsonValue) -> None:
    """Tell the session or the issue that the run failed."""
    if not _is_linear_target(target):
        return
    if "sessionId" in target:
        await create_linear_activity(
            agent_session_id=target["sessionId"],
            content={"type": "error", "body": _ERROR_MESSAGE},
        )
        return
    await create_linear_comment(
        issue_id=target["issueId"], body=_ERROR_MESSAGE, parent_id=target.get("commentId")
    )


async def _notify_interrupted(
    target: JsonValue, delivery: HttpDelivery, outcome: Literal["interrupted", "waiting"]
) -> None:
    """The conversation waits on a decision, made in LangSmith Studio: say so with a link.

    In a session it asks for input, so the session waits rather than ends:
    once the decision is made, the resumed run's answer is the session's
    response. Otherwise it is a comment.
    """
    if not _is_linear_target(target):
        return
    body = interrupted_notice(outcome, delivery, "ask again")
    if "sessionId" in target:
        await create_linear_activity(
            agent_session_id=target["sessionId"], content={"type": "elicitation", "body": body}
        )
        return
    await create_linear_comment(
        issue_id=target["issueId"], body=body, parent_id=target.get("commentId")
    )


def _is_linear_target(value: object) -> TypeIs[_LinearTarget]:
    if not isinstance(value, Mapping):
        return False

    def nonblank(field: object) -> bool:
        return isinstance(field, str) and bool(field.strip())

    return (
        nonblank(value.get("organizationId"))
        and nonblank(value.get("issueId"))
        and all(
            key not in value or nonblank(value[key])
            for key in ("identifier", "commentId", "sessionId")
        )
    )
