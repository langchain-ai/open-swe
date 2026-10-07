from typing import Any

import pytest

from openswe.review import analyzer_cron
from openswe.review.styles import REVIEW_STYLES, ReviewStyle


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
    monkeypatch.setattr(analyzer_cron, "langgraph_client", lambda: client)
    return client


def _patch_record(monkeypatch, record: ReviewStyle | None) -> dict[str, Any]:  # noqa: ANN001
    updates: dict[str, Any] = {}

    async def fake_get(full_name: str) -> ReviewStyle | None:
        return record

    async def fake_set_cron(full_name: str, cron_id: str | None) -> ReviewStyle:
        updates["continual_cron_id"] = cron_id
        return (record or ReviewStyle.seed(full_name)).model_copy(
            update={"continual_cron_id": cron_id}
        )

    monkeypatch.setattr(REVIEW_STYLES, "get", fake_get)
    monkeypatch.setattr(REVIEW_STYLES, "set_continual_cron", fake_set_cron)
    return updates


async def test_ensure_continual_cron_idempotent(monkeypatch, fake_client) -> None:  # noqa: ANN001
    _patch_record(monkeypatch, ReviewStyle(full_name="o/r", continual_cron_id="existing"))

    cron_id = await analyzer_cron.ensure_continual_cron("o/r")

    assert cron_id == "existing"
    assert fake_client.crons.created == []


def test_daily_schedule_is_stable_and_in_window() -> None:
    sched = analyzer_cron._daily_schedule("o/r")
    sched2 = analyzer_cron._daily_schedule("o/r")
    assert sched == sched2
    minute, hour, *_ = sched.split()
    assert 0 <= int(minute) <= 59
    assert 5 <= int(hour) <= 8
