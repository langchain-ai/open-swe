from __future__ import annotations

import copy
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from importlib import import_module
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast
from urllib.parse import quote

import httpx
from langgraph_sdk.schema import MultitaskStrategy

from managed_deepagents._agent_auth_http import ConnectionsNotConfiguredError
from managed_deepagents._channels.actor import endpoint_actor
from managed_deepagents._channels.github_app_setup import github_app_setup
from managed_deepagents._channels.github_connections import (
    GITHUB_TOKEN_SLUG,
    github_app_connection_slug,
)
from managed_deepagents._channels.github_events import github_event, github_installation_event
from managed_deepagents._channels.github_schema import (
    INSTALLATION_EVENT_KEYS,
    SUPPORTED_EVENT_KEYS,
    GitHubInstallationEventKey,
    github_identifier_adapter,
    github_receipt_adapter,
)
from managed_deepagents._channels.github_types import (
    GitHubAccount,
    GitHubAppOptions,
    GitHubChannelThread,
    GitHubEndpointFields,
    GitHubEvent,
    GitHubEventResult,
    GitHubHandlers,
    GitHubInstallationEvent,
    GitHubRepository,
    GitHubSender,
    SupportedEventKey,
)
from managed_deepagents._channels.http import HttpChannel, channel_thread_id, http_channel
from managed_deepagents._channels.http.provider import (
    EventFinished,
    EventReceived,
    channel_name,
    check_event_keys,
    event_snapshot,
    finish_events,
    read_deployment_secret,
    receive_events,
    reset_deployment_secrets,
    resolve_endpoints,
    run_event_handler,
    verify_hmac_sha256,
)
from managed_deepagents._channels.http.studio import interrupted_notice, pending_decision
from managed_deepagents._channels.http.types import ChannelName
from managed_deepagents._channels.resolve_user import checked_resolve_user
from managed_deepagents._channels.run_input import starts_on_conversation
from managed_deepagents._log import logger
from managed_deepagents._telemetry.otel import set_log_attributes

if TYPE_CHECKING:
    from managed_deepagents._channels.http.endpoint import EventEndpoint
    from managed_deepagents._channels.http.types import (
        HttpChannelInboundMessage,
        HttpChannelParseResult,
        HttpChannelRequest,
        HttpDelivery,
        HttpFinishedDelivery,
        JsonValue,
    )
    from managed_deepagents._channels.resolve_user import ResolveUser
    from managed_deepagents._channels.run_input import ChannelRunResult

_MAX_SAFE_INTEGER = 9007199254740991

_Handler = Callable[
    ["GitHubEvent | GitHubInstallationEvent", GitHubChannelThread],
    "GitHubEventResult | Awaitable[GitHubEventResult]",
]
"""A handler of the channel's: a repository event's or an installation event's."""


