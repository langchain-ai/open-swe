from __future__ import annotations

import importlib
from datetime import UTC, datetime
from typing import Any

import pytest

wakeup_tool = importlib.import_module("agent.tools.schedule_thread_wakeup")


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


def test_schedule_thread_wakeup_rejects_zero_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wakeup_tool, "get_config", _config)
    result = wakeup_tool.schedule_thread_wakeup(0)
    assert result["success"] is False
    assert "positive" in result["error"].lower()


def test_schedule_thread_wakeup_rejects_negative_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wakeup_tool, "get_config", _config)
    result = wakeup_tool.schedule_thread_wakeup(-5)
    assert result["success"] is False


def test_schedule_thread_wakeup_rejects_delay_over_24h(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wakeup_tool, "get_config", _config)
    result = wakeup_tool.schedule_thread_wakeup(1441)
    assert result["success"] is False
    assert "1440" in result["error"]


def test_schedule_thread_wakeup_rejects_missing_thread_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        wakeup_tool,
        "get_config",
        lambda: {"configurable": {"source": "slack"}},
    )
    result = wakeup_tool.schedule_thread_wakeup(5)
    assert result["success"] is False
    assert "thread_id" in result["error"].lower()


def test_schedule_thread_wakeup_creates_cron(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_create_wakeup_cron(
        *,
        thread_id: str,
        fire_time: datetime,
        prompt: str,
        configurable: dict[str, Any],
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

    monkeypatch.setattr(wakeup_tool, "get_config", _config)
    monkeypatch.setattr(wakeup_tool, "_create_wakeup_cron", fake_create_wakeup_cron)

    result = wakeup_tool.schedule_thread_wakeup(10, prompt="Check CI status")

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


def test_schedule_thread_wakeup_uses_default_prompt_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_create_wakeup_cron(
        *,
        thread_id: str,
        fire_time: datetime,
        prompt: str,
        configurable: dict[str, Any],
    ) -> dict[str, Any]:
        captured["prompt"] = prompt
        return {"success": True, "cron_id": "cron-1", "scheduled_for": "", "thread_id": thread_id}

    monkeypatch.setattr(wakeup_tool, "get_config", _config)
    monkeypatch.setattr(wakeup_tool, "_create_wakeup_cron", fake_create_wakeup_cron)

    result = wakeup_tool.schedule_thread_wakeup(5)
    assert result["success"] is True
    assert "automated re-trigger" in captured["prompt"].lower()


def test_schedule_thread_wakeup_uses_default_prompt_when_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_create_wakeup_cron(
        *,
        thread_id: str,
        fire_time: datetime,
        prompt: str,
        configurable: dict[str, Any],
    ) -> dict[str, Any]:
        captured["prompt"] = prompt
        return {"success": True, "cron_id": "cron-1", "scheduled_for": "", "thread_id": thread_id}

    monkeypatch.setattr(wakeup_tool, "get_config", _config)
    monkeypatch.setattr(wakeup_tool, "_create_wakeup_cron", fake_create_wakeup_cron)

    result = wakeup_tool.schedule_thread_wakeup(5, prompt="   ")
    assert result["success"] is True
    assert "automated re-trigger" in captured["prompt"].lower()


def test_schedule_thread_wakeup_returns_error_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create_wakeup_cron(
        *,
        thread_id: str,
        fire_time: datetime,
        prompt: str,
        configurable: dict[str, Any],
    ) -> dict[str, Any]:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(wakeup_tool, "get_config", _config)
    monkeypatch.setattr(wakeup_tool, "_create_wakeup_cron", fake_create_wakeup_cron)

    result = wakeup_tool.schedule_thread_wakeup(5)
    assert result["success"] is False
    assert "connection refused" in result["error"]


def test_schedule_thread_wakeup_does_not_pass_none_configurable_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_create_wakeup_cron(
        *,
        thread_id: str,
        fire_time: datetime,
        prompt: str,
        configurable: dict[str, Any],
    ) -> dict[str, Any]:
        captured["configurable"] = configurable
        return {"success": True, "cron_id": "cron-1", "scheduled_for": "", "thread_id": thread_id}

    monkeypatch.setattr(wakeup_tool, "get_config", _config)
    monkeypatch.setattr(wakeup_tool, "_create_wakeup_cron", fake_create_wakeup_cron)

    result = wakeup_tool.schedule_thread_wakeup(5)
    assert result["success"] is True
    cfg = captured["configurable"]
    assert "linear_issue" not in cfg
    assert "schedule_id" not in cfg
    assert cfg["thread_id"] == "test-thread-123"


def test_ceil_to_next_minute_keeps_exact_minute() -> None:
    value = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)
    assert wakeup_tool._ceil_to_next_minute(value) == value


def test_ceil_to_next_minute_rounds_up_with_seconds() -> None:
    value = datetime(2025, 1, 15, 14, 30, 59, 123, tzinfo=UTC)
    assert wakeup_tool._ceil_to_next_minute(value) == value.replace(
        minute=31, second=0, microsecond=0
    )


def test_ceil_to_next_minute_handles_day_boundary() -> None:
    value = datetime(2025, 1, 31, 23, 59, 59, tzinfo=UTC)
    assert wakeup_tool._ceil_to_next_minute(value) == value.replace(
        year=2025, month=2, day=1, hour=0, minute=0, second=0
    )


def test_build_one_shot_cron_format() -> None:
    fire_time = datetime(2025, 1, 15, 14, 30, tzinfo=UTC)
    cron = wakeup_tool._build_one_shot_cron(fire_time)
    parts = cron.split(" ")
    assert len(parts) == 5
    assert parts[0] == "30"
    assert parts[1] == "14"
    assert parts[2] == "15"
    assert parts[3] == "1"
    assert parts[4] == "*"


def test_build_one_shot_cron_handles_month_boundary() -> None:
    fire_time = datetime(2025, 12, 31, 23, 59, tzinfo=UTC)
    cron = wakeup_tool._build_one_shot_cron(fire_time)
    parts = cron.split(" ")
    assert parts[0] == "59"
    assert parts[1] == "23"
    assert parts[2] == "31"
    assert parts[3] == "12"
