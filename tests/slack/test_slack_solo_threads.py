"""Solo-thread routing persists across requests without subscribing other humans or bots."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks
from langgraph_sdk.client import LangGraphClient
from starlette.requests import Request

from agent.slack import routes, solo_threads
from agent.slack.payloads import SlackChannelContext
from agent.slack.request import SlackRequest
from agent.utils.json_types import JsonObject
from agent.webhooks import common


class _Store:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], JsonObject] = {}

    async def get_item(self, namespace: tuple[str, ...], key: str) -> JsonObject | None:
        value = self.items.get((namespace, key))
        return {"value": dict(value)} if value is not None else None

    async def put_item(self, namespace: tuple[str, ...], key: str, value: JsonObject) -> None:
        self.items[(namespace, key)] = dict(value)


class _Harness:
    def __init__(self) -> None:
        self.store = _Store()
        self.history: list[JsonObject] = []
        self.pages: list[JsonObject] | None = None
        self.now = 1000.0
        self.claims: set[str] = set()
        self.lock = asyncio.Lock()
        self.requests: list[SlackRequest] = []

    @asynccontextmanager
    async def bot(self) -> AsyncIterator[_Harness]:
        yield self

    @asynccontextmanager
    async def mutation_lock(self, *_args: object, **_kwargs: object) -> AsyncIterator[None]:
        async with self.lock:
            yield None

    async def conversations_replies(self, **kwargs: object) -> JsonObject:
        if self.pages is not None:
            return self.pages[int(str(kwargs.get("cursor") or "0"))]
        return {"messages": list(self.history)}

    async def claim(self, event_id: str, *_args: str) -> bool:
        if event_id in self.claims:
            return False
        self.claims.add(event_id)
        return True

    async def process(self, request: SlackRequest, _repo: object) -> None:
        self.requests.append(request)

    async def send(
        self,
        text: str,
        *,
        user: str = "U1",
        thread: str = "1000.0",
        channel: str = "C1",
        subtype: str = "",
        bot_id: str = "",
    ) -> JsonObject:
        self.now += 1
        message: JsonObject = {
            "type": "message",
            "channel": channel,
            "ts": str(self.now),
            "thread_ts": thread,
            "user": user,
            "text": text,
            "subtype": subtype,
            "bot_id": bot_id,
        }
        if thread == "1000.0" and channel == "C1":
            self.history.append(message)
        payload: JsonObject = {
            "type": "event_callback",
            "event_id": f"Ev-{self.now}",
            "authorizations": [{"user_id": "BOT"}],
            "event": message,
        }

        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": json.dumps(payload).encode()}

        request = Request({"type": "http", "headers": []}, receive=receive)
        tasks = BackgroundTasks()
        response = await routes.slack_webhook(request, tasks)
        await tasks()
        return cast(JsonObject, response)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    state = _Harness()
    state.history.append({"ts": "1000.0", "user": "U1", "text": "initial question"})
    monkeypatch.setattr(solo_threads.SlackClient, "bot", state.bot)
    monkeypatch.setattr(solo_threads, "slack_thread_mutation_lock", state.mutation_lock)
    monkeypatch.setattr(solo_threads, "time", lambda: state.now)
    monkeypatch.setattr(routes, "get_langgraph_client", lambda: cast(LangGraphClient, state))
    monkeypatch.setattr(routes.service, "process_slack_mention", state.process)
    monkeypatch.setattr(common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(common, "is_code_channel", AsyncMock(return_value=False))
    monkeypatch.setattr(common, "resolve_slack_thread_id", AsyncMock(return_value="t1"))
    monkeypatch.setattr(common, "get_slack_repo_config", AsyncMock(return_value=None))
    monkeypatch.setattr(common, "claim_slack_event", state.claim)
    monkeypatch.setattr(common, "SLACK_BOT_USERNAME", "openswe")
    monkeypatch.setattr(common, "SLACK_BOT_USER_ID", "BOT")
    monkeypatch.setattr(
        common,
        "resolve_slack_channel_context",
        AsyncMock(
            return_value=SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False)
        ),
    )
    monkeypatch.setattr("agent.incidents.channels.handle_slack_event", AsyncMock(return_value=None))
    return state


async def test_mention_arms_owner_followups_but_not_other_threads(harness: _Harness) -> None:
    assert (await harness.send("before a mention"))["status"] == "ignored"
    assert (await harness.send("<@BOT> fix this"))["status"] == "accepted"
    assert (await harness.send("still wrong", subtype="file_share"))["status"] == "accepted"
    assert harness.requests[-1].treat_all_messages_as_mentions is True
    assert (await harness.send("other thread", thread="900.0"))["status"] == "ignored"
    assert (await harness.send("unrelated chatter", user="U2", thread="900.0"))[
        "status"
    ] == "ignored"
    assert (await harness.send("try again"))["status"] == "accepted"


async def test_second_human_permanently_disarms_even_after_another_mention(
    harness: _Harness,
) -> None:
    await harness.send("@openswe help")
    assert (await harness.send("I have thoughts", user="U2"))["status"] == "ignored"
    harness.history = [message for message in harness.history if message.get("user") != "U2"]
    assert (await harness.send("<@BOT> continue"))["status"] == "accepted"
    assert (await harness.send("untagged"))["status"] == "ignored"
    assert (await harness.send("my followup", user="U2"))["status"] == "ignored"


async def test_preexisting_second_human_on_later_page_prevents_arming(harness: _Harness) -> None:
    harness.pages = [
        {"messages": list(harness.history), "response_metadata": {"next_cursor": "1"}},
        {"messages": [{"ts": "1000.5", "user": "U2", "text": "earlier reply"}]},
    ]
    assert (await harness.send("<@BOT> help"))["status"] == "accepted"
    assert (await harness.send("untagged"))["status"] == "ignored"


async def test_history_detects_second_human_even_if_their_webhook_was_missed(
    harness: _Harness,
) -> None:
    await harness.send("<@BOT> help")
    harness.history.append({"ts": "1001.5", "user": "U2", "text": "earlier reply"})
    assert (await harness.send("continue"))["status"] == "ignored"
    assert len(harness.requests) == 1


async def test_bots_do_not_disarm_or_gain_followup_routing(harness: _Harness) -> None:
    await harness.send("<@BOT> help")
    assert (await harness.send("automation", user="UBOT", bot_id="B2"))["status"] == "ignored"
    assert (await harness.send("bot reply", user="BOT", bot_id="B1"))["status"] == "ignored"
    assert (await harness.send("continue"))["status"] == "accepted"


async def test_idle_expiry_requires_new_mention_and_activity_extends_it(harness: _Harness) -> None:
    await harness.send("<@BOT> help")
    harness.now += solo_threads.SOLO_THREAD_IDLE_SECONDS - 2
    assert (await harness.send("continue"))["status"] == "accepted"
    harness.now += solo_threads.SOLO_THREAD_IDLE_SECONDS - 2
    assert (await harness.send("again"))["status"] == "accepted"
    harness.now += solo_threads.SOLO_THREAD_IDLE_SECONDS
    assert (await harness.send("too late"))["status"] == "ignored"
    await harness.send("<@BOT> resume")
    assert (await harness.send("continue"))["status"] == "accepted"


@pytest.mark.parametrize(
    "page",
    [
        {"messages": []},
        {"messages": [{"ts": "1001.0", "user": "U1"}]},
        {"messages": [{"ts": "1000.0", "user": "U1"}], "has_more": True},
        {"messages": [{"ts": "1000.0", "user": "U1"}], "response_metadata": {"next_cursor": "0"}},
    ],
)
async def test_incomplete_history_never_arms(harness: _Harness, page: JsonObject) -> None:
    harness.pages = [page]
    assert (await harness.send("<@BOT> help"))["status"] == "accepted"
    assert (await harness.send("continue"))["status"] == "ignored"


async def test_slack_failure_preserves_mentions_but_fails_closed_for_followups(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await harness.send("<@BOT> help")
    monkeypatch.setattr(harness, "conversations_replies", AsyncMock(side_effect=TimeoutError))
    assert (await harness.send("continue"))["status"] == "ignored"
    assert (await harness.send("<@BOT> continue"))["status"] == "accepted"