def github_channel(
    *,
    on_event: GitHubHandlers | None = None,
    app: GitHubAppOptions | None = None,
    multitask_strategy: MultitaskStrategy = "enqueue",
    on_finish: EventFinished[GitHubEvent, GitHubChannelThread] | None = None,
    on_received: EventReceived[GitHubEvent, GitHubChannelThread] | None = None,
    resolve_user: ResolveUser | EventEndpoint | None = None,
    include_own_events: bool = False,
) -> HttpChannel:
    """Options for ``channels.github(...)``: App scope and event handlers that start runs.

    Runs for the same issue or pull request share one thread.

    !!! warning "Experimental"

        The GitHub channel may change in a minor release.

    Args:
        on_event: Event handlers that start runs. Return a prompt (string,
            message, or state), or ``None`` to skip. MDA answers GitHub before it
            asks the handler, so a handler may take longer than GitHub waits; one
            that raises starts nothing, and its error is logged. While a thread waits on an
            interrupt, a run for that thread doesn't start and the issue or
            pull request is told to continue it in LangSmith Studio; runs on
            other threads still start. A handler may also be an
            endpoint, a URL or an ``httpx.Request`` carrying headers: MDA POSTs it
            the event and its thread, and starts the run it answers with.
        app: App ``mda deploy`` creates; permissions and repository allowlist.
        multitask_strategy: What an event that starts a run does while its
            conversation already runs: ``enqueue`` (the default) waits for the
            run and starts after it, ``interrupt`` cancels the run, keeping its
            progress, and starts at once, ``rollback`` cancels it and discards
            its progress, and ``reject`` starts nothing. Events on a
            conversation that waits on an interrupt start nothing either way.
        on_finish: A delivery of a handled repository event is done, once, with the
            event, its thread, the run it started and how that run ended, as
            LangGraph names it, and the decisions the thread waits on. A
            delivery that started no run, such as one whose handler returned
            ``None``, has no ``run``. Called after the App's own notices, such
            as the comment that a decision is pending, in the background;
            failures are logged. See ``http_channel``'s ``on_finish`` for its
            limits.
        on_received: A delivery of a handled repository event reached its thread, once,
            with the event and its thread, before MDA reads the thread or asks
            the handler: the moment to acknowledge it, such as with a 👀
            reaction. It fires even when the thread then waits on an
            interrupt, or the handler declines, so apply your own filters,
            such as a mention check. In the background, before ``on_finish``;
            failures are logged and don't stop the run.
        include_own_events: Also hand handlers the events the channel's own
            App caused, such as a push or a pull request the agent made with
            the App's token, with ``event.from_app`` set. Off by default, so
            the agent's own activity starts nothing. A handler that answers
            comments or reviews skips ``event.from_app``, or the agent answers
            itself.

    Example::

        channels.github(
            on_event={
                "issues.opened": lambda e, _: f"Help with #{e.payload['issue']['number']}",
            },
        )
    """
    handlers: Mapping[str, _Handler] = resolve_endpoints(
        "GitHub",
        # By key; the channel calls each only with its own key's events.
        cast("Mapping[str, _Handler | EventEndpoint]", on_event or {}),
        _endpoint_fields,
    )
    check_event_keys("GitHub", handlers, SUPPORTED_EVENT_KEYS | INSTALLATION_EVENT_KEYS)
    app_setup = github_app_setup(handlers, app)
    repositories = frozenset(app_setup["repositories"]) if "repositories" in app_setup else None

    def name() -> str:
        return channel_name(channel)

    async def parse(ctx: HttpChannelRequest) -> HttpChannelParseResult:
        return await _parse(ctx, handlers, name(), repositories, include_own_events)

    async def run_input(
        message: HttpChannelInboundMessage, raw_event: JsonValue | None
    ) -> ChannelRunResult | None:
        return await _handler_input(
            handlers,
            raw_event,
            GitHubChannelThread(id=message["thread_id"], channel=ChannelName(name=name())),
        )

    # Deliveries whose handler was asked while their thread waited: a run it
    # put on that thread is what found it waiting, so the notice needn't ask again.
    asked_interrupted: set[str] = set()

    async def interrupted_input(
        message: HttpChannelInboundMessage, raw_event: JsonValue | None
    ) -> ChannelRunResult | None:
        delivery_id = _delivery_id(raw_event)
        if delivery_id is not None:
            asked_interrupted.add(delivery_id)
        return await _handler_input(
            handlers,
            raw_event,
            GitHubChannelThread(
                id=message["thread_id"], channel=ChannelName(name=name()), interrupted=True
            ),
        )

    async def notify_interrupted(target: JsonValue, delivery: HttpFinishedDelivery) -> None:
        """Tell the issue or pull request that a new event found its thread waiting on a decision.

        A run that stops on interrupts posts its own notice as it ends. Only
        for an event its handler would start a run on the thread for, so the
        handler's filters, such as trusted senders or mentions, decide who
        gets a notice. A handler asked once its thread waited already said so;
        any other is asked here, knowing it is only asked, so it can skip side
        effects.
        """
        delivery_id = _delivery_id(delivery.get("raw_event"))
        asked = delivery_id in asked_interrupted
        asked_interrupted.discard(delivery_id or "")
        pending = pending_decision(delivery)
        if pending is None:
            return
        if not asked:
            thread = GitHubChannelThread(
                id=delivery["thread_id"], channel=ChannelName(name=name()), interrupted=True
            )
            result = await _handler_input(handlers, delivery.get("raw_event"), thread)
            if not starts_on_conversation(result):
                return
        await _post_interrupted_notice(target, delivery, pending)

    channel = http_channel(
        provider="github",
        verify=_verify,
        parse=parse,
        input=run_input,
        on_received=receive_events(
            on_received,
            _event,
            lambda thread_id: GitHubChannelThread(id=thread_id, channel=ChannelName(name=name())),
        ),
        on_finish=finish_events(
            notify_interrupted,
            on_finish,
            _event,
            lambda delivery: GitHubChannelThread(
                id=delivery["thread_id"], channel=ChannelName(name=name())
            ),
        ),
        # mda deploy sets up the GitHub App from this.
        provider_config=cast("dict[str, JsonValue]", app_setup),
        multitask_strategy=multitask_strategy,
    )
    channel.paused_notice = _post_paused_notice
    channel.interrupted_input = interrupted_input
    # Deploy provides the App's token at this name.
    channel.agent_connections = [GITHUB_TOKEN_SLUG]
    channel.resolve_user = checked_resolve_user("channels.github", resolve_user)
    return channel


