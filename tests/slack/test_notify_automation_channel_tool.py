import importlib
from typing import Any

import pytest

notification_tool = importlib.import_module("agent.tools.notify_automation_channel")


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: list[str], key: str) -> dict[str, Any] | None:
        value = self.items.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict[str, Any]) -> None:
        self.items[(tuple(namespace), key)] = value

    async def delete_item(self, namespace: list[str], key: str) -> None:
        self.items.pop((tuple(namespace), key), None)


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()


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
    monkeypatch.setattr(notification_tool, "get_client", lambda: client)
    monkeypatch.setattr(
        notification_tool,
        "dashboard_thread_url",
        lambda thread_id: f"https://example.com/agents/{thread_id}",
    )
    return client


async def test_notify_automation_channel_rejects_unauthorized_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        notification_tool,
        "get_config",
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
        notification_tool,
        "get_config",
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
    monkeypatch.setattr(notification_tool, "get_config", _config)

    empty = await notification_tool.notify_automation_channel("   ")
    oversized = await notification_tool.notify_automation_channel("x" * 3_001)

    assert empty == {"success": False, "error": "Message cannot be empty"}
    assert oversized == {
        "success": False,
        "error": "Message must be at most 3000 characters",
    }
    assert fake_client.store.items == {}


async def test_notify_automation_channel_posts_to_trusted_destination(
    fake_client: _FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[dict[str, Any]] = []

    async def fake_post(channel_id: str, text: str, **kwargs: Any) -> tuple[str, None]:
        posted.append({"channel_id": channel_id, "text": text, "kwargs": kwargs})
        return "1786504009.596419", None

    monkeypatch.setattr(notification_tool, "get_config", _config)
    monkeypatch.setattr(notification_tool, "post_slack_top_level_message_with_ts", fake_post)

    result = await notification_tool.notify_automation_channel(
        "Opened a pull request with dependency updates."
    )

    assert result == {"success": True, "message_ts": "1786504009.596419"}
    assert posted[0]["channel_id"] == "C0123456789"
    assert "Dependency check" in posted[0]["text"]
    assert "Opened a pull request" in posted[0]["text"]
    assert "https://example.com/agents/thread_1" in posted[0]["text"]
    stored = fake_client.store.items[(("automation_notifications",), "thread_1")]
    assert stored["status"] == "delivered"
    assert stored["message_ts"] == "1786504009.596419"


async def test_notify_automation_channel_suppresses_duplicate_posts(
    fake_client: _FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_count = 0

    async def fake_post(*args: Any, **kwargs: Any) -> tuple[str, None]:
        nonlocal post_count
        post_count += 1
        return "1786504009.596419", None

    monkeypatch.setattr(notification_tool, "get_config", _config)
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

    monkeypatch.setattr(notification_tool, "get_config", _config)
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
