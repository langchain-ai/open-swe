"""The Linear channel: webhook verification, routing, filters, and replies."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import TypeAdapter
from starlette.requests import Request

from managed_deepagents import LinearChannelThread, LinearRateLimitError, _ingress, channels
from managed_deepagents._channel import collect_channels, requirements_from_channels
from managed_deepagents._channels import linear as linear_module
from managed_deepagents._channels import linear_graphql, messaging_registry
from managed_deepagents._channels.http import ChannelName, channel_thread_id
from managed_deepagents._channels.http import ingress as http_ingress
from managed_deepagents._channels.linear_schema import SUPPORTED_EVENT_KEYS
from managed_deepagents._connections import ConnectionNotFoundError, connections

if TYPE_CHECKING:
    from collections.abc import Iterator

    from starlette.responses import Response

    from managed_deepagents._channels.http import HttpChannel

_OK = TypeAdapter(dict[str, int])
ORG = "18ce88dc-79cf-4fa2-887d-7a9dfb017836"
ISSUE = "820b5572-b9fc-40b4-8f21-a6c9c045a7cd"
APP_USER = "29852591-5abc-4286-812b-b4c671778f7d"
PERSON = "7a3a2607-ca96-49ba-b2df-09a2dd3e678c"
DEPLOYMENT = "22222222-2222-4222-8222-222222222222"
SECRET = "lin_wh_test-secret"
SESSION = "d19c03fb-11a4-458c-af12-b7d7ce4fcdf0"
TEAM = {"id": "ce66e076-9a43-4993-9832-cbe804ab12de", "key": "MYT"}


def payload(key: str = "Issue.create", now_ms: float | None = None) -> dict[str, Any]:
    """A payload shaped like Linear's, as captured from a test workspace."""
    kind, action = key.split(".")
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    issue = {
        "id": ISSUE,
        "identifier": "MYT-5",
        "title": "untrusted title",
        "description": "private payload",
        "url": "https://linear.app/acme/issue/MYT-5/first-issue",
        "teamId": TEAM["id"],
        "team": dict(TEAM),
        "labelIds": ["label-bug"],
        "labels": [{"id": "label-bug", "name": "Bug"}],
        "botActor": None,
    }
    comment = {
        "id": "comment-1",
        "body": "untrusted comment",
        "issueId": ISSUE,
        "parentId": None,
        "userId": PERSON,
        "user": {"id": PERSON, "name": "Person"},
        "issue": {"id": ISSUE, "identifier": "MYT-5", "title": "t", "team": dict(TEAM)},
        "botActor": None,
        "isArtificialAgentSessionRoot": False,
    }
    record: dict[str, Any] = {
        "action": action,
        "type": kind,
        "organizationId": ORG,
        "webhookId": "webhook-1",
        "webhookTimestamp": int(now_ms),
        "url": issue["url"],
        "actor": {"id": PERSON, "type": "user", "name": "Person"},
        "data": comment if kind == "Comment" else issue,
    }
    if action == "update":
        record["updatedFrom"] = {"labelIds": []}
    if kind == "AgentSessionEvent":
        record = session_payload(action, now_ms)
    return record


def session_payload(action: str, now_ms: float) -> dict[str, Any]:
    """An agent session event, shaped like Linear's."""
    record: dict[str, Any] = {
        "type": "AgentSessionEvent",
        "action": action,
        "organizationId": ORG,
        "oauthClientId": "client-1",
        "appUserId": APP_USER,
        "webhookId": "webhook-1",
        "webhookTimestamp": int(now_ms),
        "agentSession": {
            "id": SESSION,
            "status": "pending",
            "issueId": ISSUE,
            "issue": {
                "id": ISSUE,
                "identifier": "MYT-5",
                "title": "untrusted title",
                "description": "",
                "team": dict(TEAM),
            },
            "commentId": "comment-root",
            "comment": {"id": "comment-root", "body": "@mdatestagent hello"},
            "creatorId": PERSON,
            "creator": {"id": PERSON, "name": "Person"},
        },
    }
    if action == "created":
        record["promptContext"] = '<issue identifier="MYT-5"><title>untrusted title</title></issue>'
        record["previousComments"] = []
        record["guidance"] = None
    else:
        record["agentActivity"] = {
            "content": {"type": "prompt", "body": "go ahead"},
            "signal": None,
            "sourceCommentId": "comment-reply",
            "userId": PERSON,
            "user": {"id": PERSON, "name": "Person"},
        }
    return record


def sign(raw: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), raw, "sha256").hexdigest()


async def send(
    channel: HttpChannel,
    data: object = None,
    *,
    delivery: str = "delivery-1",
    signature: str | None = None,
) -> Response:
    if channel.name is None:
        collect_channels([("channels/mylinear.py", SimpleNamespace(channel=channel))])
    raw = json.dumps(payload() if data is None else data).encode()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/channels/mylinear/events",
            "headers": [
                (b"linear-delivery", delivery.encode()),
                (b"linear-signature", (sign(raw) if signature is None else signature).encode()),
            ],
        }
    )
    return await channel.events(
        {"name": "mylinear", "channel": channel, "request": request, "raw_body": raw}
    )


