import importlib
from collections.abc import Callable
from typing import Any

import pytest

from agent import store as agent_store
from tests.support.slack_api import SlackAPI

notification_tool = importlib.import_module("agent.tools.notify_automation_channel")


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}
        self.fail_put: Callable[[dict[str, Any]], bool] = lambda value: False

    async def get_item(self, namespace: list[str], key: str) -> dict[str, Any] | None:
        value = self.items.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict[str, Any]) -> None:
        if self.fail_put(value):
            raise RuntimeError("store unavailable")
        self.items[(tuple(namespace), key)] = value

    async def delete_item(self, namespace: list[str], key: str) -> None:
        self.items.pop((tuple(namespace), key), None)


class _FakeThreads:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        self.updates.append({"thread_id": thread_id, "metadata": metadata})


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()
        self.threads = _FakeThreads()


def _config(thread_id: str = "thread_1") -> dict[str, Any]:
    return {
        "configurable": {
            "source": "schedule",
            "schedule_id": "sched_1",
            "thread_id": thread_id,
            "automation_slack_notification": {
                "channel_id": "C0123456789",
                "mode": "on_action",
                "schedule_id": "sched_1",
                "schedule_name": "Dependency check",
            },
        }
    }


def test_notify_automation_channel_exported() -> None:
    from agent.tools import notify_automation_channel

    assert callable(notify_automation_channel)


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(agent_store, "store_client", lambda: client)
    monkeypatch.setattr(notification_tool, "get_client", lambda: client)
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://example.com")
    return client


async def test_notify_automation_channel_rejects_unauthorized_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent.run_config.get_config",
        lambda: {"configurable": {"source": "slack", "thread_id": "thread_1"}},
    )

    result = await notification_tool.notify_automation_channel("Changed dependencies")

    assert result == {
        "success": False,
        "error": "This tool is only available to scheduled runs",
    }


async def test_notify_automation_channel_rejects_nonconditional_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent.run_config.get_config",
        lambda: {"configurable": {"source": "schedule", "thread_id": "thread_1"}},
    )

    result = await notification_tool.notify_automation_channel("Changed dependencies")

    assert result == {
        "success": False,
        "error": "This schedule is not configured for action-only Slack notifications",
    }


