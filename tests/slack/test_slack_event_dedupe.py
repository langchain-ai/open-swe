import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks
from starlette.requests import Request

from openswe.slack import events as slack_events
from openswe.slack import failures as slack_failures
from openswe.slack import routes as slack_routes
from openswe.slack import unfurls
from openswe.slack.channels import SlackChannel
from openswe.slack.payloads import SlackChannelContext, SlackEventEnvelope
from openswe.users import User, UserIdentity
from openswe.webhooks import common as webhook_common


@pytest.mark.parametrize(
    "destination",
    [
        "channel",
        "mpim",
        "owner_dm",
        "other_dm",
        "mismatched_dm",
        "external",
        "unknown",
        "wrong_team",
    ],
)
async def test_dashboard_unfurls_authorize_destination_and_private_thread(
    monkeypatch: pytest.MonkeyPatch, destination: str
) -> None:
    thread_id = "e97598dc-1c56-4c4e-b2ff-c402b6b99b23"
    url = f"https://openswe.example.com/agents/{thread_id}"
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://openswe.example.com")
    monkeypatch.delenv("SLACK_APP_ID", raising=False)
    channel = SlackChannel(
        id="D1",
        payload={
            "is_im": destination.endswith("dm"),
            "is_mpim": destination == "mpim",
            "is_ext_shared": destination == "external",
            "is_pending_ext_shared": False,
            "user": "U1" if destination != "mismatched_dm" else "U2",
        },
    )
    monkeypatch.setattr(
        unfurls.SlackChannel,
        "load",
        AsyncMock(return_value=None if destination == "unknown" else channel),
    )
    user = User(
        identities=[
            UserIdentity(provider="slack", external_id="U1", team_id="T1"),
            UserIdentity(provider="github", external_id="1", login="alice"),
        ]
    )
    identity_lookup = AsyncMock(return_value=user)
    monkeypatch.setattr(unfurls.User, "for_identity", identity_lookup)
    monkeypatch.setattr(unfurls, "is_authorized_github_login", AsyncMock(return_value=True))
    monkeypatch.setattr(unfurls, "note_for_concierge", AsyncMock())
    read = AsyncMock(
        return_value={
            "status": "busy",
            "metadata": {
                "source": "dashboard",
                "visibility": "private",
                "owner_login": "bob" if destination == "other_dm" else "alice",
                "title": "Secret <@U2> rollout",
            },
        }
    )
    monkeypatch.setattr(
        unfurls, "langgraph_client", lambda: SimpleNamespace(threads=SimpleNamespace(get=read))
    )
    send = AsyncMock()
    client = SimpleNamespace(chat_unfurl=send)

    @asynccontextmanager
    async def bot():
        yield client

    monkeypatch.setattr(unfurls.SlackClient, "bot", bot)
    monkeypatch.setattr(unfurls, "slack_identity", AsyncMock(return_value={"team_id": "T1"}))
    envelope = SlackEventEnvelope.model_validate(
        {
            "type": "event_callback",
            "event_id": "EvUnfurl",
            "team_id": "T2" if destination == "wrong_team" else "T1",
            "event": {
                "type": "link_shared",
                "channel": "D1",
                "user": "U1",
                "message_ts": "123.45",
                "links": [
                    {"url": url},
                    {"url": "https://github.com/langchain-ai/open-swe/pull/123"},
                    {"url": "https://openswe.example.com.evil.test/agents/" + thread_id},
                    {"url": "https://openswe.example.com/agents/instructions"},
                ],
            },
        }
    )
    tasks = _FakeBackgroundTasks()
    assert (await _post(envelope.model_dump(mode="json"), tasks))["status"] == "accepted"
    assert len(tasks.tasks) == 1
    await unfurls.unfurl_dashboard_links(envelope)
    await unfurls.unfurl_dashboard_links(envelope)
    if destination in {"external", "unknown", "wrong_team"}:
        send.assert_not_awaited()
        read.assert_not_awaited()
        return
    send.assert_awaited_once()
    cards = send.await_args.kwargs["unfurls"]
    assert list(cards) == [url]
    assert cards[url]["blocks"][-1]["elements"][0]["url"] == url
    if destination == "owner_dm":
        assert "Secret" in cards[url]["fallback"]
        assert "Running" in cards[url]["fallback"]
        assert cards[url]["blocks"][0]["text"]["type"] == "plain_text"
    else:
        assert "Secret" not in str(cards)
    if destination in {"channel", "mpim", "mismatched_dm"}:
        identity_lookup.assert_not_awaited()
        read.assert_not_awaited()


