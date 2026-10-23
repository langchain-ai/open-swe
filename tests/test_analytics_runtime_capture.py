"""Capture for agent-runtime events: tool output budgets and conversation compaction."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from agent.analytics import emitter, usage
from agent.analytics.events import EventName
from agent.database import analytics as database
from agent.run_config import RunConfig


@pytest.fixture
def capture_config(monkeypatch):
    monkeypatch.setenv("POSTGRES_URI", "postgresql://localhost/analytics_test")
    monkeypatch.setattr(database, "_WORKSPACE_ID", uuid4())


def _runtime(monkeypatch, invocation_id: str = "inv-1", thread_id: str = "thread-1") -> None:
    monkeypatch.setattr(
        RunConfig,
        "from_runtime",
        classmethod(lambda cls: RunConfig(invocation_id=invocation_id, thread_id=thread_id)),
    )


def _no_runtime(monkeypatch) -> None:
    def reject(cls):
        raise RuntimeError("called outside a runnable context")

    monkeypatch.setattr(RunConfig, "from_runtime", classmethod(reject))


@pytest.mark.asyncio
async def test_tool_output_truncated_records_loss_and_identity(monkeypatch, capture_config):
    _runtime(monkeypatch)
    enqueue = AsyncMock()
    monkeypatch.setattr(emitter, "enqueue", enqueue)

    await usage.record_tool_output_truncated(
        tool="fetch_url",
        outcome="truncated",
        original_chars=250_000,
        kept_chars=100_000,
    )

    [event] = [call.args[0] for call in enqueue.await_args_list]
    assert event.event_name == EventName.TOOL_OUTPUT_TRUNCATED
    assert event.payload.tool == "fetch_url"
    assert event.payload.outcome == "truncated"
    assert event.payload.original_chars == 250_000
    assert event.payload.kept_chars == 100_000
    assert event.run_id is not None
    assert event.thread_id is not None


@pytest.mark.asyncio
async def test_tool_output_truncated_without_runtime_is_dropped(monkeypatch, capture_config):
    _no_runtime(monkeypatch)
    enqueue = AsyncMock()
    monkeypatch.setattr(emitter, "enqueue", enqueue)

    await usage.record_tool_output_truncated(
        tool="http_request",
        outcome="offloaded",
        original_chars=120_000,
        kept_chars=0,
    )

    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_context_offloaded_records_compaction(monkeypatch, capture_config):
    _runtime(monkeypatch)
    enqueue = AsyncMock()
    monkeypatch.setattr(emitter, "enqueue", enqueue)

    await usage.record_context_offloaded(
        trigger="automatic",
        status="completed",
        cutoff_index=42,
        messages_summarized=42,
        summarized_tokens=90_000,
        summary_tokens=1_200,
        duration_ms=3_500,
    )

    [event] = [call.args[0] for call in enqueue.await_args_list]
    assert event.event_name == EventName.CONTEXT_OFFLOADED
    assert event.payload.trigger == "automatic"
    assert event.payload.status == "completed"
    assert event.payload.cutoff_index == 42
    assert event.payload.messages_summarized == 42
    assert event.payload.summarized_tokens == 90_000
    assert event.payload.summary_tokens == 1_200
    assert event.payload.duration_ms == 3_500
    assert event.run_id is not None
    assert event.thread_id is not None


@pytest.mark.asyncio
async def test_context_offloaded_failure_keeps_cutoff_unknown(monkeypatch, capture_config):
    _runtime(monkeypatch)
    enqueue = AsyncMock()
    monkeypatch.setattr(emitter, "enqueue", enqueue)

    await usage.record_context_offloaded(
        trigger="manual",
        status="failed",
        messages_summarized=12,
        summarized_tokens=40_000,
        duration_ms=800,
    )

    [event] = [call.args[0] for call in enqueue.await_args_list]
    assert event.payload.status == "failed"
    assert event.payload.cutoff_index is None
    assert event.payload.messages_summarized == 12
    assert event.payload.summarized_tokens == 40_000
    assert event.payload.summary_tokens is None
    assert event.payload.duration_ms == 800
