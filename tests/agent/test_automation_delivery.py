import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from openswe.database.postgres import transaction
from openswe.run_config import RunConfig
from openswe.schedules import delivery_outcomes, store
from openswe.schedules.delivery_outcomes import AutomationRunDelivery
from openswe.schedules.store import ScheduleTrigger

pytestmark = pytest.mark.usefixtures("registry_db")

SCHEDULE = "11111111-1111-4111-8111-111111111111"
JOB = uuid.UUID("22222222-2222-4222-8222-222222222222")
THREAD = "agent-thread"


class _Threads:
    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {}

    async def get(self, _thread_id: str) -> SimpleNamespace:
        return SimpleNamespace(metadata=dict(self.metadata))

    async def update(self, _thread_id: str, patch: dict[str, Any]) -> None:
        self.metadata.update(patch)


class _Creator:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: str) -> tuple[str, str]:
        self.sent.append(message)
        return "D1", "1.0"


@pytest.fixture
async def automation(monkeypatch: pytest.MonkeyPatch) -> tuple[_Threads, _Creator]:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://robin.example.com")
    async with transaction() as conn:
        await conn.execute(
            text(
                "INSERT INTO automation (id, name, prompt, admin_thread, model, base_branch, "
                "enabled, created_by, updated_by, user_email, system) VALUES (:id, "
                "'Daily digest', 'Post a digest to #eng', false, 'Default', 'main', true, "
                "'alice', 'alice', '', false)"
            ),
            {"id": uuid.UUID(SCHEDULE)},
        )
        await store._insert_triggers(
            conn, SCHEDULE, [ScheduleTrigger(cron="0 9 * * *")], [uuid.uuid4()], [str(JOB)]
        )
    threads, creator = _Threads(), _Creator()
    monkeypatch.setattr(delivery_outcomes, "THREADS", threads)
    monkeypatch.setattr(
        delivery_outcomes,
        "authorized_schedule_run",
        lambda _cfg: store.get_agent_schedule(SCHEDULE),
    )
    monkeypatch.setattr(
        delivery_outcomes.CreatorDelivery, "for_run", AsyncMock(return_value=creator)
    )
    monkeypatch.setattr(store, "queue", SimpleNamespace(cancel=AsyncMock(return_value=True)))
    return threads, creator


async def _run(run_id: str) -> AutomationRunDelivery:
    config = await store._agent_run_config({"id": SCHEDULE, "system": True}, THREAD, None)
    delivery = AutomationRunDelivery.of(RunConfig.from_config(config), run_id)
    assert delivery is not None
    return delivery


def _answer(success: bool, error: str = "not_in_channel") -> str:
    return json.dumps({"success": True} if success else {"success": False, "error": error})


async def _undelivered_runs() -> int:
    async with transaction() as conn:
        return int(
            await conn.scalar(
                text("SELECT undelivered_runs FROM automation WHERE id = :id"),
                {"id": uuid.UUID(SCHEDULE)},
            )
        )


async def test_a_failed_delivery_tells_the_creator_once(
    automation: tuple[_Threads, _Creator],
) -> None:
    _, creator = automation
    run = await _run("run-1")
    await run.tool_finished("slack_post_message", _answer(False))
    await run.ended()
    await run.ended()

    [notice] = creator.sent
    assert "Daily digest" in notice
    assert "slack_post_message: not_in_channel" in notice
    assert f"https://robin.example.com/agents/{THREAD}" in notice
    assert "paused" not in notice
    assert await _undelivered_runs() == 1


@pytest.mark.parametrize(
    "steps",
    [
        [("slack_post_message", True)],
        [("slack_post_message", False), ("slack_post_message", True)],
        [("slack_reply", True)],
        [],
        [("github_comment", False)],
    ],
    ids=["delivered", "retried", "slack-reply", "quiet", "not-delivery"],
)
async def test_a_run_that_delivered_or_never_failed_sends_no_notice(
    automation: tuple[_Threads, _Creator], steps: list[tuple[str, bool]]
) -> None:
    _, creator = automation
    run = await _run("run-1")
    for name, outcome in steps:
        await run.tool_finished(name, _answer(outcome))
    await run.ended()

    assert creator.sent == []
    assert await _undelivered_runs() == 0


@pytest.mark.parametrize("reset_delivery", [True, None], ids=["slack-success", "quiet-run"])
async def test_three_undelivered_runs_in_a_row_pause_the_automation(
    automation: tuple[_Threads, _Creator], reset_delivery: bool | None
) -> None:
    threads, creator = automation
    for index, delivered in enumerate([False, False, reset_delivery, False, False]):
        run = await _run(f"run-{index}")
        if delivered is not None:
            await run.tool_finished("slack_post_message", _answer(delivered))
        await run.ended()
        await run.ended()
    record = await store.get_agent_schedule(SCHEDULE)
    assert record is not None and record["enabled"] is True
    assert len(creator.sent) == 4
    assert all("paused" not in notice for notice in creator.sent)

    threads.metadata["schedule_test"] = True
    for index, delivered in enumerate([True, False, None]):
        run = await _run(f"test-run-{index}")
        if delivered is not None:
            await run.tool_finished("slack_post_message", _answer(delivered))
        await run.ended()
        assert await _undelivered_runs() == 2
    threads.metadata.pop("schedule_test")

    for marker in (None, run.cfg.automation_invocation_id):
        followup = AutomationRunDelivery.of(
            run.cfg.model_copy(
                update={
                    "origin": "dashboard",
                    "invocation_id": str(uuid.uuid4()),
                    "automation_invocation_id": marker,
                }
            ),
            "dashboard-followup",
        )
        if followup is not None:
            await followup.ended()
        assert await _undelivered_runs() == 2

    run = await _run("run-5")
    await run.tool_finished("slack_post_message", _answer(False))
    await run.ended()

    assert "paused it after 3 runs in a row" in creator.sent[-1]
    record = await store.get_agent_schedule(SCHEDULE)
    assert record is not None
    assert record["enabled"] is False
    assert "Paused after 3 runs" in record["last_error"]
    assert [t["cron_id"] for t in record["triggers"]] == [None]
    store.queue.cancel.assert_awaited_once_with(JOB)  # type: ignore[attr-defined]
