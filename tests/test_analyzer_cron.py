from __future__ import annotations

from typing import Any

import pytest

from agent.dashboard import analyzer_cron


class _FakeCrons:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    async def create(self, assistant_id: str, **kwargs: Any) -> dict[str, Any]:
        self.created.append({"assistant_id": assistant_id, **kwargs})
        return {"cron_id": "cron_123"}

    async def delete(self, cron_id: str) -> None:
        self.deleted.append(cron_id)


class _FakeClient:
    def __init__(self) -> None:
        self.crons = _FakeCrons()


@pytest.fixture
def fake_client(monkeypatch) -> _FakeClient:  # noqa: ANN001
    client = _FakeClient()
    monkeypatch.setattr(analyzer_cron, "_client", lambda: client)
    return client


def _patch_record(monkeypatch, record: dict[str, Any] | None) -> dict[str, Any]:  # noqa: ANN001
    updates: dict[str, Any] = {}

    async def fake_get(full_name: str) -> dict[str, Any] | None:
        return record

    async def fake_update(full_name: str, patch: dict[str, Any]) -> dict[str, Any]:
        updates.update(patch)
        return {**(record or {}), **patch}

    monkeypatch.setattr(analyzer_cron, "get_review_style", fake_get)
    monkeypatch.setattr(analyzer_cron, "update_review_style", fake_update)
    return updates


async def test_ensure_continual_cron_creates_and_stores(monkeypatch, fake_client) -> None:  # noqa: ANN001
    updates = _patch_record(monkeypatch, {"full_name": "o/r", "continual_cron_id": None})

    cron_id = await analyzer_cron.ensure_continual_cron("o/r")

    assert cron_id == "cron_123"
    assert len(fake_client.crons.created) == 1
    created = fake_client.crons.created[0]
    assert created["assistant_id"] == "analyzer"
    assert created["config"]["configurable"]["analyzer_mode"] == "continual"
    assert "/continual-learning/SKILL.md" in created["input"]["files"]
    assert updates["continual_cron_id"] == "cron_123"


async def test_ensure_continual_cron_idempotent(monkeypatch, fake_client) -> None:  # noqa: ANN001
    _patch_record(monkeypatch, {"full_name": "o/r", "continual_cron_id": "existing"})

    cron_id = await analyzer_cron.ensure_continual_cron("o/r")

    assert cron_id == "existing"
    assert fake_client.crons.created == []


async def test_remove_continual_cron(monkeypatch, fake_client) -> None:  # noqa: ANN001
    updates = _patch_record(monkeypatch, {"full_name": "o/r", "continual_cron_id": "cron_123"})

    await analyzer_cron.remove_continual_cron("o/r")

    assert fake_client.crons.deleted == ["cron_123"]
    assert updates["continual_cron_id"] is None


async def test_remove_continual_cron_noop_when_absent(monkeypatch, fake_client) -> None:  # noqa: ANN001
    _patch_record(monkeypatch, {"full_name": "o/r", "continual_cron_id": None})

    await analyzer_cron.remove_continual_cron("o/r")

    assert fake_client.crons.deleted == []


def test_daily_schedule_is_stable_and_in_window() -> None:
    sched = analyzer_cron._daily_schedule("o/r")
    sched2 = analyzer_cron._daily_schedule("o/r")
    assert sched == sched2
    minute, hour, *_ = sched.split()
    assert 0 <= int(minute) <= 59
    assert 5 <= int(hour) <= 8