@pytest.mark.parametrize(
    "route",
    [
        "agents/reviews/langchain-ai/open-swe/123",
        "langchain-ai/open-swe/pull/123",
        "agents/e97598dc-1c56-4c4e-b2ff-c402b6b99b23",
    ],
)
async def test_dashboard_pr_unfurl_denied_access_never_fetches_details(
    monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    from fastapi import HTTPException

    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://openswe.example.com")
    link = unfurls.dashboard_link(f"https://openswe.example.com/{route}")
    assert link is not None
    monkeypatch.setattr(
        unfurls, "require_repo_access_for_user", AsyncMock(side_effect=HTTPException(404))
    )
    read = AsyncMock(
        return_value={
            "metadata": {
                "source": "review_chat",
                "github_login": "alice",
                "repo_owner": "langchain-ai",
                "repo_name": "open-swe",
                "pr_number": 123,
                "title": "Restricted review",
            }
        }
    )
    monkeypatch.setattr(
        unfurls, "langgraph_client", lambda: SimpleNamespace(threads=SimpleNamespace(get=read))
    )
    fetch = AsyncMock()
    monkeypatch.setattr(unfurls, "github_request", fetch)
    with pytest.raises(HTTPException):
        await unfurls._details(
            link, User(identities=[UserIdentity(provider="github", external_id="1", login="alice")])
        )
    fetch.assert_not_awaited()


@pytest.mark.parametrize("rollup", [None, {"state": "PENDING"}])
async def test_dashboard_pr_preview_uses_live_status_without_inventing_checks(
    monkeypatch: pytest.MonkeyPatch, rollup: dict[str, str] | None
) -> None:
    import httpx2

    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://openswe.example.com")
    link = unfurls.dashboard_link(
        "https://openswe.example.com/agents/reviews/langchain-ai/open-swe/123"
    )
    assert link is not None
    monkeypatch.setattr(unfurls, "require_repo_access_for_user", AsyncMock(return_value="token"))
    response = httpx2.Response(
        200,
        request=httpx2.Request("POST", "https://api.github.com/graphql"),
        json={
            "data": {
                "repository": {
                    "pullRequest": {
                        "title": "Fix <@U2> notifications",
                        "state": "OPEN",
                        "isDraft": True,
                        "author": {"login": "alice"},
                        "reviewDecision": "REVIEW_REQUIRED",
                        "commits": {"nodes": [{"commit": {"statusCheckRollup": rollup}}]},
                    }
                }
            },
        },
    )
    monkeypatch.setattr(unfurls, "github_request", AsyncMock(return_value=response))
    card = await unfurls._details(
        link, User(identities=[UserIdentity(provider="github", external_id="1", login="alice")])
    )
    assert card["blocks"][0]["text"]["type"] == "plain_text"
    assert "Draft · Author: alice · Review: review required" in card["fallback"]
    assert ("Check rollup: pending" in card["fallback"]) is (rollup is not None)
    assert "success" not in card["fallback"].lower()


class _ConflictError(Exception):
    pass


class _FakeThreads:
    def __init__(self) -> None:
        self.ids: set[str] = set()
        self.lock = asyncio.Lock()

    async def create(self, *, thread_id: str, **_kwargs: Any) -> None:
        async with self.lock:
            if thread_id in self.ids:
                raise _ConflictError
            self.ids.add(thread_id)

    async def get(self, thread_id: str) -> dict[str, str]:
        if thread_id not in self.ids:
            raise KeyError(thread_id)
        return {"thread_id": thread_id}


class _FakeClient:
    def __init__(self) -> None:
        self.threads = _FakeThreads()


class _FakeBackgroundTasks:
    def __init__(self) -> None:
        self.tasks: list[tuple[Any, tuple[Any, ...]]] = []

    def add_task(self, func: Any, *args: Any) -> None:
        self.tasks.append((func, args))


class _FakeRequest:
    def __init__(self, payload: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        self.headers: dict[str, str] = headers or {}
        self._body = json.dumps(payload).encode()

    async def body(self) -> bytes:
        return self._body


def _mention_payload(event_id: str = "Ev1") -> dict[str, Any]:
    return {
        "type": "event_callback",
        "event_id": event_id,
        "authorizations": [{"user_id": "BOT"}],
        "event": {
            "type": "app_mention",
            "channel": "C1",
            "ts": "1786573369.551099",
            "user": "U1",
            "text": "<@BOT> hello?",
        },
    }


def _channel_message_payload(event_id: str = "Ev2") -> dict[str, Any]:
    payload = _mention_payload(event_id)
    payload["event"] = {**payload["event"], "type": "message", "channel_type": "channel"}
    return payload


async def _post(
    payload: dict[str, Any],
    background_tasks: _FakeBackgroundTasks,
    headers: dict[str, str] | None = None,
) -> dict[str, str]:
    return await slack_routes.slack_webhook(
        cast(Request, _FakeRequest(payload, headers)),
        cast(BackgroundTasks, background_tasks),
    )


@pytest.fixture(autouse=True)
def _patch_slack_webhook(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    slack_events.reset_slack_event_claims()
    client = _FakeClient()
    monkeypatch.setattr(
        "openswe.incidents.channels.handle_slack_event", AsyncMock(return_value=None)
    )

    async def channel_context(_channel_id: str, *, use_cache: bool = True) -> SlackChannelContext:
        return SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False)

    async def repo_config(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"owner": "langchain-ai", "name": "open-swe"}

    monkeypatch.setattr(slack_events, "get_client", lambda url: client)
    monkeypatch.setattr(webhook_common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(webhook_common, "resolve_slack_thread_id", AsyncMock(return_value="t1"))
    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", channel_context)
    monkeypatch.setattr(webhook_common, "get_slack_repo_config", repo_config)
    return client


async def test_mention_and_message_deliveries_start_one_run(
    monkeypatch: pytest.MonkeyPatch,
    _patch_slack_webhook: _FakeClient,
) -> None:
    background_tasks = _FakeBackgroundTasks()
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USER_ID", "BOT")

    first = await _post(_mention_payload("Ev1"), background_tasks)
    slack_events.reset_slack_event_claims()
    second = await _post(_channel_message_payload("Ev2"), background_tasks)

    assert first["status"] == "accepted"
    assert second["status"] == "ignored"
    assert len(background_tasks.tasks) == 1
    assert _patch_slack_webhook.threads.ids == {
        slack_events._claim_thread_id("Ev1"),
        slack_events._claim_thread_id("Ev2"),
        slack_events._claim_thread_id("C1:1786573369.551099"),
    }


def _bot_payload(*, event_type: str = "message", with_user: bool = True) -> dict[str, Any]:
    payload = _mention_payload()
    payload.update({"team_id": "T123", "api_app_id": "AOWN"})
    payload["event"].update(
        {
            "type": event_type,
            "subtype": "bot_message",
            "bot_id": "B123",
            "app_id": "A123",
            "user": "U123",
        }
    )
    if not with_user:
        payload["event"].pop("user")
    return payload


@pytest.mark.parametrize(
    "change",
    [
        {"bot_id": "BOTHER"},
        {"user": "UOTHER"},
        {"app_id": "AOTHER"},
        {"user": "BOT"},
        {"app_id": "AOWN"},
        {"text": "A status update"},
        {"subtype": "message_deleted"},
    ],
)
async def test_bot_must_be_allowed_and_explicitly_mention_us(
    allowed_bot: None,
    change: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(webhook_common, "is_code_channel", AsyncMock(return_value=True))
    payload = _bot_payload()
    payload["event"].update(change)
    tasks = _FakeBackgroundTasks()
    assert (await _post(payload, tasks))["status"] == "ignored"
    assert tasks.tasks == []


@pytest.mark.parametrize("team_id", ["", "TOTHER"])
async def test_bot_allowlist_is_workspace_scoped(allowed_bot: None, team_id: str) -> None:
    payload = _bot_payload()
    payload["team_id"] = team_id
    tasks = _FakeBackgroundTasks()
    assert (await _post(payload, tasks))["status"] == "ignored"
    assert tasks.tasks == []


async def test_bot_authorization_is_workspace_owned(
    allowed_bot: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "someone-else")
    tasks = _FakeBackgroundTasks()
    assert (await _post(_bot_payload(), tasks))["status"] == "accepted"
    assert len(tasks.tasks) == 1


async def test_retry_header_alone_does_not_drop_an_unseen_event() -> None:
    background_tasks = _FakeBackgroundTasks()

    response = await _post(_mention_payload("EvNew"), background_tasks, {"X-Slack-Retry-Num": "2"})

    assert response["status"] == "accepted"
    assert len(background_tasks.tasks) == 1


async def test_concurrent_cross_instance_redeliveries_start_one_run() -> None:
    background_tasks = _FakeBackgroundTasks()

    async def post() -> dict[str, str]:
        slack_events.reset_slack_event_claims()
        return await _post(_mention_payload(), background_tasks)

    responses = await asyncio.gather(*(post() for _ in range(3)))

    assert [response["status"] for response in responses].count("accepted") == 1
    assert len(background_tasks.tasks) == 1


async def test_external_channel_refuses_without_starting_a_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    background_tasks = _FakeBackgroundTasks()
    post_reply = AsyncMock(return_value=True)
    resolve_thread = cast(AsyncMock, webhook_common.resolve_slack_thread_id)
    monkeypatch.setattr(
        webhook_common,
        "resolve_slack_channel_context",
        AsyncMock(return_value=SlackChannelContext(is_ext_shared=True)),
    )
    monkeypatch.setattr(webhook_common, "post_slack_thread_reply", post_reply)

    response = await _post(_mention_payload(), background_tasks)

    assert response == {"status": "ignored", "reason": "Slack channel is not eligible"}
    assert len(background_tasks.tasks) == 1
    await background_tasks.tasks[0][0](*background_tasks.tasks[0][1])
    post_reply.assert_awaited_once_with(
        "C1",
        "1786573369.551099",
        slack_routes._EXTERNAL_CHANNEL_REFUSAL,
    )
    resolve_thread.assert_not_awaited()


async def test_unverified_channel_fails_closed_without_reply_or_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    background_tasks = _FakeBackgroundTasks()
    post_reply = AsyncMock(return_value=True)
    resolve_thread = cast(AsyncMock, webhook_common.resolve_slack_thread_id)
    monkeypatch.setattr(
        webhook_common,
        "resolve_slack_channel_context",
        AsyncMock(return_value=SlackChannelContext(is_ext_shared=None)),
    )
    monkeypatch.setattr(webhook_common, "post_slack_thread_reply", post_reply)

    response = await _post(_mention_payload(), background_tasks)

    assert response == {"status": "ignored", "reason": "Slack channel is not eligible"}
    assert background_tasks.tasks == []
    post_reply.assert_not_awaited()
    resolve_thread.assert_not_awaited()


async def test_preprocessing_failure_replies_and_does_not_claim_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    background_tasks = _FakeBackgroundTasks()
    post_reply = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_failures, "post_slack_thread_reply", post_reply)

    async def failed_repo_config(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        raise RuntimeError

    original_repo_config = webhook_common.get_slack_repo_config
    monkeypatch.setattr(webhook_common, "get_slack_repo_config", failed_repo_config)
    response = await _post(_mention_payload(), background_tasks)

    assert response["status"] == "error"
    assert background_tasks.tasks == []
    post_reply.assert_awaited_once()
    await_args = post_reply.await_args
    assert await_args is not None
    assert await_args.args[:2] == ("C1", "1786573369.551099")
    assert f"Error ID: `{response['error_id']}`" in await_args.args[2]

    monkeypatch.setattr(webhook_common, "get_slack_repo_config", original_repo_config)
    assert (await _post(_mention_payload(), background_tasks))["status"] == "accepted"
