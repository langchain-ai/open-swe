"""Capture, delivery, and transactional progress regressions."""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from functools import partial
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx2
import pytest
from langchain_core.messages import HumanMessage, message_to_dict
from runtime_api.loop import RunStarted, ToolResultReport
from runtime_api.provenance import Provenance
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from openswe.agent_api.tokens import RemoteRun, RunSettings
from openswe.analytics import identity, ingestion, outbox, retention, segment
from openswe.analytics.events import (
    EntryPoint,
    EventName,
    FeedbackSubmittedPayload,
    PRDistanceMeasuredPayload,
    RunCompletedPayload,
    RunStartedPayload,
    make_event,
    person_uuid,
)
from openswe.analytics.segment import SEGMENT_JOB, SegmentDelivery, SegmentTrack
from openswe.analytics.segment_activity import SegmentActivity
from openswe.database import analytics as database
from openswe.database import postgres
from openswe.jobs import queue
from openswe.jobs.queue import Job
from openswe.jobs.worker import JobWorker
from openswe.users.models import User
from tests.analytics.conftest import initialize_database
from tests.analytics.helpers import run_event


async def test_capture_start_survives_delivery_retention_and_restart(deployment_db, monkeypatch):
    monkeypatch.setenv("ANALYTICS_RAW_EVENT_MONTHS", "1")
    await initialize_database()
    person = identity.opaque_person("github", 123)
    before = datetime.now(UTC)
    events = [run_event(occurred_at=before - timedelta(days=60), person=person) for _ in range(2)]
    assert await asyncio.gather(*(outbox.enqueue(event) for event in events)) == [True, True]
    async with postgres.connection() as conn:
        started_at = await database.collection_started_at(conn)
    assert started_at is not None
    assert before <= started_at <= datetime.now(UTC)
    assert await outbox.deliver_batch() == 2
    assert not await outbox.enqueue(events[0])

    delayed = run_event(occurred_at=before - timedelta(days=60), person=person)
    assert await ingestion.ingest(delayed)
    assert not await ingestion.ingest(delayed)
    async with postgres.connection() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM run_projection")) == 3
        assert await database.collection_started_at(conn) == started_at
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE outbox SET acknowledged_at = clock_timestamp() - interval '31 days'")
        )
        await conn.execute(
            text("UPDATE ingestion_receipts SET expires_at = clock_timestamp() - interval '1 day'")
        )
    await retention.enforce_retention()
    async with postgres.connection() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM events")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM outbox")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM ingestion_receipts")) == 0
        assert (
            await conn.scalar(text("SELECT sum(event_count) FROM additive_event_projection")) == 3
        )
    await postgres.close()
    await initialize_database()
    assert identity.opaque_person("github", 123) == person
    async with postgres.connection() as conn:
        assert await database.collection_started_at(conn) == started_at
        assert await conn.scalar(text("SELECT count(*) FROM run_projection")) == 3


async def test_failed_ingestion_does_not_start_collection(deployment_db):
    await initialize_database()
    async with postgres.transaction() as conn:
        await conn.execute(
            text("ALTER TABLE additive_event_projection ADD CONSTRAINT reject_event CHECK (false)")
        )
    event = run_event()
    with pytest.raises(IntegrityError):
        await ingestion.ingest(event)
    async with postgres.transaction() as conn:
        assert await database.collection_started_at(conn) is None
        assert await conn.scalar(text("SELECT count(*) FROM events")) == 0
        await conn.execute(
            text("ALTER TABLE additive_event_projection DROP CONSTRAINT reject_event")
        )
    assert await ingestion.ingest(event)
    async with postgres.connection() as conn:
        assert await database.collection_started_at(conn) is not None