async def _post_paused_notice(target: JsonValue, delivery: HttpDelivery) -> None:
    """A run stopped on interrupts. The handler already accepted the event that
    started it, so there is nothing to ask it again."""
    await _post_interrupted_notice(target, delivery, "interrupted")


def _endpoint_fields(event: GitHubEvent | GitHubInstallationEvent) -> GitHubEndpointFields:
    """The fields of a GitHub event its endpoints are sent beside key, delivery ID
    and payload: the one place they're listed, as TypeScript's ``ENDPOINT_EVENT_FIELDS``."""
    if isinstance(event, GitHubInstallationEvent):
        return {
            "action": event.action,
            "account": {"id": event.account.id, "login": event.account.login},
            "sender": {
                "id": event.sender.id,
                "login": event.sender.login,
                "type": event.sender.type,
            },
            "actor": endpoint_actor(event.actor),
        }
    fields: GitHubEndpointFields = {
        "repository": {
            "id": event.repository.id,
            "owner": event.repository.owner,
            "name": event.repository.name,
            "fullName": event.repository.full_name,
            "private": event.repository.private,
        },
        "sender": {
            "id": event.sender.id,
            "login": event.sender.login,
            "type": event.sender.type,
        },
        "actor": endpoint_actor(event.actor),
        "mentions": list(event.mentions),
        "fromApp": event.from_app,
    }
    if event.action is not None:
        fields["action"] = event.action
    return fields


async def _github_app_value(env_key: str, suffix: str) -> str | None:
    """The App's webhook secret or bot ID.

    The ``GH_*`` env override wins; otherwise the agent secret Agent Auth stored
    when ``mda deploy`` created the App, at ``mda/github-app/{deployment}/{suffix}``.
    Only a missing connection reads as ``None``; credential store failures propagate.
    """
    return await read_deployment_secret(
        env_key,
        lambda deployment_id: github_app_connection_slug(deployment_id, suffix),
        f"GitHub App {suffix}",
    )


def reset_github_app_values() -> None:
    """Forget values read from Agent Auth; for tests."""
    reset_deployment_secrets()


def _delivery_label(delivery_id: str | None) -> str:
    """GitHub's delivery ID for a log line, or ``unknown`` when it is not one."""
    if delivery_id and re.fullmatch(r"[0-9a-fA-F-]{1,64}", delivery_id):
        return delivery_id
    return "unknown"


async def _verify(ctx: HttpChannelRequest) -> bool:
    # GitHub pings a new App before Agent Auth has its webhook secret, and a
    # ping starts nothing, so it needs no secret. Reading it here would also
    # remember the secret as missing and reject the first real events.
    if ctx.request.headers.get("x-github-event") == "ping":
        return True
    # Reject unsigned deliveries before reading the secret from Agent Auth.
    signature = ctx.request.headers.get("x-hub-signature-256", "")
    if re.fullmatch(r"sha256=[a-fA-F0-9]{64}", signature) is None:
        return False
    try:
        secret = await _github_app_value("GH_WEBHOOK_SECRET", "webhook-secret")
    except ConnectionsNotConfiguredError as error:
        # The message names only the missing setting, so it is safe to log.
        logger.error(
            "GitHub delivery %s cannot be verified: %s",
            _delivery_label(ctx.request.headers.get("x-github-delivery")),
            error,
        )
        raise
    if not secret:
        return False
    return verify_hmac_sha256(secret, ctx.raw_body, signature[7:])


def _sender_user_id(sender: GitHubSender) -> str:
    """Who the run acts for.

    GitHub signs each delivery, so its sender is verified: a person acts as
    ``github:<id>``, by the numeric ID that outlives login renames, and gets
    their own user memory where the agent allows it. Bots and organizations run
    as the channel.
    """
    return f"github:{sender.id}" if sender.type == "User" else "mda:channel"


