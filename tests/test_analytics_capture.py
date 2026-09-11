import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from agent.analytics import database, directory, emitter


@pytest.fixture
def capture_config(monkeypatch):
    monkeypatch.setenv("POSTGRES_URI", "postgresql://localhost/analytics_test")
    monkeypatch.setattr(database, "_WORKSPACE_ID", uuid4())


@pytest.mark.parametrize("configuration", ["disabled", "uninitialized", "invalid_uri"])
async def test_capture_contains_configuration_errors(monkeypatch, capture_config, configuration):
    if configuration == "disabled":
        monkeypatch.delenv("POSTGRES_URI", raising=False)
    elif configuration == "uninitialized":
        monkeypatch.setattr(database, "_WORKSPACE_ID", None)
    else:
        monkeypatch.setenv("POSTGRES_URI", "invalid")
    enqueue = AsyncMock()
    monkeypatch.setattr(emitter, "enqueue", enqueue)

    await emitter.run_terminal(run_key="run", thread_key="thread", status="success")
    await emitter.run_started(
        run_key="run",
        thread_key="thread",
        model="model",
        source="api",
        immutable_person_key=123,
        repository_key="owner/repo",
    )
    enqueue.assert_not_awaited()


@pytest.mark.parametrize("invalid_payload", [False, True])
async def test_capture_contains_payload_and_enqueue_errors(
    monkeypatch, capture_config, caplog, invalid_payload
):
    enqueue = AsyncMock(side_effect=RuntimeError("outbox unavailable"))
    monkeypatch.setattr(emitter, "enqueue", enqueue)
    if invalid_payload:
        await emitter.run_cost(run_key="run", cost_usd=float("nan"))
        enqueue.assert_not_awaited()
    else:
        await emitter.run_terminal(run_key="run", thread_key="thread", status="success")
        enqueue.assert_awaited_once()
    assert any(record.exc_info for record in caplog.records)


async def test_capture_contains_directory_failures(monkeypatch, capture_config):
    @asynccontextmanager
    async def unavailable():
        raise RuntimeError("directory unavailable")
        yield

    monkeypatch.setattr(directory, "transaction", unavailable)
    enqueue = AsyncMock()
    monkeypatch.setattr(emitter, "enqueue", enqueue)
    await emitter.run_started(
        run_key="run",
        thread_key="thread",
        model="model",
        source="api",
        immutable_person_key=123,
        repository_key="owner/repo",
    )
    assert enqueue.await_args.args[0].event_name == emitter.EventName.RUN_STARTED


async def test_capture_preserves_cancellation(monkeypatch, capture_config):
    monkeypatch.setattr(emitter, "enqueue", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await emitter.run_terminal(run_key="run", thread_key="thread", status="success")


async def test_run_started_records_configured_but_not_effective_attribution(
    capture_config, monkeypatch
):
    enqueue = AsyncMock()
    monkeypatch.setattr(emitter, "enqueue", enqueue)
    monkeypatch.setattr(directory, "upsert_model", AsyncMock())

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