async def test_failed_projection_does_not_advance_processing_progress(deployment_db, monkeypatch):
    await initialize_database()
    assert await ingestion.ingest(run_event())
    async with postgres.connection() as conn:
        before = await database.reporting_metadata(conn)
    monkeypatch.setattr(ingestion, "_project", AsyncMock(side_effect=RuntimeError("failed")))
    with pytest.raises(RuntimeError, match="failed"):
        await ingestion.ingest(run_event())
    async with postgres.connection() as conn:
        after = await database.reporting_metadata(conn)
    assert after["last_processed_at"] == before["last_processed_at"]
    async with postgres.connection() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM run_projection")) == 1


class SegmentServer:
    def __init__(self, statuses: list[int]) -> None:
        self.statuses = statuses
        self.requests: list[httpx2.Request] = []

    def respond(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.statuses.pop(0))


class TestSegmentDelivery:
    def test_minimization_rejects_content_and_unbounded_values(self):
        event_id = uuid4()
        body = SegmentTrack(
            messageId=event_id,
            event="agents_run_failed",
            timestamp=datetime.now(UTC),
            anonymousId=uuid4(),
            properties={
                "event_id": str(event_id),
                "failure_class": "timeout",
                "failure_code": "secret-bearing provider error",
                "missing_reason": "customer request",
                "category": "customer-defined category",
                "severity": "customer-defined severity",
                "configured_effort": "private model configuration",
                "repository_full_name": "private/repository",
                "opening_head_sha": "a" * 40,
                "repository_id": "private/repository",
                "input_tokens": 123,
                "output_tokens": True,
                "total_tokens": 2**80,
                "cost_usd": float("inf"),
                "source": "backend",
                "nested": {"prompt": "secret"},
            },
        ).minimized()
        assert body["properties"] == {
            "product": "open-swe",
            "schema_version": 1,
            "event_id": str(event_id),
            "failure_class": "timeout",
            "severity": "unknown",
            "configured_effort": "unknown",
            "input_tokens": 123,
            "source": "backend",
        }
        assert "userId" not in body
        assert "secret" not in json.dumps(body)

    @pytest.mark.parametrize("key", [None, "", " \t "])
    async def test_unconfigured_is_noop_before_database_and_network(self, monkeypatch, key):
        if key is None:
            monkeypatch.delenv("SEGMENT_BROWSER_WRITE_KEY", raising=False)
        else:
            monkeypatch.setenv("SEGMENT_BROWSER_WRITE_KEY", key)
        schedule = AsyncMock()
        monkeypatch.setattr(queue, "schedule", schedule)
        event = make_event(
            workspace_id=uuid4(),
            event_name=EventName.RUN_COMPLETED,
            producer="test",
            producer_event_id="done",
            occurred_at=datetime.now(UTC),
            environment="test",
            payload=RunCompletedPayload(),
        )
        await SegmentDelivery.enqueue_event(event)
        await SegmentDelivery.enqueue_track(
            message_id=event.event_id,
            occurred_at=event.occurred_at,
            workspace_id=event.workspace_id,
            event="invalid",
            properties={},
        )
        job = Job(
            id=uuid4(),
            kind=SEGMENT_JOB,
            payload={},
            due_at=datetime.now(UTC),
            cron=None,
            timezone="UTC",
            key=None,
            metadata={},
            attempts=0,
            created_at=datetime.now(UTC),
        )
        await SegmentDelivery.deliver(job)
        schedule.assert_not_awaited()

    async def test_queue_preserves_canonical_actor_without_storing_write_key(self, monkeypatch):
        monkeypatch.setenv("SEGMENT_BROWSER_WRITE_KEY", "test-segment-key")
        schedule = AsyncMock()
        monkeypatch.setattr(queue, "schedule", schedule)
        message_id, workspace, user = uuid4(), uuid4(), uuid4()
        occurred = datetime(2026, 9, 1, tzinfo=UTC)
        await SegmentDelivery.enqueue_track(
            message_id=message_id,
            occurred_at=occurred,
            workspace_id=workspace,
            event="agents_request_started",
            user_id=user,
            properties={"attribution_role": "actor", "activity_kind": "human_request"},
        )
        kind, body = schedule.call_args.args
        assert kind == SEGMENT_JOB
        assert schedule.call_args.kwargs == {
            "key": f"segment:{workspace}:{message_id}",
            "replace": False,
        }
        assert body["userId"] == str(user)
        assert "anonymousId" not in body
        assert body["messageId"] == str(message_id)
        assert body["timestamp"] == occurred.isoformat()
        assert body["properties"]["attribution_role"] == "actor"
        assert body["properties"]["identity_quality"] == "canonical"
        assert "test-segment-key" not in json.dumps(body)

    async def test_network_failure_propagates_and_retries_same_identity(self, monkeypatch):
        monkeypatch.setenv("SEGMENT_BROWSER_WRITE_KEY", " test-segment-key \t")
        server = SegmentServer([500, 200, 302])
        monkeypatch.setattr(
            segment, "pooled_transport", partial(httpx2.MockTransport, server.respond)
        )
        occurred = datetime(2026, 9, 1, tzinfo=UTC)
        track = SegmentTrack(
            messageId=uuid4(),
            event="agents_run_completed",
            timestamp=occurred,
            anonymousId=uuid4(),
            properties={"source": "backend", "failure_code": "private"},
        )
        job = Job(
            id=uuid4(),
            kind=SEGMENT_JOB,
            payload=track.model_dump(mode="json", exclude_none=True),
            due_at=datetime.now(UTC),
            cron=None,
            timezone="UTC",
            key=None,
            metadata={},
            attempts=1,
            created_at=datetime.now(UTC),
        )
        with pytest.raises(httpx2.HTTPStatusError):
            await SegmentDelivery.deliver(job)
        await SegmentDelivery.deliver(job)
        with pytest.raises(httpx2.HTTPStatusError):
            await SegmentDelivery.deliver(job)
        first, second, _ = server.requests
        assert first.url == "https://api.segment.io/v1/track"
        assert (
            first.headers["authorization"]
            == "Basic " + base64.b64encode(b"test-segment-key:").decode()
        )
        assert first.content == second.content
        payload = json.loads(first.content)
        assert payload["messageId"] == str(track.messageId)
        assert payload["timestamp"] == occurred.isoformat()
        assert payload["properties"] == {
            "product": "open-swe",
            "schema_version": 1,
            "source": "backend",
        }
        assert len(server.requests) == 3

    async def test_canonical_identity_requires_immutable_provider_match(
        self, analytics_db, monkeypatch
    ):
        workspace, tx = analytics_db
        owner, other, directory_id = uuid4(), uuid4(), uuid4()
        person = person_uuid(workspace, "github", "123")
        async with tx() as conn:
            await conn.execute(
                text("INSERT INTO users (id) VALUES (:owner), (:other)"),
                {"owner": owner, "other": other},
            )
            await conn.execute(
                text(
                    "INSERT INTO user_identity (provider, external_id, user_id, login, email) "
                    "VALUES ('github', '123', :owner, 'renamed', 'same@example.test'), "
                    "('github', '456', :other, 'reused', 'same@example.test')"
                ),
                {"owner": owner, "other": other},
            )
            await conn.execute(
                text(
                    "INSERT INTO identity_directory (workspace_id, person_id, github_login, email, anonymize_after) "
                    "VALUES (:workspace, :person, 'reused', 'same@example.test', clock_timestamp())"
                ),
                {"workspace": workspace, "person": directory_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO identity_aliases (workspace_id, alias_person_id, person_id) "
                    "VALUES (:workspace, :alias, :person)"
                ),
                {"workspace": workspace, "alias": person, "person": directory_id},
            )
        assert await SegmentDelivery.canonical_user(workspace, directory_id) == owner
        assert await SegmentDelivery.canonical_user(workspace, person) == owner
        assert await SegmentDelivery.canonical_user(uuid4(), directory_id) is None
        assert await SegmentDelivery.canonical_user(workspace, owner) is None
        monkeypatch.setenv("SEGMENT_BROWSER_WRITE_KEY", "test-segment-key")
        schedule = AsyncMock()
        monkeypatch.setattr(queue, "schedule", schedule)
        feedback = make_event(
            workspace_id=workspace,
            event_name=EventName.FEEDBACK_SUBMITTED,
            producer="test",
            producer_event_id="feedback",
            occurred_at=datetime.now(UTC),
            environment="preview",
            user_id=directory_id,
            payload=FeedbackSubmittedPayload(sentiment="positive", rating=5),
        )
        await SegmentDelivery.enqueue_event(feedback)
        body = schedule.call_args.args[1]
        assert body["userId"] == str(owner)
        assert body["properties"]["attribution_role"] == "actor"
        assert body["properties"]["environment"] == "preview"
        await SegmentDelivery.enqueue_event(
            make_event(
                workspace_id=workspace,
                event_name=EventName.RUN_STARTED,
                producer="test",
                producer_event_id="owner",
                occurred_at=datetime.now(UTC),
                environment="test",
                run_id=uuid4(),
                user_id=directory_id,
                payload=RunStartedPayload(model_attribution_quality="unavailable"),
            )
        )
        assert schedule.call_args.args[1]["properties"]["attribution_role"] == "owner"
        async with tx() as conn:
            await conn.execute(
                text("DELETE FROM user_identity WHERE user_id = :owner"), {"owner": owner}
            )
        assert await SegmentDelivery.canonical_user(workspace, directory_id) is None

    async def test_outbox_acks_before_segment_network_and_queue_retries(
        self, analytics_db, monkeypatch, caplog
    ):
        workspace, tx = analytics_db
        monkeypatch.setenv("SEGMENT_BROWSER_WRITE_KEY", "test-segment-key")
        started = make_event(
            workspace_id=workspace,
            event_name=EventName.RUN_STARTED,
            producer="test",
            producer_event_id="start",
            occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
            environment="test",
            run_id=uuid4(),
            user_id=uuid4(),
            entry_point=EntryPoint.SLACK,
            payload=RunStartedPayload(
                configured_model_id=uuid4(), model_attribution_quality="configured"
            ),
        )
        completed = make_event(
            workspace_id=workspace,
            event_name=EventName.RUN_COMPLETED,
            producer="test",
            producer_event_id="complete",
            occurred_at=datetime(2026, 9, 2, tzinfo=UTC),
            environment="test",
            run_id=started.run_id,
            payload=RunCompletedPayload(input_tokens=10, output_tokens=20, total_tokens=30),
        )
        assert await outbox.enqueue(started)
        assert await outbox.deliver_batch() == 1
        assert await outbox.enqueue(completed)
        assert await outbox.deliver_batch() == 1
        await SegmentDelivery.enqueue_event(completed)
        async with tx() as conn:
            assert (
                await conn.scalar(
                    text("SELECT count(*) FROM job WHERE kind = :kind"), {"kind": SEGMENT_JOB}
                )
                == 2
            )
            assert (
                await conn.scalar(text("SELECT count(*) FROM outbox WHERE state = 'acknowledged'"))
                == 2
            )
            payload = await conn.scalar(
                text("SELECT payload FROM job WHERE key = :key"),
                {"key": f"segment:{workspace}:{completed.event_id}"},
            )
        assert payload["anonymousId"] == str(started.user_id)
        assert "userId" not in payload
        assert payload["properties"]["entry_point"] == "slack"
        assert payload["properties"]["configured_model_id"] == str(
            started.payload.configured_model_id
        )
        assert payload["properties"]["input_tokens"] == 10
        assert payload["properties"]["activity_kind"] == "lifecycle"
        server = SegmentServer([500, 500])
        monkeypatch.setattr(
            segment, "pooled_transport", partial(httpx2.MockTransport, server.respond)
        )
        worker = JobWorker()
        worker.register(SEGMENT_JOB)(SegmentDelivery.deliver)
        assert await worker.run_due() == 2
        async with tx() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM job WHERE attempts = 1")) == 2
            assert (
                await conn.scalar(text("SELECT count(*) FROM outbox WHERE state = 'acknowledged'"))
                == 2
            )
            assert (
                await conn.scalar(
                    text("SELECT technical_status FROM run_projection WHERE run_id = :run"),
                    {"run": started.run_id},
                )
                == "completed"
            )
        monkeypatch.setattr(queue, "schedule", AsyncMock(side_effect=RuntimeError("private error")))
        monkeypatch.setenv("ANALYTICS_OUTBOX_MAX_ATTEMPTS", "1")
        unscheduled = make_event(
            workspace_id=workspace,
            event_name=EventName.RUN_COMPLETED,
            producer="test",
            producer_event_id="unscheduled",
            occurred_at=datetime.now(UTC),
            environment="test",
            run_id=uuid4(),
            payload=RunCompletedPayload(),
        )
        assert await outbox.enqueue(unscheduled)
        assert await outbox.deliver_batch() == 1
        async with tx() as conn:
            assert (
                await conn.scalar(
                    text("SELECT state FROM outbox WHERE event_id = :id"),
                    {"id": unscheduled.event_id},
                )
                == "acknowledged"
            )
            assert (
                await conn.scalar(
                    text("SELECT technical_status FROM run_projection WHERE run_id = :run"),
                    {"run": unscheduled.run_id},
                )
                == "completed"
            )
        failure = next(
            record
            for record in caplog.records
            if record.message == "Segment lifecycle capture failed"
        )
        assert failure.error_type == "RuntimeError"
        assert failure.analytics_event_id == str(unscheduled.event_id)
        assert failure.exc_info is None
        assert "private error" not in caplog.text

    async def test_distance_only_exports_measurement_not_git_evidence(
        self, analytics_db, monkeypatch
    ):
        workspace, _ = analytics_db
        monkeypatch.setenv("SEGMENT_BROWSER_WRITE_KEY", "test-segment-key")
        schedule = AsyncMock()
        monkeypatch.setattr(queue, "schedule", schedule)
        event = make_event(
            workspace_id=workspace,
            event_name=EventName.PR_DISTANCE_MEASURED,
            producer="test",
            producer_event_id="distance",
            occurred_at=datetime.now(UTC),
            environment="test",
            pr_id=uuid4(),
            payload=PRDistanceMeasuredPayload(
                repository_full_name="private/repo",
                pr_number=23,
                distance_basis_points=4321,
                opening_base_sha="a" * 40,
                opening_head_sha="b" * 40,
                final_base_sha="c" * 40,
                final_head_sha="d" * 40,
                algorithm_revision="myers-line-v1",
                opening_evidence_ref=uuid4(),
                final_evidence_ref=uuid4(),
            ),
        )
        await SegmentDelivery.enqueue_event(event)
        body = schedule.call_args.args[1]
        assert body["properties"]["distance_basis_points"] == 4321
        serialized = json.dumps(body)
        assert "private/repo" not in serialized
        assert "sha" not in serialized
        assert "evidence" not in serialized
        assert "pr_number" not in serialized