async def _parse(
    ctx: HttpChannelRequest,
    handlers: Mapping[str, _Handler],
    channel_name: str,
    repositories: frozenset[str] | None,
    include_own_events: bool,
) -> HttpChannelParseResult:
    json_response = import_module("starlette.responses").JSONResponse

    envelope = github_receipt_adapter.validate_python(
        {
            "event": ctx.request.headers.get("x-github-event"),
            "delivery_id": ctx.request.headers.get("x-github-delivery"),
            "payload": await ctx.request.json(),
        },
        strict=True,
    )
    event = envelope["event"]
    record = envelope["payload"]
    _receipt(record, event, envelope["delivery_id"])
    response = json_response({"status": "received"}, status_code=200 if event == "ping" else 202)

    # Events nothing handles stop here, before run creation.
    # Events without an action, such as `push`, are keyed by their name.
    action = record.get("action")
    key = cast("SupportedEventKey", event if action is None else f"{event}.{action}")
    set_log_attributes({"provider_event_type": key})
    if handlers.get(key) is None:
        return {"type": "ignore", "response": response}

    parsed_event: GitHubEvent | GitHubInstallationEvent
    if key in INSTALLATION_EVENT_KEYS:
        # About the whole installation, so a channel limited to repositories gets it too.
        parsed_event = github_installation_event(
            cast("GitHubInstallationEventKey", key), record, envelope["delivery_id"]
        )
    else:
        parsed_event = github_event(key, record, envelope["delivery_id"])
        # Events from repositories the channel is not limited to start nothing.
        if (
            repositories is not None
            and parsed_event.repository.full_name.lower() not in repositories
        ):
            return {"type": "ignore", "response": response}

    # The App's own activity starts nothing unless the channel asks for it, so
    # the agent doesn't trigger itself.
    configured_bot_id = await _github_app_value("GH_APP_BOT_ID", "bot-id") or ""
    if not re.fullmatch(r"[1-9][0-9]*", configured_bot_id):
        raise TypeError(
            "GitHub App bot ID is unavailable; run `mda deploy` to store it or set GH_APP_BOT_ID"
        )
    bot_id = github_identifier_adapter.validate_python(int(configured_bot_id), strict=True)
    if parsed_event.sender.id == bot_id:
        # An installation event is a person's, never the App's own.
        if not include_own_events or not isinstance(parsed_event, GitHubEvent):
            return {"type": "ignore", "response": response}
        parsed_event = replace(parsed_event, from_app=True)

    # Preserve the original event even if a callback modifies its argument.
    snapshot = event_snapshot(parsed_event, "GitHub")

    # One thread per issue or pull request, so the agent keeps the
    # conversation. Other events get their own thread, keyed by the delivery so
    # a redelivery still finds the run it started.
    delivery_id = envelope["delivery_id"]
    number = _conversation_number(parsed_event) if isinstance(parsed_event, GitHubEvent) else None
    thread_id = channel_thread_id(
        channel_name,
        f"delivery/{delivery_id}"
        if number is None or not isinstance(parsed_event, GitHubEvent)
        else f"{parsed_event.repository.id}/{number}",
    )
    target: dict[str, JsonValue] | None = None
    if number is not None and isinstance(parsed_event, GitHubEvent):
        target = {"repository": parsed_event.repository.full_name, "number": number}
    # What the run starts with is decided once MDA knows the thread takes a new
    # turn.
    message: HttpChannelInboundMessage = {
        "thread_id": thread_id,
        "user_id": _sender_user_id(parsed_event.sender),
        "target": target,
        "delivery_key": delivery_id,
    }
    # A handler may take longer to decide than GitHub waits, so GitHub hears first.
    message["background"] = True
    return {
        "type": "message",
        "message": message,
        "raw_event": snapshot,
        "actor": parsed_event.actor,
        "response": response,
    }


async def _handler_input(
    handlers: Mapping[str, _Handler],
    raw_event: JsonValue | None,
    thread: GitHubChannelThread,
) -> ChannelRunResult | None:
    """What the event's handler starts a run with; ``None`` when it skips or fails."""
    event = _event(raw_event) or _installation_event(raw_event)
    handler = None if event is None else handlers.get(event.key)
    if event is None or handler is None:
        return None
    outcome = await run_event_handler(
        "GitHub", event.key, event.delivery_id, lambda: handler(event, thread)
    )
    return outcome.input


def _delivery_id(raw_event: JsonValue | None) -> str | None:
    delivery_id = raw_event.get("delivery_id") if isinstance(raw_event, Mapping) else None
    return delivery_id if isinstance(delivery_id, str) else None


def _event(raw_event: JsonValue | None) -> GitHubEvent | None:
    """The parsed repository event a delivery carries, as callbacks may change it.

    ``None`` for an installation event, so ``on_received`` and ``on_finish`` get
    only repository events.
    """
    if not isinstance(raw_event, Mapping) or raw_event.get("key") in INSTALLATION_EVENT_KEYS:
        return None
    raw = cast("Mapping[str, object]", copy.deepcopy(dict(raw_event)))
    try:
        return GitHubEvent(
            key=cast("SupportedEventKey", raw["key"]),
            name=cast("str", raw["name"]),
            action=cast("str", raw["action"]),
            delivery_id=cast("str", raw["delivery_id"]),
            installation_id=cast("int", raw["installation_id"]),
            repository=GitHubRepository(**cast("dict[str, Any]", raw["repository"])),
            sender=GitHubSender(**cast("dict[str, Any]", raw["sender"])),
            payload=cast("Any", raw["payload"]),
            mentions=tuple(cast("list[str]", raw.get("mentions", ()))),
            from_app=raw.get("from_app") is True,
        )
    except (KeyError, TypeError):
        return None


