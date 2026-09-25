from datetime import UTC, date, datetime

from sqlalchemy import text
from starlette.requests import Request

from agent.database import transaction
from agent.webhooks.inbound import InboundWebhook


async def _partitions() -> set[str]:
    async with transaction() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE i.inhparent = 'inbound_webhooks'::regclass"
            )
        )
        return set(rows.scalars().all())


async def test_rotation_keeps_yesterday_today_and_tomorrow(registry_db: None) -> None:
    await InboundWebhook.rotate_partitions(date(2026, 9, 1))
    assert await _partitions() == {"inbound_webhooks_20260901", "inbound_webhooks_20260902"}

    await InboundWebhook.rotate_partitions(date(2026, 9, 3))
    assert await _partitions() == {
        "inbound_webhooks_20260902",
        "inbound_webhooks_20260903",
        "inbound_webhooks_20260904",
    }


async def test_record_stores_form_bodies_as_objects(registry_db: None) -> None:
    await InboundWebhook.rotate_partitions(datetime.now(UTC).date())
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/webhooks/slack/commands",
            "query_string": b"",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        }
    )

    await InboundWebhook.record(
        request,
        b"command=%2Foswe&text=hello+there",
        "slack",
        event_type="/oswe",
        delivery_id="trigger-1",
    )

    async with transaction() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT source, endpoint, event_type, delivery_id, payload FROM inbound_webhooks"
                )
            )
        ).one()
    assert tuple(row) == (
        "slack",
        "/webhooks/slack/commands",
        "/oswe",
        "trigger-1",
        {"command": "/oswe", "text": "hello there"},
    )
