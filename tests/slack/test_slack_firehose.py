import asyncio
from typing import Any

import pytest

from agent.utils import slack_firehose


@pytest.fixture(autouse=True)
def _reset_firehose():
    slack_firehose._threads.clear()
    slack_firehose._chain.clear()
    slack_firehose._task_cards_supported = True
    yield
    slack_firehose._threads.clear()
    slack_firehose._chain.clear()


class _FakeSlack:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.replies: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.reply_failures = 0
        self._ts = 0

    def _next_ts(self) -> str:
        self._ts += 1
        return f"170000000.{self._ts:06d}"

    async def post_top_level(self, channel_id, text, **kwargs):
        self.posts.append({"channel": channel_id, "text": text, **kwargs})
        return self._next_ts(), None

    async def post_reply(self, channel_id, thread_ts, text, **kwargs):
        self.replies.append({"channel": channel_id, "thread_ts": thread_ts, "text": text, **kwargs})
        if self.reply_failures > 0:
            self.reply_failures -= 1
            return None, "invalid_blocks"
        return self._next_ts(), None

    async def update(self, channel_id, message_ts, text, **kwargs):
        self.updates.append({"channel": channel_id, "ts": message_ts, "text": text, **kwargs})
        return True, None


class _FakeClient:
    def __init__(self, metadata: dict[str, Any]) -> None:
        self.threads = self
        self.metadata = metadata
        self.updates: list[dict[str, Any]] = []

    async def get(self, thread_id: str) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": self.metadata}

    async def update(self, thread_id: str, metadata: dict[str, Any]) -> None:
        self.updates.append({"thread_id": thread_id, **metadata})
        self.metadata.update(metadata)


@pytest.fixture
def slack(monkeypatch) -> _FakeSlack:
    fake = _FakeSlack()
    monkeypatch.setattr(slack_firehose, "post_slack_top_level_message_with_ts", fake.post_top_level)
    monkeypatch.setattr(slack_firehose, "post_slack_thread_reply_with_ts", fake.post_reply)
    monkeypatch.setattr(slack_firehose, "update_slack_message", fake.update)
    return fake


@pytest.fixture
def client(monkeypatch) -> _FakeClient:
    fake = _FakeClient({})
    monkeypatch.setattr(slack_firehose, "get_client", lambda: fake)
    return fake


@pytest.fixture
def channel(monkeypatch) -> str:
    async def channel_id() -> str:
        return "C_FIREHOSE"

    monkeypatch.setattr(slack_firehose, "get_team_firehose_channel_id", channel_id)
    return "C_FIREHOSE"


async def _drain() -> None:
    while slack_firehose._chain:
        await asyncio.gather(*list(slack_firehose._chain.values()), return_exceptions=True)


async def test_thread_is_duplicated_into_the_firehose(slack, client, channel):
    slack_firehose.record_inbound(
        "t1", text="fix the flaky test", message_id="h1", source="slack", requester="ramon"
    )
    slack_firehose.record_turn(
        "t1",
        text="On it.",
        tool_calls=[{"name": "execute", "args": {"command": "pytest -q"}}],
        message_id="a1",
    )
    slack_firehose.record_run_end("t1")
    await _drain()

    assert len(slack.posts) == 1
    assert slack.posts[0]["channel"] == channel
    assert client.updates[0]["firehose_channel_id"] == channel

    # Inbound prose, the agent's prose, then one rolling activity card.
    assert len(slack.replies) == 3
    card = slack.replies[2]["blocks"][0]
    assert card["type"] == "task_card"
    assert card["status"] == "in_progress"
    assert slack.updates[-1]["blocks"][0]["status"] == "complete"


async def test_tool_calls_collapse_into_one_card(slack, client, channel):
    slack_firehose.record_inbound("t2", text="go", message_id="h1", source="dashboard")
    for i in range(3):
        slack_firehose.record_turn(
            "t2",
            text="",
            tool_calls=[{"name": "read_file", "args": {"file_path": f"a{i}.py"}}],
            message_id=f"a{i}",
        )
    await _drain()

    tool_replies = [r for r in slack.replies if r["blocks"][0]["type"] == "task_card"]
    assert len(tool_replies) == 1
    assert slack.updates[-1]["blocks"][0]["title"] == "3 tool calls"


async def test_prose_starts_a_fresh_card(slack, client, channel):
    slack_firehose.record_inbound("t3", text="go", message_id="h1", source="dashboard")
    slack_firehose.record_turn(
        "t3", text="", tool_calls=[{"name": "ls", "args": {"path": "."}}], message_id="a1"
    )
    slack_firehose.record_turn(
        "t3", text="Found it.", tool_calls=[{"name": "ls", "args": {"path": "/"}}], message_id="a2"
    )
    await _drain()

    assert [r["blocks"][0]["type"] for r in slack.replies] == [
        "markdown",
        "task_card",
        "markdown",
        "task_card",
    ]
    # The card above the prose settles instead of sitting at in_progress forever.
    assert slack.updates[0]["blocks"][0]["status"] == "complete"


async def test_existing_root_is_reused_across_processes(slack, client, channel):
    client.metadata.update({"firehose_channel_id": channel, "firehose_thread_ts": "111.222"})
    slack_firehose.record_inbound("t4", text="follow up", message_id="h2", source="linear")
    await _drain()

    assert slack.posts == []
    assert slack.replies[0]["thread_ts"] == "111.222"


async def test_repeated_message_is_mirrored_once(slack, client, channel):
    slack_firehose.record_inbound("t5", text="go", message_id="h1", source="dashboard")
    slack_firehose.record_turn("t5", text="hello", tool_calls=[], message_id="a1")
    slack_firehose.record_turn("t5", text="hello", tool_calls=[], message_id="a1")
    await _drain()

    assert len(slack.replies) == 2


async def test_falls_back_when_task_cards_are_rejected(slack, client, channel):
    slack.reply_failures = 1
    slack_firehose.record_inbound("t6", text="go", message_id="h1", source="dashboard")
    await _drain()
    slack.reply_failures = 1
    slack_firehose.record_turn(
        "t6", text="", tool_calls=[{"name": "ls", "args": {"path": "."}}], message_id="a1"
    )
    await _drain()

    assert slack_firehose._task_cards_supported is False
    assert slack.replies[-1]["blocks"][0]["type"] == "markdown"


async def test_firehose_stays_off_without_a_channel(monkeypatch, slack, client):
    async def no_channel() -> None:
        return None

    monkeypatch.setattr(slack_firehose, "get_team_firehose_channel_id", no_channel)
    slack_firehose.record_inbound("t7", text="go", message_id="h1", source="dashboard")
    slack_firehose.record_turn("t7", text="hi", tool_calls=[], message_id="a1")
    await _drain()

    assert slack.posts == []
    assert slack.replies == []


async def test_slack_failure_never_escapes(monkeypatch, client, channel):
    async def boom(*args, **kwargs):
        raise RuntimeError("slack is down")

    monkeypatch.setattr(slack_firehose, "post_slack_top_level_message_with_ts", boom)
    slack_firehose.record_inbound("t8", text="go", message_id="h1", source="dashboard")
    await _drain()


def test_tool_calls_render_their_most_useful_argument():
    assert (
        slack_firehose.describe_tool_call({"name": "execute", "args": {"command": "git  status"}})
        == "execute: git status"
    )
    assert (
        slack_firehose.describe_tool_call({"name": "task", "args": {"subagent_type": "browser"}})
        == "task: browser"
    )
    assert slack_firehose.describe_tool_call({"name": "mystery", "args": {"x": 1}}) == "mystery"
