import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from openswe.analytics import emitter
from openswe.database import analytics as database


@pytest.fixture
def capture_config(monkeypatch):
    monkeypatch.setenv("POSTGRES_URI", "postgresql://localhost/analytics_test")
    monkeypatch.setattr(database, "_WORKSPACE_ID", uuid4())


async def test_capture_preserves_cancellation(monkeypatch, capture_config):
    monkeypatch.setattr(emitter, "enqueue", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await emitter.run_terminal(run_key="run", thread_key="thread", status="success")


async def test_run_started_records_configured_but_not_effective_attribution(
    capture_config, monkeypatch
):
    enqueue = AsyncMock()
    monkeypatch.setattr(emitter, "enqueue", enqueue)
    monkeypatch.setattr(emitter, "upsert_model", AsyncMock())

    await emitter.run_started(
        run_key="run",
        thread_key="thread",
        model="openai:gpt-5.6-sol",
        source="api",
        immutable_person_key=123,
        repository_key="owner/repo",
    )

    event = enqueue.await_args.args[0]
    assert event.payload.configured_model_id is not None
    assert event.payload.effective_model_id is None
    assert event.payload.model_attribution_quality == "configured"