@pytest.fixture
def ingress(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    monkeypatch.setenv("MDA_ASSISTANT_ID", "agent")
    monkeypatch.setenv("LINEAR_WEBHOOK_SECRET", SECRET)
    prepared: list[object] = []

    async def start(**kwargs: Any) -> dict[str, object]:
        prepare = kwargs.get("prepare")
        input_ = None if prepare is None else await prepare()
        prepared.append(input_)
        if input_ is None:
            return {"skipped": "ignored"}
        return {"thread_id": kwargs["thread_id"], "run_id": "run-1"}

    start_mock = AsyncMock(side_effect=start)
    observe = AsyncMock(return_value={"status": "success", "interrupts": []})
    monkeypatch.setattr(http_ingress, "start_http_ingress_agent", start_mock)
    monkeypatch.setattr(http_ingress, "observe_http_ingress_agent", observe)
    # Nobody resumes a paused thread here.
    jobs: list[Any] = []
    monkeypatch.setattr(_ingress, "schedule_ingress_work", lambda work, **_: jobs.append(work))
    app_user = AsyncMock(return_value={"id": APP_USER, "display_name": "mdatestagent"})
    comment = AsyncMock(return_value={"id": "c-new", "url": "https://linear.app/c"})
    activity = AsyncMock(return_value={"id": "activity-1"})
    in_session = AsyncMock(return_value=False)
    session_url = AsyncMock()
    cancel = AsyncMock(return_value=["run-1"])
    monkeypatch.setattr(linear_module, "linear_app_user", app_user)
    monkeypatch.setattr(linear_module, "create_linear_comment", comment)
    monkeypatch.setattr(linear_module, "create_linear_activity", activity)
    monkeypatch.setattr(linear_module, "comment_in_agent_session", in_session)
    monkeypatch.setattr(linear_module, "add_linear_session_url", session_url)
    monkeypatch.setattr(http_ingress, "cancel_http_ingress_runs", cancel)
    monkeypatch.setattr(
        http_ingress, "http_ingress_thread_waits_on", AsyncMock(return_value={"interrupts": []})
    )
    linear_module.reset_linear_webhook_secret()
    yield SimpleNamespace(
        start=start_mock,
        observe=observe,
        jobs=jobs,
        prepared=prepared,
        comment=comment,
        activity=activity,
        in_session=in_session,
        session_url=session_url,
        cancel=cancel,
    )
    linear_module.reset_linear_webhook_secret()


async def drain(ingress: SimpleNamespace) -> None:
    while ingress.jobs:
        await ingress.jobs.pop(0)()


@pytest.mark.parametrize("key", sorted(SUPPORTED_EVENT_KEYS))
def test_dispatches_each_event_to_its_handler_on_its_conversations_thread(
    ingress: SimpleNamespace, key: str
) -> None:
    seen: list[Any] = []

    def handler(event: Any, thread: Any) -> str:
        seen.append((event, thread))
        return "prompt"

    channel = channels.linear(on_event={key: handler})  # ty: ignore[invalid-argument-type]
    response = asyncio.run(send(channel, payload(key)))
    assert response.status_code == 200
    assert json.loads(bytes(response.body)) == {"status": "received"}
    event, thread = seen[0]
    assert (event.key, event.delivery_id, event.organization_id, event.issue_id) == (
        key,
        "delivery-1",
        ORG,
        ISSUE,
    )
    assert (event.team.id, event.team.key) == (TEAM["id"], "MYT")
    assert event.actor.id == PERSON
    assert event.actor.name == "Person"
    assert thread.channel.name == "mylinear"
    start = ingress.start.call_args.kwargs
    # A session is a conversation of its own; other events share the issue's.
    conversation = f"{ORG}/{ISSUE}"
    if key.startswith("AgentSessionEvent"):
        conversation = f"{conversation}/session/{SESSION}"
    assert start["thread_id"] == channel_thread_id("mylinear", conversation)
    # The thread the run starts on, so run context can name it.
    assert thread.id == start["thread_id"]
    assert start["user_id"] == "mda:channel"
    assert start["delivery_key"] == "delivery-1"
    expected_target = {"organizationId": ORG, "issueId": ISSUE, "identifier": "MYT-5"}
    if key == "Comment.create":
        expected_target["commentId"] = "comment-1"
    if key.startswith("AgentSessionEvent"):
        expected_target["sessionId"] = SESSION
        assert event.session_id == SESSION
    assert start["binding"]["target"] == expected_target


def test_threads_a_reply_to_a_reply_under_the_threads_first_comment(
    ingress: SimpleNamespace,
) -> None:
    channel = channels.linear(on_event={"Comment.create": lambda _e, _r: "prompt"})
    reply = payload("Comment.create")
    reply["data"]["parentId"] = "comment-root"
    asyncio.run(send(channel, reply))
    assert ingress.start.call_args.kwargs["binding"]["target"]["commentId"] == "comment-root"


def test_rejects_deliveries_that_are_unsigned_mis_signed_or_stale(
    ingress: SimpleNamespace,
) -> None:
    channel = channels.linear(on_event={"Issue.create": lambda _e, _r: "prompt"})
    body = payload()
    raw = json.dumps(body).encode()
    for signature in ("", "sha256=abc", sign(raw, "wrong")):
        assert asyncio.run(send(channel, body, signature=signature)).status_code == 401
    now_ms = time.time() * 1000
    # A captured delivery can't be replayed after the window.
    for stale in (now_ms - 61_000, now_ms + 61_000):
        assert asyncio.run(send(channel, payload(now_ms=stale))).status_code == 401
    unstamped = {key: value for key, value in body.items() if key != "webhookTimestamp"}
    assert asyncio.run(send(channel, unstamped)).status_code == 401
    ingress.start.assert_not_called()
    assert asyncio.run(send(channel, body)).status_code == 200


def test_reads_the_webhook_secret_from_agent_auth_once_and_fails_closed(
    ingress: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LINEAR_WEBHOOK_SECRET", "")
    channel = channels.linear(on_event={"Issue.create": lambda _e, _r: "prompt"})
    # Without a deployment there is nowhere to read it from.
    assert asyncio.run(send(channel)).status_code == 401

    monkeypatch.setenv("LANGSMITH_HOST_PROJECT_ID", DEPLOYMENT)
    read = AsyncMock(return_value=SECRET)
    monkeypatch.setattr(connections, "resolve_deployment_secret", read)

    async def twice() -> list[int]:
        return [
            (await send(channel, payload(), delivery=delivery)).status_code
            for delivery in ("d-1", "d-2")
        ]

    assert asyncio.run(twice()) == [200, 200]
    read.assert_awaited_once_with(f"mda/linear-app/{DEPLOYMENT}/webhook-secret")

    linear_module.reset_linear_webhook_secret()
    read.side_effect = ConnectionNotFoundError("missing", "agent")
    assert asyncio.run(send(channel)).status_code == 401


def test_starts_nothing_for_unhandled_events_bots_or_the_apps_own_activity(
    ingress: SimpleNamespace,
) -> None:
    handled: list[object] = []

    def handler(event: object, _thread: object) -> str:
        handled.append(event)
        return "prompt"

    channel = channels.linear(on_event={"Issue.create": handler, "Comment.create": handler})
    by_bot = payload()
    by_bot["data"]["botActor"] = {"id": "bot", "type": "x"}
    own_comment = payload("Comment.create")
    own_comment["data"]["user"] = {"id": APP_USER, "name": "MDA Test Agent"}
    for record in (
        # Unhandled and unknown events.
        payload("Issue.update"),
        {**payload(), "type": "Project", "action": "create"},
        {
            "type": "OAuthApp",
            "action": "revoked",
            "organizationId": ORG,
            "webhookTimestamp": int(time.time() * 1000),
        },
        # Other apps and integrations.
        by_bot,
        # The app's own comments and changes; Linear reports its comments as
        # its user's, not as a bot's.
        own_comment,
        {**payload(), "actor": {"id": APP_USER, "type": "user"}},
    ):
        assert asyncio.run(send(channel, record)).status_code == 200
    assert handled == []
    ingress.start.assert_not_called()

    # Bots can be let through.
    allow_bots = channels.linear(on_event={"Issue.create": handler}, ignore_bots=False)
    asyncio.run(send(allow_bots, by_bot))
    assert len(handled) == 1


def test_asks_the_deliverys_actor_who_changed_an_issue(ingress: SimpleNamespace) -> None:
    handled: list[object] = []
    channel = channels.linear(
        on_event={"Issue.update": lambda event, _r: handled.append(event) or "prompt"}
    )
    # An issue's botActor is the bot that created it: a person changing an
    # issue an integration filed is still a person's change.
    by_person = payload("Issue.update")
    by_person["data"]["botActor"] = {"id": "slack", "type": "x"}
    asyncio.run(send(channel, by_person))
    assert len(handled) == 1

    # Another app changing a person's issue starts nothing.
    by_app = {**payload("Issue.update"), "actor": {"id": "app-2", "type": "OauthClient"}}
    asyncio.run(send(channel, by_app, delivery="d-2"))
    assert len(handled) == 1


def test_limits_runs_to_the_listed_teams(
    ingress: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    handled: list[object] = []
    channel = channels.linear(
        app={"teams": ["myt"]},
        on_event={"Issue.create": lambda event, _r: handled.append(event) or "prompt"},
    )
    other = payload()
    other["data"]["team"] = {"id": "t-2", "key": "ENG"}
    teamless = payload()
    del teamless["data"]["team"]
    for record in (payload(), other, teamless):
        asyncio.run(send(channel, record))
    assert len(handled) == 1
    # Each drop says why.
    assert "its team is not in app.teams" in caplog.text
    assert "it names no team" in caplog.text


def test_drops_a_comment_without_its_issues_team_when_limited_and_says_so(
    ingress: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    handled: list[object] = []
    channel = channels.linear(
        app={"teams": ["myt"]},
        on_event={"Comment.create": lambda event, _r: handled.append(event) or "prompt"},
    )
    # Linear's comment deliveries carry `issue.team`; one that doesn't can't be
    # placed in a team.
    comment = payload("Comment.create")
    del comment["data"]["issue"]["team"]
    assert asyncio.run(send(channel, comment)).status_code == 200
    assert handled == []
    assert "it names no team" in caplog.text


def test_validates_options_and_tells_deploy_what_it_handles() -> None:
    with pytest.raises(ValueError, match="unknown Linear app option team"):
        channels.linear(app={"team": ["MYT"]})  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="must list team keys"):
        channels.linear(app={"teams": []})
    with pytest.raises(ValueError, match="unsupported Linear event key"):
        channels.linear(on_event={"Project.create": lambda _e, _r: "x"})  # ty: ignore[invalid-argument-type]
    for name in ("x", "My Linear bot", "see https://acme.dev"):
        with pytest.raises(ValueError, match=r"app\.name"):
            channels.linear(app={"name": name})
    with pytest.raises(ValueError, match=r"app\.description"):
        channels.linear(app={"description": "x" * 1001})
    channel = channels.linear(
        app={
            "name": " Triage Bot ",
            "description": "Triages new issues.",
            "teams": ["MYT"],
        },
        on_event={"Issue.update": lambda _e, _r: "x", "Comment.create": lambda _e, _r: "y"},
    )
    collect_channels([("channels/mylinear.py", SimpleNamespace(channel=channel))])
    manifest = requirements_from_channels([channel])["mylinear"]
    assert manifest["provider"] == "linear"
    assert manifest["autoReply"] is True
    # Deploy provides the app's token at this name.
    assert manifest["agentConnections"] == ["mda/linear-token"]
    assert channels.linear(reply="none").requirements("mylinear")["autoReply"] is False
    assert manifest["providerConfig"] == {
        "events": [
            "AgentSessionEvent.created",
            "AgentSessionEvent.prompted",
            "Comment.create",
            "Issue.update",
        ],
        "sessions": True,
        "webhookResourceTypes": [
            "AgentSessionEvent",
            "Comment",
            "Issue",
            "OAuthAuthorization",
            "PermissionChange",
        ],
        "scopes": ["read", "write", "app:mentionable", "app:assignable"],
        "allowedScopes": [
            "read",
            "write",
            "app:mentionable",
            "app:assignable",
            "customer:read",
            "customer:write",
            "initiative:read",
            "initiative:write",
        ],
        # Deploy filters nothing by team, so teams stay out of the manifest.
        "app": {"name": "Triage Bot", "description": "Triages new issues."},
    }
    quiet = channels.linear(sessions=False, on_event={"Issue.create": lambda _e, _r: "x"})
    collect_channels([("channels/quiet.py", SimpleNamespace(channel=quiet))])
    assert requirements_from_channels([quiet])["quiet"]["providerConfig"]["events"] == [
        "Issue.create"
    ]


def test_posts_the_runs_reply_as_a_comment_in_the_triggering_thread(
    ingress: SimpleNamespace,
) -> None:
    # The run posts its answer through the channel (_channels/delivery.py), to the
    # target the delivery parsed.
    def reply(channel: HttpChannel) -> None:
        messaging_registry.register_channel_messaging([channel])
        transport = messaging_registry.messaging_transport_for_name("mylinear")
        assert transport is not None
        target = ingress.start.call_args.kwargs["binding"]["target"]
        asyncio.run(
            transport.post(
                message={"type": "content", "content": "Done, see the PR."}, target=target
            )
        )

    comments = channels.linear(on_event={"Comment.create": lambda _e, _r: "prompt"})
    asyncio.run(send(comments, payload("Comment.create")))
    reply(comments)
    ingress.comment.assert_awaited_once_with(
        issue_id=ISSUE, body="Done, see the PR.", parent_id="comment-1"
    )

    # An issue event has no thread; the reply goes on the issue.
    ingress.comment.reset_mock()
    issues = channels.linear(on_event={"Issue.create": lambda _e, _r: "prompt"})
    asyncio.run(send(issues, payload()))
    reply(issues)
    ingress.comment.assert_awaited_once_with(
        issue_id=ISSUE, body="Done, see the PR.", parent_id=None
    )


def test_a_failing_handler_starts_no_run(
    ingress: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(event: Any, _thread: object) -> str:
        event.payload["data"]["title"] = "changed"
        raise RuntimeError("handler broke: private payload")

    channel = channels.linear(on_event={"Issue.create": handler})
    assert asyncio.run(send(channel)).status_code == 200
    assert ingress.prepared == [None]
    assert "private payload" not in caplog.text


# GraphQL client


@pytest.fixture
def graphql(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    tokens = iter(["token-1", "token-2", "token-2", "token-2"])
    read = AsyncMock(side_effect=lambda _slug: next(tokens))
    monkeypatch.setattr(connections, "resolve_deployment_secret", read)
    requests: list[httpx.Request] = []
    responses: list[httpx.Response] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return responses.pop(0)

    transport = httpx.MockTransport(handle)
    real_client = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(linear_graphql.httpx, "AsyncClient", client)
    linear_graphql.reset_linear_state()
    yield SimpleNamespace(read=read, requests=requests, responses=responses)
    linear_graphql.reset_linear_state()


def test_graphql_calls_linear_as_the_app_and_reuses_its_token(graphql: SimpleNamespace) -> None:
    graphql.responses.extend(
        [httpx.Response(200, json={"data": {"ok": 1}}), httpx.Response(200, json={"data": {}})]
    )

    async def run() -> object:
        first = await linear_graphql.linear_request("query Q { ok }", {"id": "x"}, _OK)
        await linear_graphql.linear_request("query Q { ok }", {}, _OK)
        return first

    assert asyncio.run(run()) == {"ok": 1}
    graphql.read.assert_awaited_once_with("mda/linear-token")
    request = graphql.requests[0]
    assert str(request.url) == "https://api.linear.app/graphql"
    assert request.headers["authorization"] == "Bearer token-1"
    assert json.loads(request.content) == {"query": "query Q { ok }", "variables": {"id": "x"}}


def test_graphql_reads_the_token_again_once_when_linear_rejects_it(
    graphql: SimpleNamespace,
) -> None:
    graphql.responses.extend(
        [
            httpx.Response(
                200, json={"errors": [{"extensions": {"code": "AUTHENTICATION_ERROR"}}]}
            ),
            httpx.Response(200, json={"data": {"ok": 1}}),
        ]
    )
    assert asyncio.run(linear_graphql.linear_request("query Q { ok }", {}, _OK)) == {"ok": 1}
    assert graphql.requests[1].headers["authorization"] == "Bearer token-2"

    graphql.responses.extend([httpx.Response(401, json={}), httpx.Response(401, json={})])
    with pytest.raises(RuntimeError, match=r"HTTP 401"):
        asyncio.run(linear_graphql.linear_request("query Q { ok }", {}, _OK))


def test_graphql_reports_rate_limits_and_failures_without_details(
    graphql: SimpleNamespace,
) -> None:
    graphql.responses.append(
        httpx.Response(400, json={"errors": [{"extensions": {"code": "RATELIMITED"}}]})
    )
    with pytest.raises(LinearRateLimitError):
        asyncio.run(linear_graphql.linear_request("query Q { ok }", {}, _OK))
    graphql.responses.append(
        httpx.Response(200, json={"errors": [{"message": "Entity not found: secret detail"}]})
    )
    with pytest.raises(RuntimeError, match="Linear refused the request") as raised:
        asyncio.run(linear_graphql.linear_request("query Q { ok }", {}, _OK))
    assert "secret detail" not in str(raised.value)


def test_graphql_checks_what_linear_answers_against_the_operations_schema(
    graphql: SimpleNamespace,
) -> None:
    graphql.responses.append(httpx.Response(200, json={"data": {"ok": "secret detail"}}))
    with pytest.raises(RuntimeError, match="unexpected shape") as raised:
        asyncio.run(linear_graphql.linear_request("query Q { ok }", {}, _OK))
    assert "secret detail" not in str(raised.value)


def test_graphql_reuses_the_apps_user_for_an_hour(
    graphql: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    viewer = {"data": {"viewer": {"id": "app-user", "displayName": "mdatestagent"}}}
    graphql.responses.extend(
        [
            httpx.Response(200, json=viewer),
            httpx.Response(200, json={"data": {"viewer": None}}),
            httpx.Response(200, json=viewer),
        ]
    )
    user = {"id": "app-user", "display_name": "mdatestagent"}

    async def twice() -> list[dict[str, str]]:
        return [await linear_graphql.linear_app_user(), await linear_graphql.linear_app_user()]

    assert asyncio.run(twice()) == [user, user]
    assert len(graphql.requests) == 1
    # Read again once it is older than the TTL, an hour.
    assert linear_graphql._APP_USER_TTL_SECONDS == 3600
    linear_graphql.reset_linear_state()
    monkeypatch.setattr(linear_graphql, "_APP_USER_TTL_SECONDS", 0)
    graphql.responses.insert(0, httpx.Response(200, json=viewer))
    graphql.responses.insert(0, httpx.Response(200, json=viewer))
    assert asyncio.run(twice()) == [user, user]
    assert len(graphql.requests) == 3

    # A failed read is tried again on the next call.
    linear_graphql.reset_linear_state()

    async def failing_then_ok() -> dict[str, str]:
        with pytest.raises(RuntimeError, match="unexpected shape"):
            await linear_graphql.linear_app_user()
        return await linear_graphql.linear_app_user()

    assert asyncio.run(failing_then_ok()) == user


def test_graphql_comments_on_an_issue_threaded_when_asked(graphql: SimpleNamespace) -> None:
    graphql.responses.extend(
        [
            httpx.Response(
                200,
                json={
                    "data": {
                        "commentCreate": {
                            "success": True,
                            "comment": {"id": "c-1", "url": "https://linear.app/c-1"},
                        }
                    }
                },
            ),
            httpx.Response(200, json={"data": {"commentCreate": {"success": False}}}),
        ]
    )
    posted = asyncio.run(
        linear_graphql.create_linear_comment(issue_id="issue-1", body="Done", parent_id="c-0")
    )
    assert posted == {"id": "c-1", "url": "https://linear.app/c-1"}
    assert json.loads(graphql.requests[0].content)["variables"] == {
        "input": {"issueId": "issue-1", "body": "Done", "parentId": "c-0"}
    }
    with pytest.raises(RuntimeError, match="unexpected shape"):
        asyncio.run(linear_graphql.create_linear_comment(issue_id="issue-1", body="Done"))


# Agent sessions


def reply(body: str) -> dict[str, Any]:
    record = payload("AgentSessionEvent.prompted")
    record["agentActivity"]["content"]["body"] = body
    return record


def test_answers_a_new_session_with_linears_prompt_and_replies_there(
    ingress: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGSMITH_TENANT_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setenv("LANGSMITH_HOST_PROJECT_ID", DEPLOYMENT)
    monkeypatch.setenv("MDA_LANGSMITH_URL", "https://smith.langchain.com")
    channel = channels.linear()
    assert asyncio.run(send(channel, payload("AgentSessionEvent.created"))).status_code == 200
    assert ingress.prepared[0]["messages"][0]["content"] == (
        '<issue identifier="MYT-5"><title>untrusted title</title></issue>'
    )
    asyncio.run(drain(ingress))
    # Acknowledged at once, so Linear doesn't mark the session unresponsive.
    assert ingress.activity.await_args_list[0].kwargs == {
        "agent_session_id": SESSION,
        "content": {"type": "thought", "body": "Working on it."},
        "ephemeral": True,
    }
    session_id, kwargs = (
        ingress.session_url.await_args.args[0],
        ingress.session_url.await_args.kwargs,
    )
    assert session_id == SESSION
    assert kwargs["label"] == "LangSmith Studio"
    assert kwargs["url"].startswith("https://smith.langchain.com/o/")
    # The run posts its answer through the channel (_channels/delivery.py).
    messaging_registry.register_channel_messaging([channel])
    transport = messaging_registry.messaging_transport_for_name("mylinear")
    assert transport is not None
    target = ingress.start.call_args.kwargs["binding"]["target"]
    asyncio.run(
        transport.post(message={"type": "content", "content": "Here's the plan."}, target=target)
    )
    assert ingress.activity.await_args.kwargs == {
        "agent_session_id": SESSION,
        "content": {"type": "response", "body": "Here's the plan."},
    }
    ingress.comment.assert_not_called()


def test_continues_with_a_session_reply_and_answers_a_declined_session(
    ingress: SimpleNamespace,
) -> None:
    asyncio.run(send(channels.linear(), payload("AgentSessionEvent.prompted")))
    assert ingress.prepared[0]["messages"][0]["content"] == "go ahead"

    # A handler that declines still answers the session.
    declining = channels.linear(on_event={"AgentSessionEvent.created": lambda _e, _r: None})
    asyncio.run(send(declining, payload("AgentSessionEvent.created"), delivery="d-2"))
    assert ingress.prepared[1] is None
    assert ingress.activity.await_args.kwargs["content"] == {
        "type": "response",
        "body": "I'm not set up to work on this request.",
    }

    # A handler that raises failed, which the session hears as an error.
    def failing(_event: object, _thread: object) -> str:
        raise RuntimeError("boom")

    raising = channels.linear(on_event={"AgentSessionEvent.created": failing})
    asyncio.run(send(raising, payload("AgentSessionEvent.created"), delivery="d-4"))
    assert ingress.activity.await_args.kwargs["content"] == {
        "type": "error",
        "body": "Something went wrong while working on this.",
    }

    # So does a declined follow-up, such as a reply with nothing to read.
    ingress.activity.reset_mock()
    declining_replies = channels.linear(
        on_event={"AgentSessionEvent.prompted": lambda _e, _r: None}
    )
    asyncio.run(send(declining_replies, payload("AgentSessionEvent.prompted"), delivery="d-3"))
    assert ingress.activity.await_args.kwargs["content"] == {
        "type": "response",
        "body": "I'm not set up to work on this request.",
    }


def test_tells_a_session_why_an_endpoint_declined(
    ingress: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from managed_deepagents._channels.http import endpoint as endpoint_module  # noqa: PLC0415

    caplog.set_level(logging.INFO)
    real_client = httpx.AsyncClient
    why = "Which repository should I work in?"
    monkeypatch.setattr(
        endpoint_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"decline": why})),
            **kwargs,
        ),
    )
    channel = channels.linear(
        on_event={"AgentSessionEvent.created": "https://backend.example/runs"}
    )
    asyncio.run(send(channel, payload("AgentSessionEvent.created")))
    asyncio.run(drain(ingress))
    assert ingress.prepared == [None]
    assert ingress.activity.await_args.kwargs["content"] == {"type": "response", "body": why}
    assert "Linear endpoint declined" in caplog.text
    assert why not in caplog.text


def test_rejects_a_malformed_service_secret_when_declared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # It fails here rather than on every event.
    monkeypatch.setenv("MDA_SERVICE_SECRET", "whsec_tooshort")
    with pytest.raises(TypeError, match="MDA_SERVICE_SECRET"):
        channels.linear(on_event={"Issue.update": "https://backend.example/runs"})


def test_asks_an_endpoint_what_a_session_starts(
    ingress: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from managed_deepagents._channels.http import endpoint as endpoint_module  # noqa: PLC0415

    seen: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def backend(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"input": "Plan it", "metadata": {"ticket": "MYT-5"}})

    monkeypatch.setattr(
        endpoint_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(backend), **kwargs),
    )
    channel = channels.linear(
        on_event={
            "AgentSessionEvent.created": httpx.Request(
                "POST", "https://backend.example/runs", headers={"x-api-key": "secret"}
            )
        }
    )
    # Linear gets its answer before the endpoint is asked.
    assert asyncio.run(send(channel, payload("AgentSessionEvent.created"))).status_code == 200
    assert not seen
    asyncio.run(drain(ingress))
    [prepared] = ingress.prepared
    assert prepared["metadata"] == {"ticket": "MYT-5"}
    [request] = seen
    assert request.headers["x-api-key"] == "secret"
    body = json.loads(request.content)
    assert body["event"]["key"] == "AgentSessionEvent.created"
    assert body["thread"]["id"] == channel_thread_id("mylinear", f"{ORG}/{ISSUE}/session/{SESSION}")


def test_reads_a_signal_linear_adds_later_as_unknown_not_as_stop(
    ingress: SimpleNamespace,
) -> None:
    seen: list[Any] = []
    channel = channels.linear(
        on_event={"AgentSessionEvent.prompted": lambda e, _t: seen.append(e) or "prompt"}
    )
    later = reply("go on")
    later["agentActivity"]["signal"] = "continue"
    later["agentActivity"]["content"]["type"] = "something-new"
    asyncio.run(send(channel, later))
    ingress.cancel.assert_not_called()
    activity = seen[0].payload["agentActivity"]
    assert (activity["signal"], activity["content"]["type"]) == ("unknown", "unknown")


def test_cancels_only_its_sessions_run_on_stop(ingress: SimpleNamespace) -> None:
    stop = reply("stop")
    stop["agentActivity"]["signal"] = "stop"
    assert asyncio.run(send(channels.linear(), stop)).status_code == 200
    ingress.start.assert_not_called()
    # The session's thread, not the issue's or another session's.
    assert ingress.cancel.await_args.kwargs["thread_id"] == channel_thread_id(
        "mylinear", f"{ORG}/{ISSUE}/session/{SESSION}"
    )
    asyncio.run(drain(ingress))
    assert ingress.activity.await_args.kwargs["content"] == {"type": "response", "body": "Stopped."}


def test_tells_on_finish_about_a_stop_after_the_sessions_final_word(
    ingress: SimpleNamespace,
) -> None:
    calls: list[str] = []
    ingress.activity.side_effect = lambda **kwargs: (
        calls.append(kwargs["content"]["type"]) or {"id": "activity"}
    )
    on_finish = Mock(side_effect=lambda *_: calls.append("finish"))
    channel = channels.linear(on_finish=on_finish)
    stop = reply("stop")
    stop["agentActivity"]["signal"] = "stop"
    asyncio.run(send(channel, stop))
    asyncio.run(drain(ingress))
    assert calls == ["response", "finish"]
    event, thread, finished = on_finish.call_args.args
    assert event.key == "AgentSessionEvent.prompted"
    assert thread == LinearChannelThread(
        id=channel_thread_id("mylinear", f"{ORG}/{ISSUE}/session/{SESSION}"),
        channel=ChannelName(name="mylinear"),
    )
    # A Stop starts no run of its own.
    assert finished == {"type": "skipped", "reason": "cancel", "interrupts": []}

    # The session's run, which the Stop cancelled, finishes on its own, and
    # says nothing more in the session than the acknowledgement.
    ingress.observe.return_value = {"status": "interrupted", "interrupts": []}
    asyncio.run(send(channel, payload("AgentSessionEvent.created"), delivery="d-2"))
    asyncio.run(drain(ingress))
    assert on_finish.call_args.args[2] == {
        "type": "run",
        "run": {"id": "run-1", "status": "interrupted"},
        "interrupts": [],
    }
    assert calls[2:] == ["thought", "finish"]


def test_tells_a_session_its_run_could_not_be_started_once_linear_wont_retry_it(
    ingress: SimpleNamespace,
) -> None:
    def errors() -> list[object]:
        return [
            call
            for call in ingress.activity.await_args_list
            if call.kwargs["content"]["type"] == "error"
        ]

    ingress.start.side_effect = RuntimeError("server down")
    # Answered with an error, Linear retries the delivery, which may start.
    channel = channels.linear()
    assert asyncio.run(send(channel, payload("AgentSessionEvent.created"))).status_code == 500
    asyncio.run(drain(ingress))
    assert errors() == []

    # An endpoint's event already has its answer: nothing retries it, so the
    # session doesn't keep working on a run that never started.
    endpoint = channels.linear(
        on_event={"AgentSessionEvent.created": "https://backend.example/runs"}
    )
    response = asyncio.run(send(endpoint, payload("AgentSessionEvent.created"), delivery="d-2"))
    assert response.status_code == 200
    asyncio.run(drain(ingress))
    assert len(errors()) == 1


def test_points_a_paused_session_to_studio(ingress: SimpleNamespace) -> None:
    channel = channels.linear()
    # The session's run stopped on interrupts. The run asks for the decision
    # itself as it ends, so the observer says nothing more.
    ingress.observe.return_value = {
        "status": "interrupted",
        "assistant_id": "agent",
        "interrupts": [{"id": "i-1", "value": {}}],
    }
    asyncio.run(send(channel, payload("AgentSessionEvent.created")))
    asyncio.run(drain(ingress))
    assert all(
        call.kwargs["content"]["type"] != "elicitation" for call in ingress.activity.await_args_list
    )
    # The run's notice asks for input, so the session waits rather than ends.
    assert channel.paused_notice is not None
    target = {"organizationId": "org", "issueId": "issue", "sessionId": SESSION}
    asyncio.run(channel.paused_notice(target, {"thread_id": "t-1", "assistant_id": "agent"}))
    said = ingress.activity.await_args.kwargs
    assert said["agent_session_id"] == SESSION
    assert said["content"]["type"] == "elicitation"
    assert said["content"]["body"].startswith(
        "I need a decision before I continue. Review the pending step in LangSmith Studio"
    )

    # A reply while it waits starts nothing and gets the same pointer.
    ingress.start.side_effect = [{"skipped": "paused", "interrupts": [{"id": "i-1", "value": {}}]}]
    asyncio.run(send(channel, reply("any news?"), delivery="d-2"))
    asyncio.run(drain(ingress))
    said = ingress.activity.await_args.kwargs
    assert said["content"]["type"] == "elicitation"
    assert "I'm waiting for a decision" in said["content"]["body"]


def test_reports_a_failed_run_in_the_session(ingress: SimpleNamespace) -> None:
    ingress.observe.return_value = {"status": "error", "interrupts": []}
    asyncio.run(send(channels.linear(), payload("AgentSessionEvent.created")))
    asyncio.run(drain(ingress))
    assert ingress.activity.await_args.kwargs == {
        "agent_session_id": SESSION,
        "content": {"type": "error", "body": "Something went wrong while working on this."},
    }


def test_leaves_comments_a_session_already_delivers_to_the_session(
    ingress: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    handled: list[object] = []
    channel = channels.linear(
        on_event={"Comment.create": lambda event, _r: handled.append(event) or "prompt"}
    )
    mention = payload("Comment.create")
    mention["data"]["body"] = "@mdatestagent hello"
    asyncio.run(send(channel, mention))
    in_thread = payload("Comment.create")
    in_thread["data"]["parentId"] = "comment-root"
    ingress.in_session.return_value = True
    asyncio.run(send(channel, in_thread))
    ingress.in_session.assert_awaited_with("comment-root")
    # An email address isn't a mention.
    email = payload("Comment.create")
    email["data"]["body"] = "ask ops@mdatestagent.dev"
    asyncio.run(send(channel, email))
    assert len(handled) == 1
    # Each comment left to a session says why.
    assert "it mentions the app, which opens a session" in caplog.text
    assert "it replies in a session's thread" in caplog.text
    # A reply that mentions the app needs no lookup.
    assert ingress.in_session.await_count == 1

    # Without sessions, every comment is the handler's.
    plain = channels.linear(
        sessions=False,
        on_event={"Comment.create": lambda event, _r: handled.append(event) or "prompt"},
    )
    asyncio.run(send(plain, mention))
    assert len(handled) == 2


def test_graphql_posts_session_activities_and_links_sessions(graphql: SimpleNamespace) -> None:
    graphql.responses.extend(
        [
            httpx.Response(
                200,
                json={
                    "data": {
                        "agentActivityCreate": {"success": True, "agentActivity": {"id": "a-1"}}
                    }
                },
            ),
            httpx.Response(200, json={"data": {"agentSessionUpdate": {"success": True}}}),
            httpx.Response(200, json={"data": {"comment": {"agentSession": {"id": "s-1"}}}}),
            httpx.Response(200, json={"data": {"comment": {"agentSession": None}}}),
        ]
    )

    async def run() -> list[object]:
        return [
            await linear_graphql.create_linear_activity(
                agent_session_id="s-1",
                content={"type": "elicitation", "body": "Which one?"},
                ephemeral=False,
            ),
            await linear_graphql.add_linear_session_url("s-1", label="Studio", url="https://x"),
            await linear_graphql.comment_in_agent_session("c-1"),
            await linear_graphql.comment_in_agent_session("c-2"),
        ]

    assert asyncio.run(run()) == [{"id": "a-1"}, None, True, False]
    assert json.loads(graphql.requests[0].content)["variables"] == {
        "input": {
            "agentSessionId": "s-1",
            "content": {"type": "elicitation", "body": "Which one?"},
            "ephemeral": False,
        }
    }
    assert json.loads(graphql.requests[1].content)["variables"] == {
        "id": "s-1",
        "input": {"addedExternalUrls": [{"label": "Studio", "url": "https://x"}]},
    }
