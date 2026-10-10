"""Regression coverage for incident cutover and persisted lifecycle state."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from openswe.database import postgres
from openswe.incidents import backfill, documents, service
from openswe.incidents.models import Incident, IncidentPolicy
from openswe.incidents.persistence import IncidentRow


async def test_backfill_preserves_opaque_keys_and_payloads_without_overwriting(monkeypatch):
    legacy = {
        kind: [
            {"namespace": ["incidents", kind], "key": f"opaque-{i}", "value": {"extra": i}}
            for i in range(3)
        ]
        for kind in backfill.KINDS
    }
    legacy["policies"][0]["key"] = "default"
    await service.POLICIES.put("default", IncidentPolicy(version=7))

    async def search(namespace, *, limit, offset):
        return {"items": legacy[namespace[1]][offset : offset + limit]}

    client = AsyncMock()
    client.store.search_items.side_effect = search
    monkeypatch.setattr(backfill, "store_client", lambda: client)
    assert await backfill.backfill(page_size=2) == 17
    assert await backfill.backfill(page_size=2) == 0
    assert (await service.get_policy()).version == 7
    async with postgres.session() as session:
        rows = (await session.scalars(select(IncidentRow))).all()
        assert len(rows) == 18
        assert all(
            row.payload == {"extra": int(row.key[-1])} for row in rows if row.key != "default"
        )


async def test_incidents_roundtrip_filter_delete_and_invalidate(monkeypatch):
    invalidate = AsyncMock()
    monkeypatch.setattr(service.INCIDENTS, "_invalidate", invalidate)
    record = Incident(
        id=service.incident_id("T1", "C1"),
        workspace_id="T1",
        channel_id="C1",
        thread_id="thread-1",
        status="completed",
        completed_run_id="finished",
    )
    await service.INCIDENTS.put(record.id, record)
    assert await service.INCIDENTS.get(record.id) == record
    assert await service.INCIDENTS.search(filter={"thread_id": "thread-1"}) == [record]
    assert await service.INCIDENTS.search(filter={"thread_id": "other"}) == []
    await service.INCIDENTS.delete(record.id)
    assert await service.INCIDENTS.get(record.id) is None
    assert invalidate.await_count == 2
    with pytest.raises(RuntimeError, match="PostgreSQL is not configured"):
        monkeypatch.delenv("POSTGRES_URI")
        monkeypatch.delenv("LANGSMITH_LANGGRAPH_API_VARIANT", raising=False)
        await documents.SUMMARIES.get(record.id)
