import asyncio
import importlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

wakeup_tool = importlib.import_module("openswe.tools.schedule_thread_wakeup")

# Captured before the autouse stub replaces it, for the one test that needs the real wrapper.
_real_purge_best_effort = wakeup_tool._purge_expired_wakeups_best_effort


@pytest.fixture(autouse=True)
def _stub_purge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the opportunistic purge from touching the network in every test."""

    async def _noop() -> None:
        return None

    async def _active(client: Any, thread_id: str, fallback: Any) -> Any:
        return fallback

    monkeypatch.setattr(wakeup_tool, "_purge_expired_wakeups_best_effort", _noop)
    monkeypatch.setattr(wakeup_tool, "get_active_slack_thread", _active)
    monkeypatch.setattr(wakeup_tool, "get_client", lambda url: _FakeClient([]))


class _FakeCrons:
    def __init__(self, crons: list[dict[str, Any]]) -> None:
        self._crons = list(crons)
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.search_calls: list[dict[str, Any]] = []

    async def create_for_thread(
        self, thread_id: str, assistant_id: str, **kwargs: Any
    ) -> dict[str, str]:
        cron_id = f"cron-{len(self.created) + 1}"
        self.created.append(
            {"cron_id": cron_id, "thread_id": thread_id, "assistant_id": assistant_id, **kwargs}
        )
        return {"cron_id": cron_id}

    async def search(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        thread_id: str | None = None,
        limit: int = 10,
        offset: int = 0,
        **_: Any,
    ) -> list[dict[str, Any]]:
        self.search_calls.append({"metadata": metadata, "limit": limit, "offset": offset})
        items = [
            c
            for c in self._crons
            if (thread_id is None or c.get("thread_id") == thread_id)
            and (
                not metadata
                or all((c.get("metadata") or {}).get(k) == v for k, v in metadata.items())
            )
        ]
        return items[offset : offset + limit]

    async def delete(self, cron_id: str) -> None:
        self.deleted.append(cron_id)
        self._crons = [c for c in self._crons if c.get("cron_id") != cron_id]


class _FakeThreads:
    def __init__(
        self,
        messages: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.messages = list(messages or [])
        self.metadata = dict(metadata or {})
        self.updates: list[dict[str, Any]] = []

    async def get_state(self, thread_id: str) -> dict[str, Any]:
        return {"values": {"messages": self.messages}}

    async def get(self, thread_id: str) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": self.metadata}

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        self.updates.append({"thread_id": thread_id, "metadata": metadata})
        self.metadata.update(metadata)


class _FakeClient:
    def __init__(
        self,
        crons: list[dict[str, Any]],
        *,
        messages: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.crons = _FakeCrons(crons)
        self.threads = _FakeThreads(messages, metadata)


def _wakeup_cron(cron_id: str, end_time: datetime | None) -> dict[str, Any]:
    return {
        "cron_id": cron_id,
        "end_time": end_time.isoformat() if end_time else None,
        "metadata": {"kind": "thread_wakeup"},
    }


def _config(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "configurable": {
            "thread_id": "test-thread-123",
            "source": "slack",
            "repo": {"owner": "langchain-ai", "name": "open-swe"},
            "slack_thread": {"channel_id": "C1", "thread_ts": "1.0"},
            "github_login": "johannes117",
            "user_email": "johannes@example.com",
        }
    }
    base["configurable"].update(overrides)
    return base


async def test_cancel_thread_wakeups_removes_every_timer_only_for_that_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timers = [
        {**_wakeup_cron(f"worker-{index}", None), "thread_id": "worker"} for index in range(3)
    ]
    unrelated = [
        {**_wakeup_cron("host-timer", None), "thread_id": "host"},
        {"cron_id": "background", "thread_id": "worker", "metadata": {"kind": "background_tasks"}},
    ]
    client = _FakeClient([*timers, *unrelated])
    monkeypatch.setattr(wakeup_tool, "get_client", lambda url: client)
    monkeypatch.setattr(wakeup_tool, "_PURGE_PAGE_SIZE", 2)

    await wakeup_tool.cancel_thread_wakeups("worker")

    assert await client.crons.search() == unrelated


def _input_message(message_id: str, *, kind: str, sender: str) -> dict[str, str]:
    return {
        "id": message_id,
        "content": (
            f'<input-message sender="{sender}" surface="automation" kind="{kind}">\n'
            f"{message_id}\n</input-message>"
        ),
    }


async def test_schedule_thread_wakeup_rejects_zero_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("openswe.run_config.get_config", _config)
    result = await wakeup_tool.schedule_thread_wakeup(0)
    assert result["success"] is False
    assert "positive" in result["error"].lower()


async def test_schedule_thread_wakeup_rejects_delay_over_24h(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openswe.run_config.get_config", _config)
    result = await wakeup_tool.schedule_thread_wakeup(1441)
    assert result["success"] is False
    assert "1440" in result["error"]


async def test_schedule_thread_wakeup_creates_cron(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_create_wakeup_cron(
        *,
        thread_id: str,
        fire_time: datetime,
        prompt: str,
        configurable: dict[str, Any],
        client: Any,
    ) -> dict[str, Any]:
        captured.update(
            {
                "thread_id": thread_id,
                "fire_time": fire_time,
                "prompt": prompt,
                "configurable": configurable,
            }
        )
        return {
            "success": True,
            "cron_id": "cron-abc",
            "scheduled_for": fire_time.isoformat(),
            "thread_id": thread_id,
        }

    monkeypatch.setattr("openswe.run_config.get_config", _config)
    monkeypatch.setattr(wakeup_tool, "_create_wakeup_cron", fake_create_wakeup_cron)

    result = await wakeup_tool.schedule_thread_wakeup(10, prompt="Check CI status")

    assert result["success"] is True
    assert result["cron_id"] == "cron-abc"
    assert result["thread_id"] == "test-thread-123"
    assert captured["thread_id"] == "test-thread-123"
    assert captured["prompt"] == "Check CI status"
    assert captured["configurable"]["thread_id"] == "test-thread-123"
    assert captured["configurable"]["source"] == "slack"
    assert captured["configurable"]["repo"] == {"owner": "langchain-ai", "name": "open-swe"}
    assert captured["configurable"]["slack_thread"] == {"channel_id": "C1", "thread_ts": "1.0"}
    assert captured["configurable"]["github_login"] == "johannes117"

    now = datetime.now(UTC)
    delay = (captured["fire_time"] - now).total_seconds()
    assert delay >= 600
    assert delay < 660
    assert captured["fire_time"].second == 0
    assert captured["fire_time"].microsecond == 0


async def test_new_human_message_resets_wakeup_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    human = _input_message("user-1", kind="human", sender="slack:U1")
    client = _FakeClient([], messages=[human])
    generation = wakeup_tool._latest_human_generation([human])
    client.threads.metadata.update(
        {
            wakeup_tool._WAKEUP_GENERATION_METADATA_KEY: generation,
            wakeup_tool._WAKEUP_COUNT_METADATA_KEY: 10,
        }
    )
    client.threads.messages.extend(
        [
            _input_message("wakeup-10", kind="system", sender="system:thread-wakeup"),
            _input_message("user-2", kind="human", sender="slack:U1"),
        ]
    )
    monkeypatch.setattr("openswe.run_config.get_config", _config)
    monkeypatch.setattr(wakeup_tool, "get_client", lambda url: client)

    result = await wakeup_tool.schedule_thread_wakeup(5)

    assert result["success"] is True
    assert client.threads.metadata[wakeup_tool._WAKEUP_COUNT_METADATA_KEY] == 1
    assert client.threads.metadata[wakeup_tool._WAKEUP_GENERATION_METADATA_KEY] != generation


async def test_system_wakeup_does_not_reset_wakeup_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    human = _input_message("user-1", kind="human", sender="slack:U1")
    client = _FakeClient(
        [],
        messages=[
            human,
            _input_message("wakeup-10", kind="system", sender="system:thread-wakeup"),
        ],
        metadata={
            wakeup_tool._WAKEUP_GENERATION_METADATA_KEY: (
                wakeup_tool._latest_human_generation([human])
            ),
            wakeup_tool._WAKEUP_COUNT_METADATA_KEY: 10,
        },
    )
    monkeypatch.setattr("openswe.run_config.get_config", _config)
    monkeypatch.setattr(wakeup_tool, "get_client", lambda url: client)

    result = await wakeup_tool.schedule_thread_wakeup(5)

    assert result["success"] is False
    assert not client.crons.created


async def test_schedule_does_not_create_cron_when_budget_cannot_be_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        [],
        messages=[_input_message("user-1", kind="human", sender="slack:U1")],
    )

    async def fail_update(*, thread_id: str, metadata: dict[str, Any]) -> None:
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(client.threads, "update", fail_update)
    monkeypatch.setattr("openswe.run_config.get_config", _config)
    monkeypatch.setattr(wakeup_tool, "get_client", lambda url: client)

    result = await wakeup_tool.schedule_thread_wakeup(5)

    assert result["success"] is False
    assert result["error"] == "Unable to record the thread wakeup limit"
    assert not client.crons.created


async def test_parallel_schedules_share_one_wakeup_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    human = _input_message("user-1", kind="human", sender="slack:U1")
    generation = wakeup_tool._latest_human_generation([human])
    client = _FakeClient(
        [],
        messages=[human],
        metadata={
            wakeup_tool._WAKEUP_GENERATION_METADATA_KEY: generation,
            wakeup_tool._WAKEUP_COUNT_METADATA_KEY: 9,
        },
    )
    monkeypatch.setattr("openswe.run_config.get_config", _config)
    monkeypatch.setattr(wakeup_tool, "get_client", lambda url: client)

    results = await asyncio.gather(
        wakeup_tool.schedule_thread_wakeup(5),
        wakeup_tool.schedule_thread_wakeup(5),
    )

    assert sum(result["success"] is True for result in results) == 1
    assert len(client.crons.created) == 1
    assert client.threads.metadata[wakeup_tool._WAKEUP_COUNT_METADATA_KEY] == 10


async def test_purge_deletes_only_expired_wakeups() -> None:
    now = datetime(2026, 6, 30, 22, 0, tzinfo=UTC)
    client = _FakeClient(
        [
            _wakeup_cron("expired-1", now - timedelta(hours=1)),
            _wakeup_cron("expired-2", now - timedelta(days=1)),
            _wakeup_cron("future-1", now + timedelta(hours=1)),
            _wakeup_cron("no-end", None),
        ]
    )

    deleted = await wakeup_tool.purge_expired_wakeup_crons(client, now=now)

    assert deleted == 2
    assert client.crons.deleted == ["expired-1", "expired-2"]
    # Search is scoped to the thread_wakeup kind so other crons are never seen.
    assert client.crons.search_calls[0]["metadata"] == {"kind": "thread_wakeup"}
