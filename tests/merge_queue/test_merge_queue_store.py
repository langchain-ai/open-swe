"""Storage regressions use the same migrated PostgreSQL database as the application."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from openswe.database import postgres
from openswe.github.pull_requests import PullRequest
from openswe.github.repositories import Repository
from openswe.merge_queue.models import (
    Attempt,
    Batch,
    BatchMember,
    Decision,
    Evidence,
    Lease,
    MergeRequest,
)
from openswe.merge_queue.store import LeaseLost, MergeQueue
from openswe.users.models import User
from openswe.workspaces.rows import WorkspaceRepositoryRow, WorkspaceRow
from openswe.workspaces.store import WORKSPACES

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture
async def queue() -> MergeQueue:
    repository = await Repository(full_name="acme/widgets").save()
    async with postgres.session() as session:
        workspace = WorkspaceRow(slug="widgets", name="Widgets")
        session.add(workspace)
        await session.flush()
        session.add(WorkspaceRepositoryRow(repository_id=repository.id, workspace_id=workspace.id))
    return await MergeQueue.register(workspace.id, repository.id, "main")


async def request(queue: MergeQueue, number: int) -> MergeRequest:
    pull = await PullRequest(owner="acme", repo="widgets", number=number, base_ref="main").save()
    return await queue.request_merge(pull.id, f"{number:040x}", f"pr-{number}")


async def claim(queue: MergeQueue) -> Lease:
    lease = await queue.claim(uuid7(), timedelta(minutes=5))
    assert lease is not None
    return lease


async def batch(queue: MergeQueue, lease: Lease, key: str = "batch") -> Batch:
    pending = await request(queue, 1)
    await queue.admit(pending.id, pending.head_sha, lease)
    return await queue.create_batch(
        [pending.id],
        request_key=key,
        base_sha="a" * 40,
        policy={"version": 1},
        deadline=datetime.now(UTC) + timedelta(hours=1),
        max_attempts=1,
        max_plans=1,
        lease=lease,
    )


async def test_concurrent_requests_and_restarts_preserve_intent(queue: MergeQueue) -> None:
    async with postgres.session() as session:
        user = User(display_name="Developer")
        session.add(user)
    registrations = await asyncio.gather(
        *(
            MergeQueue.register(queue.workspace_id, queue.repository_id, "refs/heads/main")
            for _ in range(2)
        )
    )
    assert {registered.id for registered in registrations} == {queue.id}
    pull = await PullRequest(owner="acme", repo="widgets", number=1, base_ref="main").save()
    first, duplicate = await asyncio.gather(
        *(queue.request_merge(pull.id, "a" * 40, "submit", actor=user.id) for _ in range(2))
    )
    assert first.id == duplicate.id
    assert first.state == "waiting_for_review"
    assert first.created_by == first.updated_by == user.id
    another = await PullRequest(owner="acme", repo="widgets", number=2, base_ref="main").save()
    with pytest.raises(ValueError, match="different input"):
        await queue.request_merge(another.id, "b" * 40, "submit")
    with pytest.raises(ValueError, match="already has an active"):
        await queue.request_merge(pull.id, "a" * 40, "another-submit")

    await postgres.close()
    restored = await MergeQueue.register(queue.workspace_id, queue.repository_id, "main")
    assert [row.id for row in await restored.requests()] == [first.id]
    await restored.withdraw(first.id, actor=user.id)
    retry = await restored.request_merge(pull.id, "a" * 40, "submit")
    assert retry.id == first.id and retry.state == "withdrawn"
    assert retry.updated_by == user.id
    assert retry.updated_at > retry.created_at
    assert await restored.requests() == []
    assert (await restored.request_merge(pull.id, "b" * 40, "new-intent")).id != first.id


async def test_workspace_scoping_and_rebinding_cannot_create_a_second_owner(
    queue: MergeQueue,
) -> None:
    async with postgres.session() as session:
        other = WorkspaceRow(slug="other", name="Other")
        session.add(other)
    with pytest.raises(ValueError, match="not bound"):
        await MergeQueue.register(other.id, queue.repository_id, "main")
    forged = MergeQueue(
        id=queue.id, workspace_id=other.id, repository_id=queue.repository_id, target_branch="main"
    )
    with pytest.raises(ValueError, match="not bound"):
        await forged.requests()
    foreign = await PullRequest(owner="other", repo="repo", number=1, base_ref="main").save()
    with pytest.raises(ValueError, match="does not target"):
        await queue.request_merge(foreign.id, "a" * 40, "foreign")
    async with postgres.session() as session:
        await session.execute(
            update(WorkspaceRepositoryRow)
            .where(
                WorkspaceRepositoryRow.repository_id == queue.repository_id,
            )
            .values(workspace_id=other.id)
        )
    with pytest.raises(ValueError, match="not bound"):
        await queue.requests()
    with pytest.raises(ValueError, match="explicit handoff"):
        await MergeQueue.register(other.id, queue.repository_id, "main")
    with pytest.raises(ValueError, match="Queue not found"):
        await forged.claim(uuid7(), timedelta(minutes=1))


async def test_fencing_rejects_old_owners_even_when_the_owner_id_is_reused(
    queue: MergeQueue,
) -> None:
    owner = uuid7()
    first, contender = await asyncio.gather(
        *(queue.claim(owner, timedelta(minutes=1)) for _ in range(2))
    )
    lease = first or contender
    assert lease is not None and (first is None) != (contender is None)
    async with postgres.session() as session:
        await session.execute(
            update(MergeQueue)
            .where(MergeQueue.id == queue.id)
            .values(
                lease_until=func.clock_timestamp() - timedelta(seconds=1),
            )
        )
    with pytest.raises(LeaseLost):
        await queue.renew(lease, timedelta(minutes=1))
    replacement = await queue.claim(owner, timedelta(minutes=1))
    assert replacement is not None and replacement.fence > lease.fence
    pending = await request(queue, 1)
    with pytest.raises(LeaseLost):
        await queue.admit(pending.id, pending.head_sha, lease)
    with pytest.raises(LeaseLost):
        await queue.release(lease)
    await queue.admit(pending.id, pending.head_sha, replacement)
    await queue.release(replacement)
    next_lease = await claim(queue)
    assert next_lease.fence > replacement.fence


async def test_batch_order_is_atomic_and_withdrawal_keeps_the_tested_snapshot(
    queue: MergeQueue,
) -> None:
    lease = await claim(queue)
    one, two = await request(queue, 1), await request(queue, 2)
    for pending in (one, two):
        await queue.admit(pending.id, pending.head_sha, lease)
    await queue.set_priority(two.id, 10)
    assert [row.id for row in await queue.requests()] == [two.id, one.id]
    deadline = datetime.now(UTC) + timedelta(hours=1)
    created, duplicate = await asyncio.gather(
        *(
            queue.create_batch(
                [two.id, one.id],
                request_key="batch",
                base_sha="a" * 40,
                policy={"version": 1},
                deadline=deadline,
                max_attempts=2,
                max_plans=1,
                lease=lease,
            )
            for _ in range(2)
        )
    )
    assert created.id == duplicate.id
    with pytest.raises(ValueError, match="unbatched"):
        await queue.set_priority(one.id, 100)
    third = await request(queue, 3)
    await queue.admit(third.id, third.head_sha, lease)
    with pytest.raises(ValueError, match="active batch"):
        await queue.create_batch(
            [third.id],
            request_key="other-batch",
            base_sha="b" * 40,
            policy={},
            deadline=deadline,
            max_attempts=1,
            max_plans=0,
            lease=lease,
        )
    attempt = await queue.start_attempt(created.id, "run", deadline, lease)
    await queue.withdraw(two.id)
    history = await queue.history(created.id)
    assert [(member.request_id, member.head_sha) for member in history.members] == [
        (two.id, two.head_sha),
        (one.id, one.head_sha),
    ]
    assert history.batch.state == "invalidated"
    assert history.attempts[0].id == attempt.id and history.attempts[0].state == "cancelled"
    assert {row.id: row.state for row in await queue.requests()} == {
        one.id: "queued",
        third.id: "queued",
    }
    with pytest.raises(ValueError, match="different result"):
        await queue.finish_attempt(attempt.id, "succeeded", lease)


async def test_attempts_plans_and_evidence_are_idempotent_and_budgets_survive_restart(
    queue: MergeQueue,
) -> None:
    lease = await claim(queue)
    created = await batch(queue, lease)
    deadline = created.deadline - timedelta(minutes=1)
    attempt = await queue.start_attempt(created.id, "run", deadline, lease)
    assert (await queue.start_attempt(created.id, "run", deadline, lease)).id == attempt.id
    with pytest.raises(ValueError, match="running attempt"):
        await queue.start_attempt(created.id, "other-run", deadline, lease)
    evidence = await queue.record_evidence(attempt.id, "report", {"artifact_id": 10}, lease)
    assert (
        await queue.record_evidence(attempt.id, "report", {"artifact_id": 10}, lease)
    ).id == evidence.id
    with pytest.raises(ValueError, match="different input"):
        await queue.record_evidence(attempt.id, "report", {"artifact_id": 11}, lease)
    decision = await queue.record_plan(created.id, "plan", {"action": "retry"}, lease)
    assert (
        await queue.record_plan(created.id, "plan", {"action": "retry"}, lease)
    ).id == decision.id
    with pytest.raises(ValueError, match="different input"):
        await queue.record_plan(created.id, "plan", {"action": "remove"}, lease)
    await queue.finish_attempt(attempt.id, "failed", lease)
    await postgres.close()
    restored = await MergeQueue.register(queue.workspace_id, queue.repository_id, "main")
    with pytest.raises(ValueError, match="Attempt budget"):
        await restored.start_attempt(created.id, "retry", deadline, lease)
    with pytest.raises(ValueError, match="Planning budget"):
        await restored.record_plan(created.id, "retry-plan", {}, lease)
    history = await restored.history(created.id)
    assert history.attempts[0].state == "failed"
    assert history.evidence[0].reference == {"artifact_id": 10}
    assert (history.decisions[0].lease_owner, history.decisions[0].fence) == (
        lease.owner,
        lease.fence,
    )
    assert history.batch.state == "active"


async def test_push_preserves_intent_but_cannot_reuse_admission_or_change_history(
    queue: MergeQueue,
) -> None:
    lease = await claim(queue)
    created = await batch(queue, lease)
    member = (await queue.history(created.id)).members[0]
    await queue.refresh_head(member.request_id, "b" * 40, lease)
    pending = (await queue.requests())[0]
    assert pending.id == member.request_id and pending.state == "waiting_for_review"
    assert pending.head_sha == "b" * 40
    replay = await queue.request_merge(pending.pull_request_id, member.head_sha, "pr-1")
    assert replay.head_sha == "b" * 40
    history = await queue.history(created.id)
    assert history.batch.state == "invalidated" and history.members[0].head_sha == member.head_sha
    with pytest.raises(ValueError, match="obsolete head"):
        await queue.admit(pending.id, member.head_sha, lease)
    await queue.admit(pending.id, pending.head_sha, lease)
    assert (await queue.requests())[0].state == "queued"


async def test_cross_queue_ids_and_expired_results_are_rejected(queue: MergeQueue) -> None:
    lease = await claim(queue)
    created = await batch(queue, lease)
    attempt = await queue.start_attempt(created.id, "run", created.deadline, lease)
    other = await MergeQueue.register(queue.workspace_id, queue.repository_id, "release")
    other_lease = await claim(other)
    with pytest.raises(ValueError, match="Batch not found"):
        await other.history(created.id)
    with pytest.raises(ValueError, match="Attempt not found"):
        await other.record_evidence(attempt.id, "foreign", {}, other_lease)
    member = (await queue.history(created.id)).members[0]
    with pytest.raises(ValueError, match="Merge request not found"):
        await other.withdraw(member.request_id)
    async with postgres.session() as session:
        await session.execute(
            update(Attempt)
            .where(Attempt.id == attempt.id)
            .values(
                deadline=func.clock_timestamp() - timedelta(seconds=1),
            )
        )
    with pytest.raises(ValueError, match="Expired"):
        await queue.finish_attempt(attempt.id, "succeeded", lease)
    await queue.finish_attempt(attempt.id, "timed_out", lease)


async def test_retention_preserves_live_work_and_referenced_requests(queue: MergeQueue) -> None:
    lease = await claim(queue)
    first = await batch(queue, lease)
    attempt = await queue.start_attempt(first.id, "run", first.deadline, lease)
    await queue.record_evidence(attempt.id, "report", {"artifact_id": 1}, lease)
    assert await queue.prune_history(datetime.now(UTC), lease) == 0
    await queue.invalidate(first.id, lease)
    member = (await queue.history(first.id)).members[0]
    second = await queue.create_batch(
        [member.request_id],
        request_key="second",
        base_sha="b" * 40,
        policy={},
        deadline=first.deadline,
        max_attempts=1,
        max_plans=0,
        lease=lease,
    )
    assert await queue.prune_history(datetime.now(UTC), lease) == 1
    with pytest.raises(ValueError, match="not found"):
        await queue.history(first.id)
    assert (await queue.history(second.id)).members[0].request_id == member.request_id
    await queue.withdraw(member.request_id)
    cutoff = datetime.now(UTC)
    async with postgres.session() as session:
        await session.execute(
            update(Batch)
            .where(Batch.id == second.id)
            .values(finished_at=cutoff + timedelta(hours=1))
        )
    assert await queue.prune_history(cutoff, lease) == 0
    async with postgres.session() as session:
        await session.execute(
            update(Batch)
            .where(Batch.id == second.id)
            .values(finished_at=cutoff - timedelta(seconds=1))
        )
    assert await queue.prune_history(cutoff, lease) == 2
    async with postgres.session() as session:
        assert await session.scalar(select(func.count()).select_from(MergeQueue)) == 1


async def test_database_rejects_duplicate_owners_and_active_requests(queue: MergeQueue) -> None:
    pending = await request(queue, 1)
    with pytest.raises(IntegrityError):
        async with postgres.session() as session:
            session.add(
                MergeQueue(
                    workspace_id=queue.workspace_id,
                    repository_id=queue.repository_id,
                    target_branch="main",
                )
            )
    with pytest.raises(IntegrityError):
        async with postgres.session() as session:
            session.add(
                MergeRequest(
                    queue_id=queue.id,
                    pull_request_id=pending.pull_request_id,
                    head_sha=pending.head_sha,
                    request_key="duplicate",
                )
            )


async def test_workspace_deletion_removes_its_queue_history(queue: MergeQueue) -> None:
    lease = await claim(queue)
    created = await batch(queue, lease)
    attempt = await queue.start_attempt(created.id, "run", created.deadline, lease)
    await queue.record_evidence(attempt.id, "report", {"artifact_id": 1}, lease)
    await queue.record_plan(created.id, "plan", {"action": "retry"}, lease)

    await WORKSPACES.delete("widgets")

    async with postgres.session() as session:
        for model in (MergeQueue, MergeRequest, Batch, BatchMember, Attempt, Evidence, Decision):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
        assert await session.get(Repository, queue.repository_id) is not None
        assert await session.scalar(select(func.count()).select_from(PullRequest)) == 1
