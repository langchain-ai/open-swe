"""Storage regressions use the same migrated PostgreSQL database as the application."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid7

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from openswe.database import postgres
from openswe.github.http import RepoClient
from openswe.github.pull_requests import PullRequest
from openswe.github.repositories import Repository
from openswe.merge_queue import controller, runtime
from openswe.merge_queue.admission import Admission, AdmissionReader, CheckSource, QueuePolicy
from openswe.merge_queue.configuration import QueueConfiguration
from openswe.merge_queue.controller import QueueController
from openswe.merge_queue.dispatch import DispatchGateway, DispatchRejected
from openswe.merge_queue.git import Candidate, CandidateGit, PullHead, candidate_ref
from openswe.merge_queue.models import (
    Attempt,
    Batch,
    BatchMember,
    Decision,
    Evidence,
    Lease,
    MergeRequest,
)
from openswe.merge_queue.runtime import QueueJob, QueueRuntime
from openswe.merge_queue.scheduler import BatchScheduler
from openswe.merge_queue.store import LeaseLost, MergeQueue
from openswe.merge_queue.validation_contract import ValidationPolicy
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


async def prepare(queue: MergeQueue, policy: QueuePolicy) -> tuple[Lease, list[MergeRequest]]:
    async with postgres.session() as session:
        await session.execute(
            update(MergeQueue).where(MergeQueue.id == queue.id).values(paused=False)
        )
    lease = await claim(queue)
    requests = [await request(queue, number) for number in range(1, 4)]
    for pending in requests:
        await queue.refresh_admission(
            pending.id, pending.head_sha, admission(pending, policy), lease
        )
    return lease, requests


def admission(
    pending: MergeRequest, policy: QueuePolicy, *, snapshot: str = "reviewed"
) -> Admission:
    return Admission(
        head_sha=pending.head_sha,
        base_sha="a" * 40,
        checked_at=datetime.now(UTC),
        policy_digest=policy.fingerprint,
        snapshot_digest=snapshot,
    )


def candidate_for(batch: Batch, requests: list[MergeRequest]) -> Candidate:
    return Candidate(
        repository="https://github.com/acme/widgets.git",
        branch="main",
        base_sha=batch.base_sha,
        pulls=tuple(
            PullHead(number=int(pending.head_sha, 16), sha=pending.head_sha) for pending in requests
        ),
        merges=tuple(f"{100 + position:040x}" for position in range(len(requests))),
        tree_sha="b" * 40,
    )


async def test_collection_window_dependency_order_and_stale_admission(queue: MergeQueue) -> None:
    policy = QueuePolicy(checks={}, max_batch_size=3)
    lease, requests = await prepare(queue, policy)
    async with postgres.session() as session:
        await session.execute(
            update(MergeRequest).where(MergeRequest.id == requests[0].id).values(dependencies=[2])
        )
    batch = await queue.schedule("a" * 40, policy, lease)
    assert batch is not None
    assert [entry.number for entry in (await queue.candidate_inputs(batch.id, lease))[1]] == [
        2,
        1,
        3,
    ]
    await queue.withdraw(requests[2].id)
    assert await queue.schedule("a" * 40, policy, lease) is None
    async with postgres.session() as session:
        await session.execute(
            update(MergeRequest)
            .where(MergeRequest.queue_id == queue.id)
            .values(queued_at=datetime.now(UTC) - timedelta(minutes=31))
        )
    smaller = await queue.schedule("a" * 40, policy, lease)
    assert smaller is not None
    assert len((await queue.history(smaller.id)).members) == 2
    changed = admission(requests[0], policy, snapshot="new review")
    await queue.refresh_admission(requests[0].id, requests[0].head_sha, changed, lease)
    assert (await queue.history(smaller.id)).batch.state == "invalidated"
    await queue.refresh_admission(
        requests[0].id,
        requests[0].head_sha,
        changed.model_copy(update={"checked_at": datetime.now(UTC) - timedelta(minutes=3)}),
        lease,
    )
    single = QueuePolicy(checks={}, max_batch_size=1)
    await queue.refresh_admission(
        requests[1].id, requests[1].head_sha, admission(requests[1], single), lease
    )
    next_batch = await queue.schedule("a" * 40, single, lease)
    assert next_batch is not None
    assert [entry.number for entry in (await queue.candidate_inputs(next_batch.id, lease))[1]] == [
        2
    ]
    assert await queue.schedule("c" * 40, single, lease) is None
    assert (await queue.history(next_batch.id)).batch.state == "invalidated"


async def test_dependency_cycles_and_removed_approvals_block_candidates(queue: MergeQueue) -> None:
    first = await PullRequest(owner="acme", repo="widgets", number=1, base_ref="main").save()
    second = await PullRequest(owner="acme", repo="widgets", number=2, base_ref="main").save()
    await queue.request_merge(first.id, "1" * 40, "first", dependencies=(2,))
    with pytest.raises(ValueError, match="dependency cycle"):
        await queue.request_merge(second.id, "2" * 40, "second", dependencies=(1,))
    lease = await claim(queue)
    policy = QueuePolicy(checks={}, collection_seconds=0)
    pending = (await queue.requests())[0]
    await queue.refresh_admission(pending.id, pending.head_sha, admission(pending, policy), lease)
    async with postgres.session() as session:
        await session.execute(
            update(MergeQueue).where(MergeQueue.id == queue.id).values(paused=False)
        )
    assert await queue.schedule("a" * 40, policy, lease) is None
    approved = admission(pending, policy).model_copy(update={"merged_dependencies": (2,)})
    await queue.refresh_admission(pending.id, pending.head_sha, approved, lease)
    batch = await queue.schedule("a" * 40, policy, lease)
    assert batch is not None
    await queue.refresh_admission(
        pending.id, pending.head_sha, None, lease, reason="Review dismissed"
    )
    assert (await queue.history(batch.id)).batch.state == "invalidated"
    assert (await queue.requests())[0].state == "waiting_for_review"
    with pytest.raises(ValueError, match="inactive"):
        await queue.candidate_inputs(batch.id, lease)


async def test_candidate_manifest_fence_and_retention(queue: MergeQueue) -> None:
    policy = QueuePolicy(checks={}, max_batch_size=3, max_build_attempts=1)
    lease, requests = await prepare(queue, policy)
    batch = await queue.schedule("a" * 40, policy, lease)
    assert batch is not None
    assert await queue.begin_build(batch.id, lease)
    candidate = candidate_for(batch, requests)
    await queue.save_candidate(batch.id, candidate, lease)
    await queue.save_candidate(batch.id, candidate, lease)
    with pytest.raises(ValueError, match="immutable"):
        await queue.save_candidate(
            batch.id, candidate.model_copy(update={"tree_sha": "c" * 40}), lease
        )
    await queue.release(lease)
    replacement = await claim(queue)
    with pytest.raises(LeaseLost):
        await queue.save_candidate(batch.id, candidate, lease, published=True)
    await queue.save_candidate(batch.id, candidate, replacement, published=True)
    assert not await queue.begin_build(batch.id, replacement)
    await queue.refresh_head(requests[0].id, "d" * 40, replacement)
    assert (await queue.history(batch.id)).batch.state == "invalidated"
    cutoff = datetime.now(UTC) - timedelta(days=90)
    async with postgres.session() as session:
        await session.execute(
            update(Batch).where(Batch.id == batch.id).values(finished_at=cutoff - timedelta(days=1))
        )
    assert [row.id for row in await queue.expired_candidates(cutoff, replacement)] == [batch.id]
    assert await queue.prune_history(cutoff, replacement) == 0
    with pytest.raises(ValueError, match="does not match"):
        await queue.candidate_deleted(batch.id, "e" * 40, replacement)
    await queue.candidate_deleted(batch.id, candidate.sha, replacement)
    assert await queue.prune_history(cutoff, replacement) == 1


@pytest.mark.parametrize("failure", ["build_budget", "published_ref"])
async def test_candidate_infrastructure_failure_retries_unchanged_members(
    queue: MergeQueue, failure: str
) -> None:
    policy = QueuePolicy(checks={}, max_batch_size=3, max_build_attempts=1)
    lease, requests = await prepare(queue, policy)
    failed = await queue.schedule("a" * 40, policy, lease)
    assert failed is not None
    assert await queue.begin_build(failed.id, lease)
    if failure == "build_budget":
        reason = "Candidate build retry budget exhausted"
        assert not await queue.begin_build(failed.id, lease)
    else:
        reason = "Published candidate ref changed or disappeared"
        candidate = candidate_for(failed, requests)
        await queue.save_candidate(failed.id, candidate, lease)
        await queue.save_candidate(failed.id, candidate, lease, published=True)
        await queue.candidate_failed(failed.id, reason, lease)
    history = await queue.history(failed.id)
    assert history.batch.state == "invalidated" and history.batch.candidate_error == reason
    assert all(pending.blocked_reason == reason for pending in await queue.requests())
    assert await queue.schedule("a" * 40, policy, lease) is None
    for pending in requests:
        await queue.refresh_admission(
            pending.id, pending.head_sha, admission(pending, policy), lease
        )
    following = await queue.schedule("a" * 40, policy, lease)
    assert following is not None
    assert [entry.number for entry in (await queue.candidate_inputs(following.id, lease))[1]] == [
        1,
        2,
        3,
    ]
    assert await queue.begin_build(following.id, lease)


@pytest.mark.parametrize("recovery", ["head", "base"])
async def test_candidate_conflict_blocks_only_the_culprit_until_inputs_change(
    queue: MergeQueue, recovery: str
) -> None:
    policy = QueuePolicy(checks={}, max_batch_size=3, collection_seconds=0)
    lease, requests = await prepare(queue, policy)
    failed = await queue.schedule("a" * 40, policy, lease)
    assert failed is not None
    await queue.candidate_failed(failed.id, "Merge conflict in PR #2", lease, conflicting_pr=2)
    history = await queue.history(failed.id)
    assert history.batch.state == "invalidated" and history.batch.finished_at is not None
    # Neither a failed admission read nor a fresh approval can erase a Git conflict.
    culprit = requests[1]
    await queue.refresh_admission(
        culprit.id, culprit.head_sha, None, lease, reason="GitHub unavailable"
    )
    for pending in requests:
        await queue.refresh_admission(
            pending.id, pending.head_sha, admission(pending, policy), lease
        )
    blocked = next(pending for pending in await queue.requests() if pending.id == culprit.id)
    assert blocked.blocked_reason == "Merge conflict in PR #2"
    following = await queue.schedule("a" * 40, policy, lease)
    assert following is not None
    assert [entry.number for entry in (await queue.candidate_inputs(following.id, lease))[1]] == [
        1,
        3,
    ]
    await queue.invalidate(following.id, lease)
    base = "b" * 40 if recovery == "base" else "a" * 40
    if recovery == "head":
        await queue.refresh_head(culprit.id, "d" * 40, lease)
    for pending in await queue.requests():
        refreshed = admission(pending, policy).model_copy(update={"base_sha": base})
        await queue.refresh_admission(pending.id, pending.head_sha, refreshed, lease)
    recovered = await queue.schedule(base, policy, lease)
    assert recovered is not None
    assert [entry.number for entry in (await queue.candidate_inputs(recovered.id, lease))[1]] == [
        1,
        2,
        3,
    ]


@pytest.mark.parametrize("failure", ["lost_response", "lost_lease"])
async def test_scheduler_recovers_publication_but_stops_on_lease_loss(
    queue: MergeQueue, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = QueuePolicy(checks={}, max_batch_size=3)
    lease, requests = await prepare(queue, policy)
    repo = Mock(spec=RepoClient)
    repo.full_name = "acme/widgets"
    repo.branch_sha = AsyncMock(return_value="a" * 40)

    def pull(number: int) -> Mock:
        client = Mock()
        client.queue_state = AsyncMock(
            return_value=Mock(head=f"{number:040x}", merged=False, state="OPEN")
        )
        return client

    repo.pull_request.side_effect = pull
    refs: dict[str, str] = {}
    repo.ref_sha = AsyncMock(side_effect=lambda ref: refs.get(ref))
    built = sandboxes = 0

    async def build(
        branch: str, base_sha: str, pulls: tuple[PullHead, ...], *, timestamp: int
    ) -> Candidate:
        nonlocal built
        built += 1
        if failure == "lost_lease":
            await queue.release(lease)
        return Candidate(
            repository="https://github.com/acme/widgets.git",
            branch=branch,
            base_sha=base_sha,
            pulls=pulls,
            merges=tuple(f"{100 + p.number:040x}" for p in pulls),
            tree_sha="b" * 40,
        )

    async def publish(candidate: Candidate, batch_id: UUID) -> None:
        refs[candidate_ref(batch_id)] = candidate.sha
        raise RuntimeError("Response lost after remote accepted publication")

    git = Mock(spec=CandidateGit)
    git.build = AsyncMock(side_effect=build)
    git.publish = AsyncMock(side_effect=publish)

    @asynccontextmanager
    async def candidate_git() -> AsyncIterator[CandidateGit]:
        nonlocal sandboxes
        sandboxes += 1
        yield git

    repo.candidate_git = candidate_git
    scheduler = BatchScheduler(queue, repo, policy)
    monkeypatch.setattr(
        scheduler.admission,
        "check",
        AsyncMock(
            side_effect=lambda number, head, dependencies: admission(requests[number - 1], policy)
        ),
    )
    with pytest.raises(LeaseLost if failure == "lost_lease" else RuntimeError):
        await scheduler.reconcile(lease)
    if failure == "lost_lease":
        assert refs == {}
    else:
        await scheduler.reconcile(lease)
        await scheduler.reconcile(lease)
        batch = await queue.schedule("a" * 40, policy, lease)
        assert batch is not None
        assert batch.published_at is not None
        assert batch.build_attempts == built == 1
        assert refs == {candidate_ref(batch.id): Candidate.model_validate(batch.candidate).sha}
        assert sandboxes == 1


@pytest.fixture
async def validation_batch(queue: MergeQueue) -> tuple[Lease, Batch, QueuePolicy]:
    policy = QueuePolicy(
        checks={}, max_batch_size=3, validation=ValidationPolicy(dispatch_actor_id=123)
    )
    lease, requests = await prepare(queue, policy)
    batch = await queue.schedule("a" * 40, policy, lease)
    assert batch is not None
    candidate = candidate_for(batch, requests)
    await queue.save_candidate(batch.id, candidate, lease)
    await queue.save_candidate(batch.id, candidate, lease, published=True)
    return lease, (await queue.history(batch.id)).batch, policy


async def test_validation_intent_is_atomic_and_survives_restart_and_pause(
    queue: MergeQueue, validation_batch: tuple[Lease, Batch, QueuePolicy]
) -> None:
    lease, batch, policy = validation_batch
    async with postgres.session() as session:
        await session.execute(
            update(MergeQueue).where(MergeQueue.id == queue.id).values(paused=True)
        )
    assert await queue.advance_validation(policy, lease, start=batch.id) is None
    async with postgres.session() as session:
        await session.execute(
            update(MergeQueue).where(MergeQueue.id == queue.id).values(paused=False)
        )
    results = await asyncio.gather(
        *(queue.advance_validation(policy, lease, start=batch.id) for _ in range(2))
    )
    first, replay = results
    assert first is not None and replay is not None
    assert first.dispatch != replay.dispatch
    assert first.request == replay.request
    assert first.attempt is not None and replay.attempt is not None
    assert first.attempt.id == replay.attempt.id
    deadline = first.attempt.deadline
    await queue.release(lease)
    await postgres.close()
    restored = await MergeQueue.load(queue.id, queue.workspace_id)
    assert restored is not None
    replacement = await claim(restored)
    async with postgres.session() as session:
        await session.execute(
            update(MergeQueue).where(MergeQueue.id == queue.id).values(paused=True)
        )
    recovered = await restored.advance_validation(policy, replacement, start=batch.id)
    assert recovered is not None and not recovered.dispatch
    assert recovered.attempt is not None and recovered.attempt.deadline == deadline
    await restored.bind_validation_run(first.attempt.id, 42, replacement)
    with pytest.raises(ValueError, match="different input"):
        await restored.bind_validation_run(first.attempt.id, 43, replacement)
    with pytest.raises(LeaseLost):
        await queue.bind_validation_run(first.attempt.id, 42, lease)
    history = await restored.history(batch.id)
    assert len(history.attempts) == 1 and history.decisions == []
    assert all(row.created_by is None for row in history.evidence)


@pytest.mark.parametrize("interruption", ["timeout", "withdrawal", "policy"])
async def test_validation_cannot_revive_stale_work(
    queue: MergeQueue, validation_batch: tuple[Lease, Batch, QueuePolicy], interruption: str
) -> None:
    lease, batch, policy = validation_batch
    work = await queue.advance_validation(policy, lease, start=batch.id)
    assert work is not None and work.attempt is not None and work.request is not None
    if interruption == "timeout":
        async with postgres.session() as session:
            await session.execute(
                update(Attempt)
                .where(Attempt.id == work.attempt.id)
                .values(deadline=func.clock_timestamp() - timedelta(seconds=1))
            )
    elif interruption == "withdrawal":
        await queue.withdraw((await queue.history(batch.id)).members[0].request_id)
    else:
        policy = policy.model_copy(update={"validation": ValidationPolicy(dispatch_actor_id=456)})
    assert await queue.advance_validation(policy, lease, start=batch.id) is None
    assert not await queue.finish_validation(
        work.attempt.id, work.request.model_dump(mode="json"), lease, succeeded=True
    )
    history = await queue.history(batch.id)
    assert history.attempts[0].state == ("timed_out" if interruption == "timeout" else "cancelled")
    assert history.batch.state != "landed"


async def test_validation_result_is_bound_to_saved_request_and_run(
    queue: MergeQueue, validation_batch: tuple[Lease, Batch, QueuePolicy]
) -> None:
    lease, batch, policy = validation_batch
    work = await queue.advance_validation(policy, lease, start=batch.id)
    assert work is not None and work.attempt is not None and work.request is not None
    report = {
        **work.request.model_dump(mode="json"),
        "run_id": 42,
        "run_attempt": 1,
        "tested_sha": work.request.candidate_sha,
        "artifact_id": 7,
    }
    with pytest.raises(ValueError, match="does not match its run"):
        await queue.finish_validation(work.attempt.id, report, lease, succeeded=True)
    await queue.bind_validation_run(work.attempt.id, 42, lease)
    with pytest.raises(ValueError, match="does not match its request"):
        await queue.finish_validation(
            work.attempt.id, {**report, "candidate_sha": "f" * 40}, lease, succeeded=True
        )
    assert await queue.finish_validation(work.attempt.id, report, lease, succeeded=True)
    assert not await queue.finish_validation(work.attempt.id, report, lease, succeeded=True)
    history = await queue.history(batch.id)
    assert history.attempts[0].state == "succeeded" and history.batch.state == "active"
    assert history.evidence[-1].reference["coverage"] == "smoke"
    assert await queue.advance_validation(policy, lease, start=batch.id) is None


async def test_validation_job_expires_saved_budget_during_github_outage(
    queue: MergeQueue, validation_batch: tuple[Lease, Batch, QueuePolicy]
) -> None:
    lease, batch, policy = validation_batch
    work = await queue.advance_validation(policy, lease, start=batch.id)
    assert work is not None and work.attempt is not None
    await queue.release(lease)
    async with postgres.session() as session:
        await session.execute(
            update(Attempt)
            .where(Attempt.id == work.attempt.id)
            .values(deadline=func.clock_timestamp() - timedelta(seconds=1))
        )

    @asynccontextmanager
    async def unavailable(_queue: MergeQueue) -> AsyncIterator[RepoClient]:
        raise RuntimeError("GitHub unavailable")
        yield  # pragma: no cover

    runtime = QueueRuntime({queue.id: policy}, unavailable, Mock(spec=DispatchGateway))
    with pytest.raises(RuntimeError, match="GitHub unavailable"):
        await runtime.reconcile(QueueJob(queue_id=queue.id, workspace_id=queue.workspace_id))
    history = await queue.history(batch.id)
    assert len(history.attempts) == 1 and history.attempts[0].state == "timed_out"
    assert history.batch.state == "active"


@pytest.mark.parametrize("cancelled", [False, True])
async def test_validation_precedes_cleanup_and_cancellation_skips_it(
    queue: MergeQueue,
    validation_batch: tuple[Lease, Batch, QueuePolicy],
    monkeypatch: pytest.MonkeyPatch,
    cancelled: bool,
) -> None:
    lease, expired, policy = validation_batch
    candidate = Candidate.model_validate(expired.candidate)
    await queue.invalidate(expired.id, lease)
    async with postgres.session() as session:
        await session.execute(
            update(Batch)
            .where(Batch.id == expired.id)
            .values(finished_at=datetime.now(UTC) - timedelta(days=91))
        )
    current = await queue.schedule(candidate.base_sha, policy, lease)
    assert current is not None
    await queue.save_candidate(current.id, candidate, lease)
    await queue.save_candidate(current.id, candidate, lease, published=True)
    requests = {entry.number: entry.request for entry in await queue.entries(lease)}
    await queue.release(lease)

    repo = Mock(spec=RepoClient, full_name="acme/widgets")
    repo.branch_sha = AsyncMock(return_value=candidate.base_sha)
    repo.ref_sha = AsyncMock(return_value=candidate.sha)
    dispatcher = Mock(spec=DispatchGateway, dispatch=AsyncMock(side_effect=DispatchRejected(403)))
    if cancelled:
        dispatcher.dispatch.side_effect = asyncio.CancelledError()

    def pull(number: int) -> Mock:
        return Mock(
            queue_state=AsyncMock(
                return_value=Mock(head=requests[number].head_sha, merged=False, state="OPEN")
            )
        )

    repo.pull_request.side_effect = pull
    monkeypatch.setattr(
        AdmissionReader,
        "check",
        AsyncMock(
            side_effect=lambda number, head, dependencies: admission(requests[number], policy)
        ),
    )
    states_at_cleanup: list[str] = []

    @asynccontextmanager
    async def unavailable_sandbox() -> AsyncIterator[CandidateGit]:
        states_at_cleanup.extend(
            attempt.state for attempt in (await queue.history(current.id)).attempts
        )
        raise TimeoutError("Cleanup sandbox unavailable")
        yield  # pragma: no cover

    repo.candidate_git = unavailable_sandbox

    @asynccontextmanager
    async def open_repo(_queue: MergeQueue) -> AsyncIterator[RepoClient]:
        yield repo

    runtime = QueueRuntime({queue.id: policy}, open_repo, dispatcher)
    payload = QueueJob(queue_id=queue.id, workspace_id=queue.workspace_id)
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await runtime.reconcile(payload)
    else:
        await runtime.reconcile(payload)
    assert states_at_cleanup == ([] if cancelled else ["failed"])
    assert (await queue.history(expired.id)).batch.ref_deleted_at is None


@pytest.fixture
async def queue_controller(queue: MergeQueue, monkeypatch: pytest.MonkeyPatch) -> QueueController:
    monkeypatch.setenv("MERGE_QUEUE_DISPATCH_SECRET", "s" * 32)
    monkeypatch.setenv("GITHUB_APP_ID", "99")
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    monkeypatch.setattr(runtime, "RUNTIME", None)
    monkeypatch.setattr(controller, "CONTROLLER", None)
    config = QueueConfiguration(
        workspace_id=queue.workspace_id,
        repository="acme/widgets",
        repository_id=42,
        dispatch_url="https://dispatcher.example/queue",
        workflow_digest="a" * 64,
        policy=QueuePolicy(
            checks={"checks": CheckSource(app_id=1, workflow_path=".github/workflows/ci.yml")},
            validation=ValidationPolicy(dispatch_actor_id=789),
        ),
    )
    async with postgres.session() as session:
        await session.execute(
            update(WorkspaceRow)
            .where(WorkspaceRow.id == queue.workspace_id)
            .values(merge_queue_config=config.model_dump(mode="json", exclude={"workspace_id"}))
        )
    await controller.start_queue()
    assert controller.CONTROLLER is not None
    return controller.CONTROLLER


async def test_queue_startup_preserves_state_and_running_job(
    queue_controller: QueueController,
) -> None:
    from sqlalchemy import text

    from openswe.jobs import queue as jobs

    queue = queue_controller.queue
    assert queue.paused
    pending = await request(queue, 1)
    first_job = await jobs.get(f"merge-queue:{queue.id}")
    assert first_job is not None
    # Hold the recurring job while a second replica starts.
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE job SET due_at = clock_timestamp() WHERE id = :id"), {"id": first_job.id}
        )
        await conn.execute(update(MergeQueue).where(MergeQueue.id == queue.id).values(paused=False))
    claimed = next(job for job in await jobs.claim_due(100) if job.id == first_job.id)

    await controller.start_queue()
    assert controller.CONTROLLER is not None
    assert controller.CONTROLLER.config == queue_controller.config
    next_job = await jobs.get(f"merge-queue:{queue.id}")
    assert next_job is not None and next_job.claim_token == claimed.claim_token
    restored = await MergeQueue.load(queue.id, queue.workspace_id)
    assert restored is not None and not restored.paused
    assert [row.id for row in await restored.requests()] == [pending.id]

    async with postgres.session() as session:
        await session.execute(
            update(WorkspaceRow)
            .where(WorkspaceRow.id == queue.workspace_id)
            .values(merge_queue_config=None)
        )
    await controller.start_queue()
    assert controller.CONTROLLER is None and runtime.RUNTIME is None


async def test_queue_startup_rejects_multiple_configured_workspaces(
    queue_controller: QueueController,
) -> None:
    async with postgres.session() as session:
        session.add(
            WorkspaceRow(
                slug="other-canary",
                name="Other canary",
                merge_queue_config=queue_controller.config.model_dump(
                    mode="json", exclude={"workspace_id"}
                ),
            )
        )
    with pytest.raises(ValueError, match="Only one canary workspace"):
        await controller.start_queue()
    assert controller.CONTROLLER is None and runtime.RUNTIME is None
