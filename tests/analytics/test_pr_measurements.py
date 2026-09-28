"""Verified distance survives replay without becoming a lifecycle transition."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from itertools import permutations
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.analytics import ingestion, outbox, queries, retention, revisions, usage
from agent.analytics.events import (
    EventEnvelope,
    EventName,
    PRDistanceMeasuredPayload,
    PROpenedPayload,
    PRStatePayload,
    event_uuid,
    make_event,
    subject_uuid,
)
from agent.analytics.measurements import PRDistanceConflictError, restore_pr_distance
from agent.analytics.revisions import PRRevisionConflictError
from agent.database import postgres

Transaction = Callable[[], AbstractAsyncContextManager[AsyncConnection]]
Database = tuple[UUID, Transaction]
NOW = datetime.now(UTC) - timedelta(days=5)


def measurement(**changes: object) -> PRDistanceMeasuredPayload:
    return PRDistanceMeasuredPayload.model_validate(
        {
            "repository_full_name": "owner/repo",
            "pr_number": 1,
            "distance_basis_points": 0,
            "algorithm_revision": "myers-line-v1",
            "opening_base_sha": "a" * 40,
            "opening_head_sha": "b" * 40,
            "final_base_sha": "c" * 40,
            "final_head_sha": "d" * 40,
            "opening_evidence_ref": UUID(int=1),
            "final_evidence_ref": UUID(int=2),
            **changes,
        }
    )


def events(workspace: UUID, *, number: int = 1) -> tuple[EventEnvelope, ...]:
    identities = {
        "pr_id": subject_uuid(workspace, "pr", f"owner/repo#{number}"),
        "repository_id": subject_uuid(workspace, "repository", "owner/repo"),
    }
    model = subject_uuid(workspace, "model", "test-model")
    return tuple(
        make_event(
            workspace_id=workspace,
            event_name=name,
            producer="test",
            producer_event_id=f"{number}:{name}",
            occurred_at=NOW + timedelta(days=index),
            payload=payload,
            environment="test",
            **identities,
        )
        for index, (name, payload) in enumerate(
            [
                (
                    EventName.PR_OPENED,
                    PROpenedPayload(
                        originating_model_id=model, model_attribution_quality="configured"
                    ),
                ),
                (EventName.PR_MERGED, PRStatePayload()),
                (EventName.PR_DISTANCE_MEASURED, measurement(pr_number=number)),
            ]
        )
    )


def revise(item: EventEnvelope, **changes: object) -> EventEnvelope:
    data = {**item.model_dump(), **changes}
    data["event_id"] = event_uuid(
        item.workspace_id,
        item.producer,
        str(data["producer_event_id"]),
        EventName(data["event_name"]),
    )
    return EventEnvelope.model_validate(data)


async def projection() -> dict[str, object]:
    async with postgres.connection() as conn:
        return dict((await conn.execute(text("SELECT * FROM pr_projection"))).mappings().one())


async def ingestion_state() -> dict[str, list[dict[str, object]]]:
    async with postgres.connection() as conn:
        return {
            table: [
                dict(row)
                for row in (
                    await conn.execute(text(f"SELECT * FROM {table} ORDER BY to_jsonb({table})"))
                ).mappings()
            ]
            for table in (
                "event_ids",
                "pr_distance_measurements",
                "events",
                "ingestion_receipts",
                "additive_event_projection",
                "pr_projection",
                "dirty_summary_partitions",
                "deployment_metadata",
            )
        }


@pytest.mark.parametrize("order", list(permutations(range(3))) + ["concurrent"])
async def test_measurement_delivery_orders_and_actual_reconciliation(
    analytics_db: Database, order: tuple[int, ...] | str
) -> None:
    workspace, transaction = analytics_db
    opened, merged, measured = items = events(workspace)
    if order == "concurrent":
        await asyncio.gather(*(ingestion.ingest(item) for item in items))
    else:
        assert isinstance(order, tuple)
        for index in order:
            assert await ingestion.ingest(items[index])
    before = await projection()
    assert before["current_state"] == "merged"
    assert before["distance_basis_points"] == 0
    assert before["outcome_at"] == merged.occurred_at
    assert before["latest_transition_at"] == merged.occurred_at
    assert before["source_version"] is None
    async with transaction() as conn:
        await ingestion._reconcile_outcomes(conn, opened)
    for item in items:
        assert not await ingestion.ingest(item)
    after = await projection()
    assert {k: v for k, v in after.items() if k != "updated_at"} == {
        k: v for k, v in before.items() if k != "updated_at"
    }
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM pr_distance_measurements")) == 1
        assert (
            await conn.scalar(text("SELECT sum(event_count) FROM additive_event_projection")) == 3
        )
        assert await conn.scalar(text("SELECT count(*) FROM pr_run_link_projection")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM task_projection")) == 0


async def test_later_measurement_does_not_override_lifecycle_and_zero_is_a_sample(
    analytics_db: Database,
) -> None:
    workspace, transaction = analytics_db
    opened, merged, measured = events(workspace)
    for item in (opened, merged):
        await ingestion.ingest(item)
    async with transaction() as conn:
        await conn.execute(
            text("UPDATE deployment_metadata SET reporting_cutover_at = :start"),
            {"start": NOW - timedelta(days=1)},
        )
    before = await queries.pr_merge_rate_by_model(period="all", admin=True)
    await ingestion.ingest(measured)
    after = await queries.pr_merge_rate_by_model(period="all", admin=True)
    assert before != after
    for report, samples, median in ((before, 0, None), (after, 1, 0)):
        cohort = report["cohorts"][0]
        assert cohort["distance_sample_size"] == samples
        assert cohort["median_distance_basis_points"] == median
        assert cohort["merged"] == 1
        assert cohort["cohort_size"] == 1
        assert cohort["decided_denominator"] == 1
    for version, name, state in (
        (10, EventName.PR_REOPENED, "open"),
        (11, EventName.PR_CLOSED_WITHOUT_MERGE, "closed_without_merge"),
    ):
        transition = revise(
            merged,
            event_name=name,
            producer_event_id=str(uuid4()),
            source_version=version,
            occurred_at=NOW + timedelta(days=3),
        )
        await ingestion.ingest(transition)
        await ingestion.ingest(revise(measured, producer_event_id=str(uuid4())))
        await ingestion.ingest(revise(merged, producer_event_id=str(uuid4())))
        current = await projection()
        assert current["current_state"] == state
        assert current["distance_basis_points"] is None
        assert current["source_version"] == version
        assert current["latest_transition_at"] == transition.occurred_at
        report = await queries.pr_merge_rate_by_model(period="all", admin=True)
        assert report["cohorts"][0]["distance_sample_size"] == 0
    await ingestion.ingest(revise(merged, producer_event_id="new-merge", source_version=12))
    assert (await projection())["distance_basis_points"] == 0


@pytest.mark.parametrize(
    "changed",
    [
        {"distance_basis_points": 1},
        {"opening_head_sha": "e" * 40},
        {"final_base_sha": "f" * 40},
        {"opening_evidence_ref": UUID(int=3)},
    ],
)
async def test_conflicts_roll_back_and_duplicates_are_idempotent(
    analytics_db: Database, changed: dict[str, object]
) -> None:
    workspace, _ = analytics_db
    opened, merged, measured = events(workspace)
    for item in (opened, merged, measured):
        await ingestion.ingest(item)
    before = await ingestion_state()
    assert not await ingestion.ingest(revise(measured, producer_event_id="duplicate"))
    assert await ingestion_state() == before
    for producer_id in (measured.producer_event_id, "conflicting-event"):
        with pytest.raises(PRDistanceConflictError):
            await ingestion.ingest(
                revise(measured, producer_event_id=producer_id, payload=measurement(**changed))
            )
        assert await ingestion_state() == before


@pytest.mark.parametrize("retained", [False, True])
@pytest.mark.parametrize("existing_target", [False, True])
async def test_reused_event_identity_cannot_mutate_another_pr(
    analytics_db: Database, retained: bool, existing_target: bool
) -> None:
    workspace, transaction = analytics_db
    measured = events(workspace)[2]
    opened, merged, target = events(workspace, number=2)
    for item in (measured, opened, merged, *([target] if existing_target else [])):
        assert await ingestion.ingest(
            revise(item, occurred_at=item.occurred_at - timedelta(days=1000)) if retained else item
        )
    if retained:
        await retention.enforce_retention()
    async with transaction() as conn:
        await conn.execute(text("UPDATE pr_projection SET distance_basis_points = NULL"))
        before = {
            table: (await conn.execute(text(f"SELECT * FROM {table}"))).mappings().all()
            for table in (
                "pr_distance_measurements",
                "pr_projection",
                "events",
                "event_ids",
                "ingestion_receipts",
                "additive_event_projection",
                "deployment_metadata",
            )
        }
    reused = revise(target, producer_event_id=measured.producer_event_id)
    assert reused.event_id == measured.event_id
    with pytest.raises(PRDistanceConflictError):
        await ingestion.ingest(reused)
    async with transaction() as conn:
        for table, rows in before.items():
            assert (await conn.execute(text(f"SELECT * FROM {table}"))).mappings().all() == rows


async def test_claimed_event_id_without_measurement_cannot_create_evidence(
    analytics_db: Database,
) -> None:
    workspace, transaction = analytics_db
    measured = events(workspace)[2]
    async with transaction() as conn:
        await conn.execute(
            text("INSERT INTO event_ids(event_id, occurred_at) VALUES (:event_id, :occurred_at)"),
            {"event_id": measured.event_id, "occurred_at": measured.occurred_at},
        )
    with pytest.raises(PRDistanceConflictError):
        await ingestion.ingest(measured)
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM pr_distance_measurements")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM events")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM event_ids")) == 1


async def test_concurrent_reused_event_identity_has_one_durable_winner(
    analytics_db: Database,
) -> None:
    workspace, transaction = analytics_db
    measured = events(workspace)[2]
    reused = revise(events(workspace, number=2)[2], producer_event_id=measured.producer_event_id)
    results = await asyncio.gather(
        ingestion.ingest(measured), ingestion.ingest(reused), return_exceptions=True
    )
    assert sum(result is True for result in results) == 1
    assert sum(isinstance(result, PRDistanceConflictError) for result in results) == 1
    winner = measured if results[0] is True else reused
    async with transaction() as conn:
        assert (
            await conn.execute(text("SELECT pr_id FROM pr_distance_measurements"))
        ).scalar_one() == winner.pr_id
        assert await conn.scalar(text("SELECT count(*) FROM events")) == 1
        assert await conn.scalar(text("SELECT count(*) FROM event_ids")) == 1


def test_measurement_envelope_rejects_nonmeasurement_event_id() -> None:
    opened, _, measured = events(uuid4())
    with pytest.raises(ValidationError, match="deterministic event identity"):
        EventEnvelope.model_validate({**measured.model_dump(), "event_id": opened.event_id})


async def test_concurrent_conflicts_have_one_durable_winner(analytics_db: Database) -> None:
    workspace, transaction = analytics_db
    measured = events(workspace)[2]
    conflicting = revise(
        measured, producer_event_id="conflict", payload=measurement(distance_basis_points=10000)
    )
    results = await asyncio.gather(
        ingestion.ingest(measured), ingestion.ingest(conflicting), return_exceptions=True
    )
    assert sum(result is True for result in results) == 1
    assert sum(isinstance(result, PRDistanceConflictError) for result in results) == 1
    winner = measured if results[0] is True else conflicting
    assert not await ingestion.ingest(winner)
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM pr_distance_measurements")) == 1
        assert await conn.scalar(text("SELECT count(*) FROM events")) == 1
        assert await conn.scalar(text("SELECT count(*) FROM pr_projection")) == 0


@pytest.mark.parametrize("name", [None, EventName.PR_REOPENED, EventName.PR_CLOSED_WITHOUT_MERGE])
async def test_first_measurement_cannot_turn_nonmerged_pr_into_a_merge(
    analytics_db: Database, name: EventName | None
) -> None:
    workspace, _ = analytics_db
    opened, merged, measured = events(workspace)
    await ingestion.ingest(opened)
    if name is not None:
        await ingestion.ingest(revise(merged, event_name=name))
    before = await projection()
    await ingestion.ingest(measured)
    assert await projection() == before
    assert before["distance_basis_points"] is None


@pytest.mark.parametrize("legacy_distance", [None, 0, 250])
async def test_measurement_and_projection_roll_back_together(
    analytics_db: Database, monkeypatch: pytest.MonkeyPatch, legacy_distance: int | None
) -> None:
    workspace, _ = analytics_db
    opened, merged, measured = events(workspace)
    for item in (
        opened,
        revise(merged, payload=PRStatePayload(distance_basis_points=legacy_distance)),
    ):
        await ingestion.ingest(item)
    measured = revise(measured, payload=measurement(distance_basis_points=legacy_distance or 0))
    before = await ingestion_state()
    project = ingestion._project

    async def fail_after_project(conn: AsyncConnection, event: EventEnvelope) -> None:
        await project(conn, event)
        raise RuntimeError("injected failure")

    with monkeypatch.context() as patch:
        patch.setattr(ingestion, "_project", fail_after_project)
        with pytest.raises(RuntimeError, match="injected failure"):
            await ingestion.ingest(measured)
    assert await ingestion_state() == before
    assert await ingestion.ingest(measured)
    assert (await projection())["distance_basis_points"] == (legacy_distance or 0)


@pytest.mark.parametrize("duplicate_id", [None, "late-duplicate"])
async def test_retention_before_opening_and_distance_cache_recovery(
    analytics_db: Database, duplicate_id: str | None
) -> None:
    workspace, transaction = analytics_db
    opened, merged, measured = events(workspace)
    measured = revise(measured, occurred_at=NOW - timedelta(days=1000))
    await outbox.enqueue(measured)
    assert await outbox.deliver_batch() == 1
    async with transaction() as conn:
        await conn.execute(text("UPDATE outbox SET acknowledged_at = now() - interval '100 days'"))
    await retention.enforce_retention()
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM events")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM outbox")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM pr_distance_measurements")) == 1
    await ingestion.ingest(merged)
    await ingestion.ingest(opened)
    before = await projection()
    assert before["distance_basis_points"] == 0
    async with transaction() as conn:
        await conn.execute(text("DELETE FROM events"))
        await conn.execute(text("UPDATE pr_projection SET distance_basis_points = NULL"))
        assert measured.pr_id is not None
        await restore_pr_distance(conn, workspace_id=workspace, pr_id=measured.pr_id)
    assert await projection() == before
    async with transaction() as conn:
        await conn.execute(text("UPDATE pr_projection SET distance_basis_points = NULL"))
    assert not await ingestion.ingest(
        revise(measured, producer_event_id=duplicate_id) if duplicate_id else measured
    )
    assert await projection() == before
    with pytest.raises(PRDistanceConflictError):
        await ingestion.ingest(revise(measured, payload=measurement(distance_basis_points=500)))
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM events")) == 0


@pytest.mark.parametrize("legacy_distance,new_distance", [(250, 0), (0, 250), (250, 500)])
async def test_legacy_distance_conflict_rolls_back_ingestion(
    analytics_db: Database, legacy_distance: int, new_distance: int
) -> None:
    workspace, _ = analytics_db
    opened, merged, measured = events(workspace)
    for item in (
        opened,
        revise(merged, payload=PRStatePayload(distance_basis_points=legacy_distance)),
    ):
        assert await ingestion.ingest(item)
    before = await ingestion_state()
    assert before["pr_distance_measurements"] == []
    with pytest.raises(PRDistanceConflictError, match="legacy"):
        await ingestion.ingest(
            revise(measured, payload=measurement(distance_basis_points=new_distance))
        )
    assert await ingestion_state() == before


@pytest.mark.parametrize("legacy_distance", [0, 250])
async def test_matching_legacy_distance_promotes_durable_evidence(
    analytics_db: Database, legacy_distance: int
) -> None:
    workspace, transaction = analytics_db
    opened, merged, measured = events(workspace)
    for item in (
        opened,
        revise(merged, payload=PRStatePayload(distance_basis_points=legacy_distance)),
    ):
        assert await ingestion.ingest(item)
    before = await projection()
    measured = revise(measured, payload=measurement(distance_basis_points=legacy_distance))
    assert await ingestion.ingest(measured)
    assert not await ingestion.ingest(measured)
    assert await projection() == before
    async with transaction() as conn:
        row = (await conn.execute(text("SELECT * FROM pr_distance_measurements"))).mappings().one()
        assert row["event_id"] == measured.event_id
        assert row["measurement"] == measured.payload.model_dump(mode="json")
        assert await conn.scalar(text("SELECT count(*) FROM events")) == 3
        assert await conn.scalar(text("SELECT count(*) FROM event_ids")) == 3
        assert await conn.scalar(text("SELECT count(*) FROM ingestion_receipts")) == 3
        assert (
            await conn.scalar(text("SELECT sum(event_count) FROM additive_event_projection")) == 3
        )


async def test_legacy_null_merge_replay_preserves_distance(analytics_db: Database) -> None:
    workspace, transaction = analytics_db
    opened, merged, _ = events(workspace)
    for item in (opened, revise(merged, payload=PRStatePayload(distance_basis_points=250))):
        await ingestion.ingest(item)
    await ingestion.ingest(revise(merged, producer_event_id="null-replay"))
    async with transaction() as conn:
        await ingestion._reconcile_outcomes(conn, opened)
        assert await conn.scalar(text("SELECT count(*) FROM pr_distance_measurements")) == 0
    assert (await projection())["distance_basis_points"] == 250


@pytest.mark.parametrize(
    "changes",
    [
        {"distance_basis_points": -1},
        {"distance_basis_points": 10001},
        {"distance_basis_points": True},
        {"distance_basis_points": None},
        {"distance_basis_points": 1.5},
        {"opening_head_sha": "unknown"},
        {"final_base_sha": ""},
        {"algorithm_revision": "unreviewed"},
        {"opening_evidence_ref": "https://example.com/raw-trace?token=secret"},
        {"diff": "raw content"},
    ],
)
def test_measurement_rejects_invalid_or_sensitive_payload(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        measurement(**changes)


@pytest.mark.parametrize(
    "changes", [{"pr_id": UUID(int=9)}, {"repository_id": UUID(int=9)}, {"source_version": 99}]
)
async def test_measurement_requires_matching_identity_without_lifecycle_version(
    analytics_db: Database, changes: dict[str, object]
) -> None:
    workspace, transaction = analytics_db
    measured = events(workspace)[2]
    with pytest.raises(ValueError, match="identity"):
        await ingestion.ingest(revise(measured, **changes))
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM events")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM pr_distance_measurements")) == 0


def webhook(
    action: str, *, merged: bool = False, opening_head_sha: str = "b" * 40
) -> dict[str, object]:
    return {
        "repository": {"owner": {"login": "Owner"}, "name": "Repo"},
        "action": action,
        "pull_request": {
            "number": 1,
            "merged": merged,
            "created_at": (NOW - timedelta(days=1)).isoformat(),
            "merged_at": NOW.isoformat() if merged else None,
            "updated_at": NOW.isoformat(),
            "base": {"sha": ("c" if merged else "a") * 40},
            "head": {"sha": "d" * 40 if merged else opening_head_sha},
        },
    }


@pytest.mark.parametrize("result", [0, None, TimeoutError("comparison timed out")])
async def test_capture_precedes_comparison_and_retry_uses_retained_revisions(
    analytics_db: Database, monkeypatch: pytest.MonkeyPatch, result: int | None | Exception
) -> None:
    _, transaction = analytics_db
    comparison = (
        AsyncMock(side_effect=result)
        if isinstance(result, Exception)
        else AsyncMock(return_value=result)
    )
    monkeypatch.setattr(revisions.distance, "post_open_distance_basis_points", comparison)
    await usage.update_agent_pr_usage_from_webhook(webhook("opened"), delivery_id="opening")
    await usage.update_agent_pr_usage_from_webhook(
        webhook("closed", merged=True), delivery_id="final"
    )
    async with transaction() as conn:
        retained = (
            (
                await conn.execute(
                    text(
                        "SELECT endpoint_kind, base_sha, head_sha, source_id FROM pr_revision_evidence ORDER BY endpoint_kind"
                    )
                )
            )
            .mappings()
            .all()
        )
    assert [
        (row["endpoint_kind"], row["base_sha"], row["head_sha"], row["source_id"])
        for row in retained
    ] == [
        ("final", "c" * 40, "d" * 40, "final"),
        ("opening", "a" * 40, "b" * 40, "opening"),
    ]
    if isinstance(result, int):
        assert await revisions.retry_pr_distance(owner="owner", repo="repo", number=1) == result
        distance_events = [
            EventEnvelope.model_validate(body)
            for body in (await _outbox_bodies())
            if EventEnvelope.model_validate(body).event_name == EventName.PR_DISTANCE_MEASURED
        ]
        assert len(distance_events) == 1
        assert isinstance(distance_events[0].payload, PRDistanceMeasuredPayload)
        assert distance_events[0].payload.distance_basis_points == result
        assert (
            len(
                [
                    EventEnvelope.model_validate(body)
                    for body in (await _outbox_bodies())
                    if EventEnvelope.model_validate(body).event_name == EventName.PR_MERGED
                ]
            )
            == 1
        )
        comparison.assert_awaited_with(
            owner="owner",
            repo="repo",
            opening_base_sha="a" * 40,
            opening_head_sha="b" * 40,
            final_base_sha="c" * 40,
            final_head_sha="d" * 40,
        )
    else:
        assert not [
            EventEnvelope.model_validate(body)
            for body in (await _outbox_bodies())
            if EventEnvelope.model_validate(body).event_name == EventName.PR_DISTANCE_MEASURED
        ]


async def _outbox_bodies() -> list[dict[str, object]]:
    async with postgres.connection() as conn:
        return list((await conn.execute(text("SELECT event_body FROM outbox"))).scalars().all())


async def test_duplicate_conflict_out_of_order_and_non_authoritative_actions(
    analytics_db: Database,
) -> None:
    _, transaction = analytics_db
    await usage.update_agent_pr_usage_from_webhook(
        webhook("closed", merged=True), delivery_id="final"
    )
    await usage.update_agent_pr_usage_from_webhook(webhook("ready_for_review"), delivery_id="ready")
    await usage.update_agent_pr_usage_from_webhook(webhook("opened"), delivery_id="opening")
    await usage.update_agent_pr_usage_from_webhook(
        webhook("opened"), delivery_id="opening-duplicate"
    )
    with pytest.raises(PRRevisionConflictError):
        await revisions.capture_pr_revision(
            owner="owner",
            repo="repo",
            number=1,
            endpoint_kind="opening",
            base_sha="a" * 40,
            head_sha="e" * 40,
            endpoint_at=NOW - timedelta(days=1),
            source_kind="webhook",
            source_id="conflict",
        )
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM pr_revision_evidence")) == 2


async def test_retry_ignores_current_pull_request_revision_columns(
    analytics_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, transaction = analytics_db
    await usage.update_agent_pr_usage_from_webhook(webhook("opened"), delivery_id="opening")
    await usage.update_agent_pr_usage_from_webhook(
        webhook("closed", merged=True), delivery_id="final"
    )
    async with transaction() as conn:
        await conn.execute(
            text(
                "INSERT INTO repository (id, key, full_name) VALUES (:id, 'owner/repo', 'owner/repo')"
            ),
            {"id": uuid4()},
        )
        repository_id = await conn.scalar(
            text("SELECT id FROM repository WHERE key = 'owner/repo'")
        )
        await conn.execute(
            text(
                "INSERT INTO pull_request (id, repository_id, number, owner, repo, opening_base_sha, opening_head_sha) "
                "VALUES (:id, :repository_id, 1, 'owner', 'repo', :base, :head)"
            ),
            {"id": uuid4(), "repository_id": repository_id, "base": "e" * 40, "head": "f" * 40},
        )
    comparison = AsyncMock(return_value=250)
    monkeypatch.setattr(revisions.distance, "post_open_distance_basis_points", comparison)
    assert await revisions.retry_pr_distance(owner="owner", repo="repo", number=1) == 250
    comparison.assert_awaited_once_with(
        owner="owner",
        repo="repo",
        opening_base_sha="a" * 40,
        opening_head_sha="b" * 40,
        final_base_sha="c" * 40,
        final_head_sha="d" * 40,
    )


async def test_retained_revision_evidence_survives_raw_retention(
    analytics_db: Database,
) -> None:
    _, transaction = analytics_db
    await usage.update_agent_pr_usage_from_webhook(webhook("opened"), delivery_id="opening")
    async with transaction() as conn:
        await conn.execute(
            text("UPDATE pr_revision_evidence SET captured_at = now() - interval '100 years'")
        )
    await retention.enforce_retention()
    async with transaction() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM pr_revision_evidence")) == 1