def _installation_event(raw_event: JsonValue | None) -> GitHubInstallationEvent | None:
    """The parsed installation event a delivery carries, as callbacks may change it."""
    if not isinstance(raw_event, Mapping) or raw_event.get("key") not in INSTALLATION_EVENT_KEYS:
        return None
    raw = cast("Mapping[str, object]", copy.deepcopy(dict(raw_event)))
    try:
        return GitHubInstallationEvent(
            key=cast("GitHubInstallationEventKey", raw["key"]),
            name=cast("str", raw["name"]),
            action=cast("str", raw["action"]),
            delivery_id=cast("str", raw["delivery_id"]),
            installation_id=cast("int", raw["installation_id"]),
            account=GitHubAccount(**cast("dict[str, Any]", raw["account"])),
            sender=GitHubSender(**cast("dict[str, Any]", raw["sender"])),
            payload=cast("Any", raw["payload"]),
        )
    except (KeyError, TypeError):
        return None


def _conversation_number(event: GitHubEvent) -> int | None:
    """The issue or pull request an event is about, if any."""
    payload = cast("Mapping[str, object]", event.payload)
    for subject in ("issue", "pull_request"):
        record = payload.get(subject)
        number = record.get("number") if isinstance(record, Mapping) else None
        if _is_issue_number(number):
            return cast("int", number)
    # A CI run belongs to a pull request only when it ran for exactly one.
    for kind in ("workflow_run", "check_run", "check_suite"):
        run = payload.get(kind)
        pull_requests = run.get("pull_requests") if isinstance(run, Mapping) else None
        if isinstance(pull_requests, list) and len(pull_requests) == 1:
            only = pull_requests[0]
            number = only.get("number") if isinstance(only, Mapping) else None
            if _is_issue_number(number):
                return cast("int", number)
    return None


def _is_issue_number(value: object) -> bool:
    return type(value) is int and 0 < value <= _MAX_SAFE_INTEGER


class _GitHubTarget(TypedDict):
    """Where notices go: the issue or pull request of the conversation."""

    repository: str
    number: int


_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
_GITHUB_API_TIMEOUT_SECONDS = 10.0


async def _post_interrupted_notice(
    target: JsonValue, delivery: HttpDelivery, outcome: Literal["interrupted", "waiting"]
) -> None:
    """The thread waits on an interrupt, so tell the issue or pull request where to continue it.

    Posts as the App with the agent's installation token; the notice is fixed text.
    """
    if not _is_github_target(target):
        return
    from managed_deepagents._connections import connections  # noqa: PLC0415

    github_target = cast("_GitHubTarget", target)
    owner, name = github_target["repository"].split("/")
    token = await connections.resolve_deployment_secret(GITHUB_TOKEN_SLUG)
    async with httpx.AsyncClient(
        timeout=_GITHUB_API_TIMEOUT_SECONDS, follow_redirects=False
    ) as client:
        response = await client.post(
            f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
            f"/issues/{github_target['number']}/comments",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"body": interrupted_notice(outcome, delivery, "comment again")},
        )
    if not response.is_success:
        raise RuntimeError(f"GitHub refused the interrupted notice (HTTP {response.status_code})")


def _is_github_target(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    repository = value.get("repository")
    return (
        isinstance(repository, str)
        and _REPOSITORY_PATTERN.fullmatch(repository) is not None
        and _is_issue_number(value.get("number"))
    )


def _receipt(record: Mapping[str, object], event: str, delivery_id: str) -> dict[str, str | int]:
    receipt: dict[str, str | int] = {
        "event": logger.log_identifier(event),
        "deliveryId": logger.log_identifier(delivery_id),
    }
    action = record.get("action")
    if isinstance(action, str):
        receipt["action"] = logger.log_identifier(action)
    for key, field in (("repository", "id"), ("issue", "number"), ("pull_request", "number")):
        value = record.get(key)
        if isinstance(value, dict):
            receipt_id = value.get(field)
            if type(receipt_id) is int and abs(receipt_id) <= _MAX_SAFE_INTEGER:
                receipt[key] = receipt_id
    logger.info("github receipt %s", json.dumps(receipt))
    return receipt
