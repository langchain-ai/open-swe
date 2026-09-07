import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from agent.investigations import coordinator, service
from agent.investigations.models import (
    CoordinatorState,
    Investigation,
    InvestigationPolicy,
    PendingRequest,
    Receipt,
)


@pytest.fixture
async def runtime(fake_store, monkeypatch):
    client = SimpleNamespace(
        threads=SimpleNamespace(create=AsyncMock(), delete=AsyncMock()),
        runs=SimpleNamespace(list=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(coordinator, "store_client", lambda: client)
    monkeypatch.setattr(coordinator, "create_durable_run", AsyncMock())
    monkeypatch.setattr(service, "_wake", AsyncMock())
    await service.POLICIES.put(
        "default",
        InvestigationPolicy(
            enabled=True,
            workspace_id="T1",
            slack_app_id="A1",
            enabled_at=100,
        ),
    )
    return client


async def test_only_accepted_events_enroll_and_retries_keep_one_binding(runtime):
    assert await coordinator.coordinate() == {"status": "idle"}
    await service.accept_slack_event(
        {
            "team_id": "T1",
            "api_app_id": "A1",
            "event_id": "E1",
            "event_time": 200,
            "event": {"type": "channel_rename", "channel": {"id": "C1", "name": "inc-api"}},
        }
    )
    first = await coordinator.coordinate()
    second = await coordinator.coordinate()
    assert first["investigation_id"] == second["investigation_id"]
    assert len(await service.INVESTIGATIONS.search_all()) == 1


async def test_one_active_worker_blocks_other_admission(runtime):
    await service.COORDINATORS.put("default", CoordinatorState(active_thread_id="active"))
    runtime.runs.list.return_value = [{"run_id": "running"}]
    assert await coordinator.coordinate() == {"status": "busy"}
    coordinator.create_durable_run.assert_not_awaited()


@pytest.mark.parametrize("status", ["paused", "completed", "watching"])
async def test_durable_questions_are_admitted_without_receipts(runtime, status):
    await service.INVESTIGATIONS.put(
        "i1",
        Investigation(
            id="i1",
            thread_id="i1",
            workspace_id="T1",
            channel_id="C1",
            status=status,
            last_verified_at=time.time(),
            pending_requests=[PendingRequest(id="ask", text="Why?")],
        ),
    )
    assert (await coordinator.coordinate())["status"] == "dispatched"


async def test_failed_admission_is_recoverable_after_active_marker(runtime):
    await service.INVESTIGATIONS.put(
        "i1",
        Investigation(
            id="i1",
            thread_id="i1",
            workspace_id="T1",
            channel_id="C1",
        ),
    )
    coordinator.create_durable_run.side_effect = RuntimeError("offline")
    with pytest.raises(RuntimeError, match="offline"):
        await coordinator.coordinate()
    coordinator.create_durable_run.side_effect = None
    assert (await coordinator.coordinate())["status"] == "dispatched"


async def test_expiry_removes_content_and_checkpoints_without_reenrollment(runtime):
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    id = service.investigation_id("T1", "C1")
    await service.INVESTIGATIONS.put(
        id,
        Investigation(
            id=id,
            thread_id=id,
            workspace_id="T1",
            channel_id="C1",
            created_at=old,
            title="confidential",
            channel_name="inc-api",
            status="paused",
        ),
    )
    await service.RECEIPTS.put(
        "old",
        Receipt(
            id="old",
            workspace_id="T1",
            channel_id="C1",
            kind="message",
            payload={"text": "confidential"},
            received_at=time.time() - 8 * 86400,
        ),
    )
    assert await coordinator.coordinate() == {"status": "idle"}
    record = await service.INVESTIGATIONS.get(id)
    assert record.expired and not record.title and not record.channel_name
    runtime.threads.delete.assert_awaited_once_with(id)
    assert not await service.RECEIPTS.get("old")
    assert await service.accept_slack_event(
        {
            "team_id": "T1",
            "api_app_id": "A1",
            "event_id": "E2",
            "event_time": time.time(),
            "event": {"type": "channel_rename", "channel": {"id": "C1", "name": "inc-new"}},
        }
    ) == {"status": "ignored"}


async def test_policy_change_does_not_admit_old_prefix_receipt(runtime):
    await service.RECEIPTS.put(
        "old",
        Receipt(
            id="old",
            workspace_id="T1",
            channel_id="C1",
            kind="channel_created",
            payload={"channel": {"id": "C1", "name": "incident-api"}},
            source_time=200,
            received_at=time.time(),
        ),
    )
    assert await coordinator.coordinate() == {"status": "idle"}
    assert not await service.INVESTIGATIONS.search_all()


async def test_slack_controls_are_admitted_during_retry_delay(runtime):
    record = Investigation(
        id="i1",
        thread_id="i1",
        workspace_id="T1",
        channel_id="C1",
        status="needs_attention",
        retry_after=time.time() + 3600,
    )
    await service.INVESTIGATIONS.put(record.id, record)
    await service.RECEIPTS.put(
        "pause",
        Receipt(
            id="pause",
            workspace_id="T1",
            channel_id="C1",
            kind="app_mention",
            received_at=time.time(),
            payload={"text": "<@UBOT> pause"},
        ),
    )
    assert (await coordinator.coordinate())["status"] == "dispatched"


async def test_missing_active_thread_does_not_stall_the_workspace(runtime):
    await service.COORDINATORS.put("default", CoordinatorState(active_thread_id="missing"))
    runtime.runs.list.side_effect = httpx.HTTPStatusError(
        "missing", request=httpx.Request("GET", "http://test"), response=httpx.Response(404)
    )
    assert await coordinator.coordinate() == {"status": "idle"}
    assert (await service.COORDINATORS.get("default")).active_thread_id is None


async def test_pending_message_outranks_idle_reverification(runtime):
    now = time.time()
    for key, verified, updated in (
        ("A", now - 3600, "2026-09-07T00:00:00+00:00"),
        ("B", now, "2026-09-07T01:00:00+00:00"),
    ):
        await service.INVESTIGATIONS.put(
            key,
            Investigation(
                id=key,
                workspace_id="T1",
                channel_id=f"C{key}",
                thread_id=key,
                status="watching",
                last_verified_at=verified,
                updated_at=updated,
            ),
        )
    await service.RECEIPTS.put(
        "m1",
        Receipt(
            id="m1",
            workspace_id="T1",
            channel_id="CB",
            kind="message",
            payload={"ts": "300.0", "text": "new evidence"},
            received_at=now,
            source_time=now,
        ),
    )
    assert (await coordinator.coordinate())["investigation_id"] == "B"


async def test_debounced_pass_is_admitted_despite_recent_verification(runtime):
    now = time.time()
    await service.INVESTIGATIONS.put(
        "A",
        Investigation(
            id="A",
            workspace_id="T1",
            channel_id="CA",
            thread_id="A",
            status="watching",
            last_verified_at=now,
            pending_since=now - 1,
        ),
    )
    assert (await coordinator.coordinate())["investigation_id"] == "A"
    paused = await service.INVESTIGATIONS.get("A")
    paused.status = "paused"
    await service.INVESTIGATIONS.put("A", paused)
    await service.COORDINATORS.put("default", CoordinatorState())
    assert await coordinator.coordinate() == {"status": "idle"}


async def test_mention_restarts_the_active_pass_but_plain_messages_wait(runtime):
    now = time.time()
    await service.INVESTIGATIONS.put(
        "A",
        Investigation(
            id="A", workspace_id="T1", channel_id="CA", thread_id="A", status="investigating"
        ),
    )
    await service.COORDINATORS.put("default", CoordinatorState(active_thread_id="A"))
    runtime.runs.list.return_value = [{"run_id": "running"}]
    await service.RECEIPTS.put(
        "m1",
        Receipt(
            id="m1",
            workspace_id="T1",
            channel_id="CA",
            kind="message",
            payload={"ts": "300.0", "text": "more context"},
            received_at=now,
            source_time=now,
        ),
    )
    assert await coordinator.coordinate() == {"status": "busy"}
    coordinator.create_durable_run.assert_not_awaited()
    await service.RECEIPTS.put(
        "q1",
        Receipt(
            id="q1",
            workspace_id="T1",
            channel_id="CA",
            kind="app_mention",
            payload={"ts": "301.0", "text": "<@UBOT> check the deploy", "user": "U1"},
            received_at=now,
            source_time=now,
        ),
    )
    assert await coordinator.coordinate() == {"status": "restarted", "investigation_id": "A"}
    assert coordinator.create_durable_run.await_args.kwargs["multitask_strategy"] == "interrupt"