async def test_notify_automation_channel_validates_message(
    fake_client: _FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("agent.run_config.get_config", _config)

    empty = await notification_tool.notify_automation_channel("   ")
    oversized = await notification_tool.notify_automation_channel("x" * 3_001)
    missing_summary = await notification_tool.notify_automation_channel("1\n2\n3\n4\n5")
    oversized_summary = await notification_tool.notify_automation_channel(
        "Details", summary="1\n2\n3\n4\n5"
    )

    assert empty == {"success": False, "error": "Content cannot be empty"}
    assert oversized == {
        "success": False,
        "error": "Content must be at most 3000 characters",
    }
    assert missing_summary == {
        "success": False,
        "error": "Summary is required when content exceeds 4 lines",
    }
    assert oversized_summary == {
        "success": False,
        "error": "Summary must be at most 4 lines",
    }
    assert fake_client.store.items == {}


async def test_notify_automation_channel_posts_to_trusted_destination(
    fake_client: _FakeClient, monkeypatch: pytest.MonkeyPatch, slack_api: SlackAPI
) -> None:
    monkeypatch.setattr("agent.run_config.get_config", _config)

    result = await notification_tool.notify_automation_channel(
        "Opened a pull request with dependency updates."
    )

    assert result == {"success": True, "message_ts": "1.0"}
    posted = slack_api.calls[0][1]
    assert posted["channel"] == "C0123456789"
    assert "Dependency check" in posted["text"]
    assert "Opened a pull request" in posted["text"]
    assert "https://example.com/agents/thread_1" in posted["text"]
    stored = fake_client.store.items[(("automation_notifications",), "thread_1")]
    assert stored["status"] == "delivered"
    assert stored["message_ts"] == "1.0"
    assert fake_client.threads.updates == [
        {
            "thread_id": "thread_1",
            "metadata": {"automation_action_posted_at": stored["notified_at"]},
        }
    ]


async def test_notify_automation_channel_posts_long_content_in_thread(
    fake_client: _FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel_posts: list[str] = []
    thread_posts: list[tuple[str, str]] = []

    async def fake_channel_post(channel_id: str, text: str, **kwargs: Any) -> tuple[str, None]:
        channel_posts.append(text)
        return "1786504009.596419", None

    async def fake_thread_post(
        channel_id: str, thread_ts: str, text: str, **kwargs: Any
    ) -> tuple[str, None]:
        thread_posts.append((thread_ts, text))
        return "1786504010.000001", None

    monkeypatch.setattr("agent.run_config.get_config", _config)
    monkeypatch.setattr(
        notification_tool, "post_slack_top_level_message_with_ts", fake_channel_post
    )
    monkeypatch.setattr(notification_tool, "post_slack_thread_reply_with_ts", fake_thread_post)

    content = "First\nSecond\nThird\nFourth\nFifth"
    result = await notification_tool.notify_automation_channel(
        content, summary="Updated five dependencies."
    )

    assert result == {"success": True, "message_ts": "1786504009.596419"}
    assert "Updated five dependencies." in channel_posts[0]
    assert "First" not in channel_posts[0]
    assert thread_posts == [("1786504009.596419", content)]


async def test_notify_automation_channel_retries_only_thread_reply_after_failure(
    fake_client: _FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel_post_count = 0
    thread_responses: list[tuple[str | None, str | None]] = [
        (None, "rate_limited"),
        ("1786504010.000001", None),
    ]

    async def fake_channel_post(*args: Any, **kwargs: Any) -> tuple[str, None]:
        nonlocal channel_post_count
        channel_post_count += 1
        return "1786504009.596419", None

    async def fake_thread_post(*args: Any, **kwargs: Any) -> tuple[str | None, str | None]:
        return thread_responses.pop(0)

    monkeypatch.setattr("agent.run_config.get_config", _config)
    monkeypatch.setattr(
        notification_tool, "post_slack_top_level_message_with_ts", fake_channel_post
    )
    monkeypatch.setattr(notification_tool, "post_slack_thread_reply_with_ts", fake_thread_post)

    content = "First\nSecond\nThird\nFourth\nFifth"
    first = await notification_tool.notify_automation_channel(content, summary="Summary")
    second = await notification_tool.notify_automation_channel(content, summary="Summary")

    assert first == {
        "success": False,
        "error": "Slack thread reply failed: rate_limited",
        "slack_error": "rate_limited",
    }
    assert second == {"success": True, "message_ts": "1786504009.596419"}
    assert channel_post_count == 1
    assert thread_responses == []


async def test_notify_automation_channel_suppresses_duplicate_posts(
    fake_client: _FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_count = 0

    async def fake_post(*args: Any, **kwargs: Any) -> tuple[str, None]:
        nonlocal post_count
        post_count += 1
        return "1786504009.596419", None

    monkeypatch.setattr("agent.run_config.get_config", _config)
    monkeypatch.setattr(notification_tool, "post_slack_top_level_message_with_ts", fake_post)

    first = await notification_tool.notify_automation_channel("Opened a pull request")
    second = await notification_tool.notify_automation_channel("Opened another pull request")

    assert first["success"] is True
    assert second == {
        "success": True,
        "already_notified": True,
        "message_ts": "1786504009.596419",
    }
    assert post_count == 1


async def test_notify_automation_channel_allows_retry_after_slack_failure(
    fake_client: _FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses: list[tuple[str | None, str | None]] = [
        (None, "not_in_channel"),
        ("1786504009.596419", None),
    ]

    async def fake_post(*args: Any, **kwargs: Any) -> tuple[str | None, str | None]:
        return responses.pop(0)

    monkeypatch.setattr("agent.run_config.get_config", _config)
    monkeypatch.setattr(notification_tool, "post_slack_top_level_message_with_ts", fake_post)

    first = await notification_tool.notify_automation_channel("Opened a pull request")
    second = await notification_tool.notify_automation_channel("Opened a pull request")

    assert first == {
        "success": False,
        "error": "Slack post failed: not_in_channel",
        "slack_error": "not_in_channel",
    }
    assert second == {"success": True, "message_ts": "1786504009.596419"}
    assert responses == []


async def test_notify_automation_channel_never_reposts_after_finalize_write_fails(
    fake_client: _FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_count = 0

    async def fake_post(*args: Any, **kwargs: Any) -> tuple[str, None]:
        nonlocal post_count
        post_count += 1
        return "1786504009.596419", None

    monkeypatch.setattr("agent.run_config.get_config", _config)
    monkeypatch.setattr(notification_tool, "post_slack_top_level_message_with_ts", fake_post)
    fake_client.store.fail_put = lambda value: value["status"] == "delivered"

    first = await notification_tool.notify_automation_channel("Opened a pull request")
    second = await notification_tool.notify_automation_channel("Opened a pull request")

    assert first == {"success": True, "message_ts": "1786504009.596419"}
    assert second == {"success": True, "message_ts": "1786504009.596419"}
    assert post_count == 1
    stored = fake_client.store.items[(("automation_notifications",), "thread_1")]
    assert stored["status"] == "posted"
    assert stored["message_ts"] == "1786504009.596419"


async def test_notify_automation_channel_never_reposts_when_post_state_is_unknown(
    fake_client: _FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_count = 0

    async def fake_post(*args: Any, **kwargs: Any) -> tuple[str, None]:
        nonlocal post_count
        post_count += 1
        return "1786504009.596419", None

    async def fake_thread_post(*args: Any, **kwargs: Any) -> tuple[None, str]:
        return None, "rate_limited"

    monkeypatch.setattr("agent.run_config.get_config", _config)
    monkeypatch.setattr(notification_tool, "post_slack_top_level_message_with_ts", fake_post)
    monkeypatch.setattr(notification_tool, "post_slack_thread_reply_with_ts", fake_thread_post)
    fake_client.store.fail_put = lambda value: value["status"] == "posted"

    content = "First\nSecond\nThird\nFourth\nFifth"
    first = await notification_tool.notify_automation_channel(content, summary="Summary")
    second = await notification_tool.notify_automation_channel(content, summary="Summary")

    assert first["success"] is False
    assert first["message_ts"] == "1786504009.596419"
    assert second["success"] is False
    assert "not posting again" in second["error"]
    assert post_count == 1


async def test_notify_automation_channel_resumes_reply_after_finalize_write_fails(
    fake_client: _FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_count = 0
    thread_posts: list[str] = []

    async def fake_post(*args: Any, **kwargs: Any) -> tuple[str, None]:
        nonlocal post_count
        post_count += 1
        return "1786504009.596419", None

    async def fake_thread_post(
        channel_id: str, thread_ts: str, text: str, **kwargs: Any
    ) -> tuple[str, None]:
        thread_posts.append(text)
        return "1786504010.000001", None

    monkeypatch.setattr("agent.run_config.get_config", _config)
    monkeypatch.setattr(notification_tool, "post_slack_top_level_message_with_ts", fake_post)
    monkeypatch.setattr(notification_tool, "post_slack_thread_reply_with_ts", fake_thread_post)
    fake_client.store.fail_put = lambda value: value["status"] == "delivered"

    content = "First\nSecond\nThird\nFourth\nFifth"
    first = await notification_tool.notify_automation_channel(content, summary="Summary")
    second = await notification_tool.notify_automation_channel(content, summary="Summary")

    assert first == {"success": True, "message_ts": "1786504009.596419"}
    assert second == {"success": True, "message_ts": "1786504009.596419"}
    assert post_count == 1
    assert len(thread_posts) == 2
