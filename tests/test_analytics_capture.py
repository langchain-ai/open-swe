import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from agent.analytics import directory, emitter


@pytest.fixture
def capture_config(monkeypatch):
    monkeypatch.setenv("ANALYTICS_POSTGRES_URI", "postgresql://localhost/analytics_test")
    monkeypatch.setenv("ANALYTICS_WORKSPACE_ID", str(uuid4()))


@pytest.mark.parametrize(
    "configuration", ["disabled", "missing_workspace", "invalid_workspace", "invalid_uri"]
)
async def test_capture_contains_configuration_errors(monkeypatch, capture_config, configuration):
    if configuration == "disabled":
        for name in ("ANALYTICS_POSTGRES_URI", "POSTGRES_URI", "ANALYTICS_WORKSPACE_ID"):
            monkeypatch.delenv(name, raising=False)
    elif configuration == "missing_workspace":
        monkeypatch.delenv("ANALYTICS_WORKSPACE_ID")
    elif configuration == "invalid_workspace":
        monkeypatch.setenv("ANALYTICS_WORKSPACE_ID", "invalid")
    else:
        monkeypatch.setenv("ANALYTICS_POSTGRES_URI", "invalid")
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
    await directory.upsert_person(provider="github", immutable_person_key=123)
    await directory.upsert_repository(full_name="owner/repo", private=True)

    enqueue.assert_not_awaited()


async def test_capture_contains_payload_and_enqueue_errors(monkeypatch, capture_config, caplog):
    enqueue = AsyncMock(side_effect=RuntimeError("outbox unavailable"))
    monkeypatch.setattr(emitter, "enqueue", enqueue)
    await emitter.run_cost(run_key="run", cost_usd=float("nan"))
    enqueue.assert_not_awaited()
    await emitter.run_terminal(run_key="run", thread_key="thread", status="success")
    assert len(caplog.records) == 2
    assert all(record.message == "Analytics capture failed" for record in caplog.records)


async def test_capture_contains_directory_failures(monkeypatch, capture_config, caplog):
    @asynccontextmanager
    async def unavailable():
        raise RuntimeError("directory unavailable")
        yield

    monkeypatch.setattr(directory, "transaction", unavailable)
    enqueue = AsyncMock()
    monkeypatch.setattr(emitter, "enqueue", enqueue)
    await directory.upsert_person(provider="github", immutable_person_key=123)
    await directory.upsert_repository(full_name="owner/repo", private=True)
    await emitter.run_started(
        run_key="run",
        thread_key="thread",
        model="model",
        source="api",
        immutable_person_key=123,
        repository_key="owner/repo",
    )
    assert len(caplog.records) == 3
    assert enqueue.await_args.args[0].event_name == emitter.EventName.RUN_STARTED

    monkeypatch.setattr(directory, "upsert_model", AsyncMock(side_effect=RuntimeError("failed")))
    await emitter.run_started(
        run_key="run",
        thread_key="thread",
        model="model",
        source="api",
        immutable_person_key=123,
        repository_key="owner/repo",
    )
    assert len(caplog.records) == 4


async def test_capture_preserves_cancellation(monkeypatch, capture_config):
    monkeypatch.setattr(emitter, "enqueue", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await emitter.run_terminal(run_key="run", thread_key="thread", status="success")
