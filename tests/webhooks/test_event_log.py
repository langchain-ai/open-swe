from datetime import date

import pytest
from sqlalchemy import text
from starlette.requests import Request

from agent.database import transaction
from agent.webhooks import event_log
from agent.webhooks.event_log import EventLog


async def _partitions() -> set[str]:
    async with transaction() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE i.inhparent = 'event_log'::regclass"
            )
        )
        return set(rows.scalars().all())


async def test_rotation_keeps_yesterday_today_and_tomorrow(registry_db: None) -> None:
    await EventLog.rotate_partitions(date(2026, 9, 1))
    assert await _partitions() == {"event_log_20260901", "event_log_20260902"}

    await EventLog.rotate_partitions(date(2026, 9, 3))
    assert await _partitions() == {
        "event_log_20260902",
        "event_log_20260903",
        "event_log_20260904",
    }


async def test_record_creates_its_partition_and_stores_form_bodies_as_objects(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(event_log, "_ROTATED_AT", None)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/webhooks/slack/commands",
            "query_string": b"",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        }
    )

    await EventLog.record(
        request,
        b"command=%2Foswe&text=hello+there",
        "slack",
        event_type="/oswe",
        delivery_id="trigger-1",
    )

    async with transaction() as conn:
        row = (
            await conn.execute(
                text("SELECT source, endpoint, event_type, delivery_id, payload FROM event_log")
            )
        ).one()
    assert tuple(row) == (
        "slack",
        "/webhooks/slack/commands",
        "/oswe",
        "trigger-1",
        {"command": "/oswe", "text": "hello there"},
    )