class TestSegmentActivity:
    async def test_provenance_carried_reports_and_tool_content_are_minimized(self, monkeypatch):
        monkeypatch.setenv("SEGMENT_BROWSER_WRITE_KEY", "test-segment-key")
        monkeypatch.setenv("DD_ENV", "test")
        workspace, owner, actor = uuid4(), uuid4(), uuid4()
        monkeypatch.setattr(database, "_WORKSPACE_ID", workspace)
        schedule = AsyncMock()
        monkeypatch.setattr(queue, "schedule", schedule)
        monkeypatch.setattr(User, "get", AsyncMock(side_effect=lambda key: User(id=key)))
        run = RemoteRun(
            thread_id="private-thread",
            assistant_id="agent",
            configurable={
                "user_id": str(owner),
                "invocation_id": "current-invocation",
                "run_id": "current-runtime",
                "invocation_started_at": "2026-09-01T00:00:00+00:00",
            },
            settings=RunSettings(source="dashboard"),
        )
        activity = SegmentActivity(run)
        human = HumanMessage(
            content="private prompt",
            additional_kwargs=Provenance(
                kind="human", sender=f"user:{actor}", surface="slack"
            ).stamp(),
        )
        context = HumanMessage(
            content="private guidance",
            additional_kwargs=Provenance.harness("instructions").stamp(),
        )
        request = RunStarted(
            run_id="earlier-runtime",
            invocation_id="earlier-invocation",
            humans=[message_to_dict(human), message_to_dict(context)],
        )
        await activity.request_started(request, carried=True)
        first = schedule.call_args.args[1]
        assert first["userId"] == str(actor)
        assert first["properties"]["attribution_role"] == "actor"
        assert first["properties"]["source"] == "slack"
        assert first["properties"]["environment"] == "test"
        assert first["properties"]["trigger_kind"] == "human"
        assert first["properties"]["run_id"] == str(identity.opaque_id("run", "earlier-invocation"))
        assert first["timestamp"] != run.configurable["invocation_started_at"]
        await activity.request_started(request, carried=True)
        assert schedule.call_args.args[1]["messageId"] == first["messageId"]
        system = HumanMessage(
            content="private background notice",
            additional_kwargs=Provenance(
                kind="system", sender="system:event", surface="automation"
            ).stamp(),
        )
        await activity.request_started(
            request.model_copy(update={"humans": [message_to_dict(system)]}),
            carried=True,
        )
        carried = schedule.call_args.args[1]
        assert "userId" not in carried
        assert carried["properties"]["activity_kind"] == "automatic_work"
        assert carried["properties"]["attribution_role"] == "unknown"
        await activity.request_started(
            RunStarted(
                run_id="current-runtime",
                invocation_id="current-invocation",
                humans=[message_to_dict(system)],
            ),
            carried=False,
        )
        current = schedule.call_args.args[1]
        assert current["userId"] == str(owner)
        assert current["properties"]["attribution_role"] == "owner"
        assert datetime.fromisoformat(current["timestamp"]) == datetime(2026, 9, 1, tzinfo=UTC)
        await activity.request_started(RunStarted(run_id="unproven-runtime"), carried=False)
        unproven = schedule.call_args.args[1]
        assert "userId" not in unproven
        assert unproven["properties"]["source"] == "unknown"
        assert unproven["timestamp"] != run.configurable["invocation_started_at"]
        assert unproven["properties"]["run_id"] == str(
            identity.opaque_id("run", "unproven-runtime")
        )
        result = ToolResultReport(
            run_id="earlier-runtime",
            tool_call_id="private-call",
            name="mcp_customer_secret",
            arguments={"prompt": "private args"},
            result={
                "type": "tool",
                "data": {"content": "private result", "tool_call_id": "private-call"},
            },
        )
        await activity.tool_executed(result, is_error=True)
        tool = schedule.call_args.args[1]
        assert "userId" not in tool
        assert tool["properties"]["tool"] == "external"
        assert tool["properties"]["is_error"] is True
        assert tool["properties"]["activity_kind"] == "agent_work"
        assert tool["properties"]["runtime_run_id"] == first["properties"]["runtime_run_id"]
        await activity.tool_executed(result, is_error=True)
        assert schedule.call_args.args[1]["messageId"] == tool["messageId"]
        assert "private" not in json.dumps(tool)
        assert "mcp_customer_secret" not in json.dumps(tool)

    async def test_capture_failure_does_not_fail_the_report(self, monkeypatch, caplog):
        monkeypatch.setenv("SEGMENT_BROWSER_WRITE_KEY", "test-segment-key")
        monkeypatch.setattr(database, "_WORKSPACE_ID", uuid4())
        monkeypatch.setattr(queue, "schedule", AsyncMock(side_effect=RuntimeError("private error")))
        activity = SegmentActivity(
            RemoteRun(thread_id="thread", assistant_id="agent", configurable={})
        )
        await activity.request_started(
            RunStarted(run_id="run", invocation_id="invocation"), carried=True
        )
        assert "Segment request activity capture failed" in caplog.text
        assert "private error" not in caplog.text
