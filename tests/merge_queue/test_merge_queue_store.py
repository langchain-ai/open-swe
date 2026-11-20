"""Storage regressions use the same migrated PostgreSQL database as the application."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid7

import httpx2
import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from openswe.database import postgres
from openswe.github.http import GitHubClient, RepoClient
from openswe.github.pull_requests import PullRequest
from openswe.github.queue import NativeQueueSnapshot
from openswe.github.repositories import Repository
from openswe.merge_queue import controller, runtime
from openswe.merge_queue.admission import Admission, AdmissionReader, CheckSource, QueuePolicy
from openswe.merge_queue.configuration import QueueConfiguration
from openswe.merge_queue.controller import QueueController
from openswe.merge_queue.diagnosis import JOB as RECOVERY_JOB
from openswe.merge_queue.diagnosis import QueueRecovery
from openswe.merge_queue.dispatch import (
    DeferralForbidden,
    DispatchGateway,
    DispatchRejected,
    WorkflowPolicyMismatch,
)
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
from openswe.merge_queue.native import NativeQueueRuntime
from openswe.merge_queue.recovery import RecoveryMember, RecoveryProposal, RecoverySettings
from openswe.merge_queue.routes import PauseChange, QueueAccess, set_paused
from openswe.merge_queue.runtime import QueueJob, QueueRuntime
from openswe.merge_queue.scheduler import BatchScheduler
from openswe.merge_queue.store import LeaseLost, MergeQueue, NativeWork
from openswe.merge_queue.validation_contract import ValidationPolicy
from openswe.routing.store import INTEGRATIONS, RepositoryBinding
from openswe.users.models import User

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture
async def queue() -> MergeQueue:
    repository = await Repository(full_name="acme/widgets").save()
    async with postgres.session() as session:
        binding = RepositoryBinding(repository_id=repository.id)
        session.add(binding)
    return await MergeQueue.register(binding.id, repository.id, "main")


async def request(queue: MergeQueue, number: int) -> MergeRequest:
    pull = await PullRequest(owner="acme", repo="widgets", number=number, base_ref="main").save()
    return await queue.request_merge(pull.id, f"{number:040x}", f"pr-{number}")


async def claim(queue: MergeQueue) -> Lease:
    lease = await queue.claim(uuid7(), timedelta(minutes=5))
    assert lease is not None
    return lease


async def save_configuration(config: QueueConfiguration) -> None:
    async with postgres.session() as session:
        await session.execute(
            update(RepositoryBinding)
            .where(RepositoryBinding.id == config.routing_id)
            .values(merge_queue_config=config.model_dump(mode="json", exclude={"routing_id"}))
        )


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
            MergeQueue.register(queue.bound_routing_id, queue.repository_id, "refs/heads/main")
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
    restored = await MergeQueue.register(queue.bound_routing_id, queue.repository_id, "main")
    assert [row.id for row in await restored.requests()] == [first.id]
    await restored.withdraw(first.id, actor=user.id)
    retry = await restored.request_merge(pull.id, "a" * 40, "submit")
    assert retry.id == first.id and retry.state == "withdrawn"
    assert retry.updated_by == user.id
    assert retry.updated_at > retry.created_at
    assert await restored.requests() == []
    assert (await restored.request_merge(pull.id, "b" * 40, "new-intent")).id != first.id


async def test_binding_scoping_and_rebinding_cannot_create_a_second_owner(
    queue: MergeQueue,
) -> None:
    other_repository = await Repository(full_name="other/repository").save()
    replacement = await Repository(full_name="other/replacement").save()
    async with postgres.session() as session:
        other = RepositoryBinding(repository_id=other_repository.id)
        session.add(other)
    with pytest.raises(ValueError, match="not bound"):
        await MergeQueue.register(other.id, queue.repository_id, "main")
    forged = MergeQueue(
        id=queue.id, routing_id=other.id, repository_id=queue.repository_id, target_branch="main"
    )
    with pytest.raises(ValueError, match="not bound"):
        await forged.requests()
    with pytest.raises(ValueError, match="Queue not found"):
        await forged.snapshot(limit=1)
    foreign = await PullRequest(owner="other", repo="repo", number=1, base_ref="main").save()
    with pytest.raises(ValueError, match="does not target"):
        await queue.request_merge(foreign.id, "a" * 40, "foreign")
    async with postgres.session() as session:
        await session.execute(
            update(RepositoryBinding)
            .where(
                RepositoryBinding.repository_id == queue.repository_id,
            )
            .values(repository_id=replacement.id)
        )
        await session.execute(
            update(RepositoryBinding)
            .where(RepositoryBinding.id == other.id)
            .values(repository_id=queue.repository_id)
        )
    with pytest.raises(ValueError, match="not bound"):
        await queue.requests()
    with pytest.raises(ValueError, match="binding changed"):
        await queue.snapshot(limit=1)
    with pytest.raises(ValueError, match="explicit handoff"):
        await MergeQueue.register(other.id, queue.repository_id, "main")
    with pytest.raises(ValueError, match="Queue not found"):
        await forged.claim(uuid7(), timedelta(minutes=1))


async def test_status_reads_do_not_wait_for_worker_locks(queue: MergeQueue) -> None:
    created = await batch(queue, await claim(queue))
    async with postgres.transaction() as conn:
        await conn.execute(
            select(RepositoryBinding)
            .where(RepositoryBinding.id == queue.bound_routing_id)
            .with_for_update()
        )
        await conn.execute(update(MergeQueue).where(MergeQueue.id == queue.id).values(paused=False))
        async with asyncio.timeout(5):
            paused, entries, active_batch_id = await queue.snapshot(limit=1)
            history = await queue.history(created.id)
        assert paused is True
        assert active_batch_id == history.batch.id == created.id
        assert [entry.request.id for entry in entries] == [
            member.request_id for member in history.members
        ]
    paused, _, _ = await queue.snapshot(limit=1)
    assert paused is False


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
    restored = await MergeQueue.register(queue.bound_routing_id, queue.repository_id, "main")
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
    other = await MergeQueue.register(queue.bound_routing_id, queue.repository_id, "release")
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
                    routing_id=queue.bound_routing_id,
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


async def test_queue_binding_cannot_be_unbound_and_out_of_band_removal_stays_fenced(
    queue: MergeQueue,
) -> None:
    with pytest.raises(ValueError, match="Cannot unbind.*merge queue"):
        await INTEGRATIONS.remove_repository("acme/widgets")
    lease = await claim(queue)
    created = await batch(queue, lease)
    attempt = await queue.start_attempt(created.id, "run", created.deadline, lease)
    await queue.record_evidence(attempt.id, "report", {"artifact_id": 1}, lease)
    await queue.record_plan(created.id, "plan", {"action": "retry"}, lease)

    async with postgres.session() as session:
        await session.execute(
            delete(RepositoryBinding).where(RepositoryBinding.id == queue.bound_routing_id)
        )

    assert await MergeQueue.load(queue.id, queue.bound_routing_id) is None
    with pytest.raises(ValueError, match="binding"):
        await queue.renew(lease, timedelta(seconds=60))
    with pytest.raises(ValueError, match="binding"):
        await queue.record_evidence(attempt.id, "late-report", {"artifact_id": 2}, lease)
    await INTEGRATIONS.configure_repository("acme/widgets", may_start_threads=False, actor=None)
    async with postgres.session() as session:
        replacement = (await session.scalars(select(RepositoryBinding))).one()
    with pytest.raises(ValueError, match="explicit handoff"):
        await MergeQueue.register(replacement.id, queue.repository_id, "main")

    async with postgres.session() as session:
        for model in (MergeQueue, MergeRequest, Batch, BatchMember, Attempt, Evidence, Decision):
            assert await session.scalar(select(func.count()).select_from(model)) == 1
        stored = await session.get(MergeQueue, queue.id)
        assert stored is not None and stored.routing_id is None
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
    restored = await MergeQueue.load(queue.id, queue.bound_routing_id)
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
        await runtime.reconcile(QueueJob(queue_id=queue.id, routing_id=queue.bound_routing_id))
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
    payload = QueueJob(queue_id=queue.id, routing_id=queue.bound_routing_id)
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
    monkeypatch.setattr(runtime, "RUNTIME", None)
    monkeypatch.setattr(controller, "CONTROLLER", None)
    config = QueueConfiguration(
        routing_id=queue.bound_routing_id,
        repository="acme/widgets",
        repository_id=42,
        dispatch_url="https://dispatcher.example/queue",
        workflow_digest="a" * 64,
        operator_emails=frozenset({"operator@example.com"}),
        policy=QueuePolicy(
            checks={"checks": CheckSource(app_id=1, workflow_path=".github/workflows/ci.yml")},
            validation=ValidationPolicy(dispatch_actor_id=789),
        ),
    )
    async with postgres.session() as session:
        await session.execute(
            update(RepositoryBinding)
            .where(RepositoryBinding.id == queue.bound_routing_id)
            .values(merge_queue_config=config.model_dump(mode="json", exclude={"routing_id"}))
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
    obsolete = await jobs.schedule(
        runtime.JOB_KIND,
        {"queue_id": str(queue.id), "workspace_id": str(uuid7())},
        key=f"merge-queue:{queue.id}",
        cron="* * * * *",
    )
    first_job = await jobs.get(f"merge-queue:{queue.id}:{queue.bound_routing_id}")
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
    next_job = await jobs.get(f"merge-queue:{queue.id}:{queue.bound_routing_id}")
    assert next_job is not None and next_job.claim_token == claimed.claim_token
    await runtime.reconcile_queue(obsolete)
    assert await jobs.get(f"merge-queue:{queue.id}") is None
    restored = await MergeQueue.load(queue.id, queue.bound_routing_id)
    assert restored is not None and not restored.paused
    assert [row.id for row in await restored.requests()] == [pending.id]

    async with postgres.session() as session:
        await session.execute(
            update(RepositoryBinding)
            .where(RepositoryBinding.id == queue.bound_routing_id)
            .values(merge_queue_config=None)
        )
    await controller.start_queue()
    assert controller.CONTROLLER is None and runtime.RUNTIME is None


async def test_native_observation_survives_restart_and_old_events(
    queue_controller: QueueController, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openswe.jobs import queue as jobs
    from tests.github_channel import deliver_github_event
    from tests.merge_queue.test_queue_controller import native_response

    original = queue_controller.queue
    config = queue_controller.config.model_copy(
        update={
            "mode": "native",
            "dispatch_url": None,
            "workflow_digest": None,
            "policy": QueuePolicy(checks={}),
        }
    )
    async with postgres.session() as session:
        await session.execute(
            update(RepositoryBinding)
            .where(RepositoryBinding.id == original.bound_routing_id)
            .values(merge_queue_config=config.model_dump(mode="json", exclude={"routing_id"}))
        )
    monkeypatch.delenv("MERGE_QUEUE_DISPATCH_SECRET")
    response = native_response()

    def respond(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.lower() == "/repos/acme/widgets" and request.method == "GET":
            return httpx2.Response(200, json={"id": 42})
        if request.url.path == "/graphql":
            return httpx2.Response(200, json={"data": {"repository": response}})
        pytest.fail(
            f"Native observer attempted another GitHub operation: {request.method} {request.url.path}"
        )

    @asynccontextmanager
    async def as_app(
        owner: str, repo: str, *, permissions: dict[str, str]
    ) -> AsyncIterator[GitHubClient]:
        assert (owner.lower(), repo.lower()) == ("acme", "widgets")
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
            yield GitHubClient(http)

    monkeypatch.setattr(GitHubClient, "as_app", as_app)
    await controller.start_queue()
    assert runtime.RUNTIME is not None
    payload = QueueJob(queue_id=original.id, routing_id=original.bound_routing_id)
    await runtime.RUNTIME.reconcile(payload)
    observed = await MergeQueue.load(original.id, original.bound_routing_id)
    assert observed is not None and observed.paused and observed.mode == "native"
    saved = NativeQueueSnapshot.model_validate(observed.native_snapshot)
    assert saved.observed_at is not None and len(saved.candidates) == 3
    old_abc = list(saved.candidates)[-1]

    await postgres.close()
    await controller.start_queue()
    restored = await MergeQueue.load(original.id, original.bound_routing_id)
    assert restored is not None and restored.native_snapshot == observed.native_snapshot
    # A delayed event is a wakeup only. The next read sees AC, not its old ABC payload.
    response = native_response((1, 3))
    event = {
        "action": "checks_requested",
        "repository": {
            "id": 42,
            "full_name": "Acme/Widgets",
            "owner": {"login": "acme"},
            "name": "widgets",
        },
        "merge_group": {"head_sha": old_abc, "base_ref": "refs/heads/main"},
    }
    for _ in range(2):
        delivered = await deliver_github_event(monkeypatch, "merge_group", event)
        assert delivered.work == ("wake_merge_queue",)
    job = await jobs.get(f"merge-queue-native-wakeup:{original.id}:{original.bound_routing_id}")
    assert job is not None and runtime.RUNTIME is not None
    await runtime.RUNTIME.reconcile(QueueJob.model_validate(job.payload))
    current = await MergeQueue.load(original.id, original.bound_routing_id)
    assert current is not None
    rebuilt = NativeQueueSnapshot.model_validate(current.native_snapshot)
    assert old_abc not in rebuilt.candidates
    assert [p.number for p in list(rebuilt.candidates.values())[-1].members] == [1, 3]
    # A real empty queue retires every candidate; API errors would instead leave the snapshot intact.
    response = native_response(())
    await runtime.RUNTIME.reconcile(payload)
    empty = await MergeQueue.load(original.id, original.bound_routing_id)
    assert (
        empty is not None
        and not NativeQueueSnapshot.model_validate(empty.native_snapshot).candidates
    )
    assert await current.requests() == []
    changed = config.model_copy(
        update={
            "repository": "ACME/WIDGETS",
            "policy": config.policy.model_copy(update={"max_batch_size": 2}),
        }
    )
    await save_configuration(changed)
    await runtime.RUNTIME.reconcile(payload)
    applied = await MergeQueue.load(original.id, original.bound_routing_id)
    assert applied is not None and applied.configuration_generation == changed.generation
    async with postgres.session() as session:
        await session.execute(
            update(RepositoryBinding)
            .where(RepositoryBinding.id == original.bound_routing_id)
            .values(merge_queue_config=None)
        )
    response = native_response((7,))
    await runtime.RUNTIME.reconcile(payload)
    disabled = await MergeQueue.load(original.id, original.bound_routing_id)
    assert disabled is not None
    assert list(NativeQueueSnapshot.model_validate(disabled.native_snapshot).candidates)
    assert disabled.lease_owner is None
    await controller.start_queue()
    assert runtime.RUNTIME is None
    await runtime.reconcile_queue(job)
    assert runtime.RUNTIME is None
    await save_configuration(changed)
    response = native_response(())
    await runtime.reconcile_queue(job)
    restored = await MergeQueue.load(original.id, original.bound_routing_id)
    assert restored is not None
    assert not NativeQueueSnapshot.model_validate(restored.native_snapshot).candidates
    with pytest.raises(LeaseLost, match="mode changed"):
        await original.claim(uuid7(), timedelta(seconds=60))
    with pytest.raises(ValueError, match="custom controller"):
        await queue_controller.start()


async def test_native_activation_requires_draining_custom_work(queue: MergeQueue) -> None:
    async with postgres.session() as session:
        operator = User(display_name="Operator")
        session.add(operator)
    pending = await request(queue, 1)
    with pytest.raises(ValueError, match="Drain custom"):
        await queue.enable_native_observation()
    await queue.withdraw(pending.id)
    lease = await claim(queue)
    with pytest.raises(ValueError, match="Pause and drain"):
        await queue.enable_native_observation()
    await queue.release(lease)
    await queue.set_paused(False, actor=operator.id)
    with pytest.raises(ValueError, match="Pause and drain"):
        await queue.enable_native_observation()
    await queue.set_paused(True, actor=operator.id)
    await queue.enable_native_observation()
    assert queue.mode == "native"


async def test_queue_startup_rejects_multiple_configured_repositories(
    queue_controller: QueueController,
) -> None:
    repository = await Repository(full_name="other/canary").save()
    async with postgres.session() as session:
        session.add(
            RepositoryBinding(
                repository_id=repository.id,
                merge_queue_config=queue_controller.config.model_dump(
                    mode="json", exclude={"routing_id"}
                ),
            )
        )
    with pytest.raises(ValueError, match="Only one canary repository"):
        await controller.start_queue()
    assert controller.CONTROLLER is None and runtime.RUNTIME is None


async def test_queue_dashboard_authorization(
    queue_controller: QueueController, monkeypatch: pytest.MonkeyPatch, langsmith_users
) -> None:
    import httpx
    from fastapi import Depends, FastAPI

    from openswe.dashboard import profiles, repo_access
    from openswe.dashboard.oauth import issue_session, require_same_origin_for_mutations
    from openswe.github.http import GitHubClient
    from openswe.merge_queue import routes
    from openswe.users.models import UserIdentity

    queue = queue_controller.queue
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "queue-test-session-secret")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://testserver")
    async with postgres.session() as session:
        people = [
            User(
                identities=[
                    UserIdentity(
                        provider="github", external_id=str(index), login=login, email=email
                    )
                ]
            )
            for index, (login, email) in enumerate(
                (
                    ("author", "author@example.com"),
                    ("coworker", "coworker@example.com"),
                    ("operator", "operator@example.com"),
                ),
                1,
            )
        ]
        for person in people:
            person.identities.append(
                UserIdentity(provider="langsmith", external_id=f"ls-{person.github_login}")
            )
            langsmith_users.people[person.id] = person
        session.add_all(people)
    author, coworker, operator = people
    permission, available = "write", True

    async def user_token(login: str, **kwargs: object) -> str:
        return login

    def github_response(request: httpx2.Request) -> httpx2.Response:
        if not available:
            return httpx2.Response(404, json={"message": "Not Found"})
        if request.url.path.endswith("/permission"):
            return httpx2.Response(200, json={"permission": permission})
        if request.url.path == "/graphql":
            return httpx2.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "number": 1,
                                "state": "OPEN",
                                "merged": False,
                                "isDraft": False,
                                "headRefOid": "a" * 40,
                                "baseRefOid": "b" * 40,
                                "baseRefName": "main",
                                "baseRef": {"target": {"oid": "b" * 40}},
                                "headRepository": {"nameWithOwner": "acme/widgets"},
                                "baseRepository": {"nameWithOwner": "acme/widgets"},
                                "reviewDecision": "REVIEW_REQUIRED",
                                "reviews": {
                                    "nodes": [],
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                },
                            }
                        }
                    }
                },
            )
        assert request.url.path.lower() == "/repos/acme/widgets"
        return httpx2.Response(200, json={"id": 42})

    @asynccontextmanager
    async def as_user(
        login: str, *, timeout: httpx2.Timeout | float | None = None
    ) -> AsyncIterator[GitHubClient]:
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(github_response),
            headers={"Authorization": f"Bearer {await user_token(login)}"},
            timeout=timeout,
        ) as client:
            yield GitHubClient(client, login=login)

    monkeypatch.setattr(profiles, "get_valid_access_token", user_token)
    monkeypatch.setattr(
        repo_access, "pooled_transport", lambda: httpx2.MockTransport(github_response)
    )
    monkeypatch.setattr(GitHubClient, "as_user", as_user)
    app = FastAPI()
    app.include_router(routes.router, dependencies=[Depends(require_same_origin_for_mutations)])
    path = f"/merge-queue/{queue.bound_routing_id}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": "http://testserver"},
    ) as client:
        assert (
            await client.get(path, headers={"Authorization": "Bearer agent-key"})
        ).status_code == 401
        client.cookies.set(
            "osw_session",
            issue_session(
                login=author.github_login,
                email=author.email,
                avatar_url=None,
                user_id=str(author.id),
            ),
        )
        assert (await client.get(f"/merge-queue/{uuid7()}")).status_code == 404
        available = False
        assert (await client.get(path)).status_code == 404
        available = True
        body = {"number": 1, "head_sha": "a" * 40, "request_key": str(uuid7())}
        permission = "read"
        assert (await client.post(f"{path}/requests", json=body)).status_code == 403
        permission = "write"
        assert (
            await client.post(
                f"{path}/requests", json=body, headers={"Origin": "http://evil.example"}
            )
        ).status_code == 403
        response = await client.post(f"{path}/requests", json=body)
        assert response.status_code == 200, response.text
        requested = response.json()
        assert requested["state"] == "waiting_for_review"
        assert (await client.post(f"{path}/requests", json=body)).json()["id"] == requested["id"]
        assert (await client.patch(f"{path}/pause", json={"paused": False})).status_code == 403
        client.cookies.set(
            "osw_session",
            issue_session(
                login=coworker.github_login,
                email=operator.email,
                avatar_url=None,
                user_id=str(coworker.id),
            ),
        )
        # An email claim cannot override the current stored person's identity.
        assert (await client.patch(f"{path}/pause", json={"paused": False})).status_code == 403
        assert (await client.delete(f"{path}/requests/{requested['id']}")).status_code == 403
        client.cookies.set(
            "osw_session",
            issue_session(
                login=operator.github_login,
                email=operator.email,
                avatar_url=None,
                user_id=str(operator.id),
            ),
        )
        assert (
            await client.patch(f"{path}/pause", json={"paused": False, "manual_hold": True})
        ).status_code == 422
        assert (await client.patch(f"{path}/pause", json={"paused": False})).status_code == 204
        revoked = queue_controller.config.model_copy(
            update={
                "repository": "ACME/WIDGETS",
                "operator_emails": frozenset({"another@example.com"}),
            }
        )
        await save_configuration(revoked)
        assert (await client.patch(f"{path}/pause", json={"paused": True})).status_code == 403
        await save_configuration(queue_controller.config)
        snapshot = (await client.get(path)).json()
        assert snapshot["paused"] is False and len(snapshot["items"]) == 1
        assert snapshot["next_cursor"] is None
        assert (await client.get(path, params={"cursor": "invalid"})).status_code == 400
        second, third, fourth = [await request(queue, number) for number in (2, 3, 4)]
        await queue.set_priority(fourth.id, -1)
        async with postgres.session() as session:
            await session.execute(
                update(MergeRequest)
                .where(MergeRequest.queue_id == queue.id)
                .values(created_at=datetime.now(UTC))
            )
        page = (await client.get(path, params={"page_size": 2})).json()
        assert [item["request"]["id"] for item in page["items"]] == [
            requested["id"],
            str(second.id),
        ]
        assert page["next_cursor"] is not None
        missing = uuid7()
        assert (await client.get(f"{path}/batches/{missing}")).status_code == 404
        assert (
            await client.patch(f"{path}/requests/{missing}/priority", json={"priority": 1})
        ).status_code == 404
        assert (await client.delete(f"{path}/requests/{missing}")).status_code == 204
        assert (await client.delete(f"{path}/requests/{requested['id']}")).status_code == 204
        assert (await client.delete(f"{path}/requests/{requested['id']}")).status_code == 204
        assert (
            await client.patch(f"{path}/requests/{requested['id']}/priority", json={"priority": 1})
        ).status_code == 409
        assert (await client.delete(f"{path}/requests/{second.id}")).status_code == 204
        page = (
            await client.get(path, params={"page_size": 2, "cursor": page["next_cursor"]})
        ).json()
        assert [item["request"]["id"] for item in page["items"]] == [
            str(third.id),
            str(fourth.id),
        ]
        assert page["next_cursor"] is None
        for pending in (third, fourth):
            assert (await client.delete(f"{path}/requests/{pending.id}")).status_code == 204
        assert (await client.get(path)).json()["items"] == []
        await queue.set_paused(True, actor=operator.id)
        await queue.enable_native_observation()
        native_config = queue_controller.config.model_copy(
            update={"mode": "native", "check_app_id": 456}
        )
        await save_configuration(native_config)
        assert (await client.patch(f"{path}/pause", json={"paused": True})).status_code == 204
        assert (await client.get(path)).json()["paused"] is True
        assert (await client.post(f"{path}/requests", json=body)).status_code == 409
        assert (await client.delete(f"{path}/requests/{missing}")).status_code == 409
        assert (
            await client.patch(f"{path}/requests/{missing}/priority", json={"priority": 1})
        ).status_code == 409
        permission = "read"
        assert (await client.patch(f"{path}/pause", json={"paused": False})).status_code == 403
        client.cookies.set(
            "osw_session",
            issue_session(
                login=author.github_login,
                email=author.email,
                avatar_url=None,
                user_id=str(author.id),
            ),
        )
        permission = "write"
        assert (await client.patch(f"{path}/pause", json={"paused": False})).status_code == 403
        async with postgres.session() as session:
            await session.execute(
                update(RepositoryBinding)
                .where(RepositoryBinding.id == queue.bound_routing_id)
                .values(merge_queue_config=None)
            )
        assert (await client.get(path)).status_code == 404


def native_snapshot(
    numbers: tuple[int, ...] = (1, 2, 3), *, main: str = "a" * 40, age: int = 90
) -> NativeQueueSnapshot:
    from tests.merge_queue.test_queue_controller import native_response

    response = native_response(numbers, main)
    for entry in response["mergeQueue"]["entries"]["nodes"]:
        entry["enqueuedAt"] = (datetime.now(UTC) - timedelta(seconds=age)).isoformat()
    return NativeQueueSnapshot.model_validate(response)


async def enable_native_validation(
    queue: MergeQueue, *, cap: int = 3, collection: int = 60
) -> tuple[QueuePolicy, Lease, User]:
    await queue.enable_native_observation()
    async with postgres.session() as session:
        operator = User(display_name="Queue operator")
        session.add(operator)
    await queue.set_paused(False, actor=operator.id)
    policy = QueuePolicy(
        checks={},
        max_batch_size=cap,
        collection_seconds=collection,
        validation=ValidationPolicy(dispatch_actor_id=123, deadline_seconds=120),
    )
    return policy, await claim(queue), operator


async def test_native_selection_survives_restart_and_recovers_ac_before_def(queue):
    policy, lease, _ = await enable_native_validation(queue)
    work = await queue.prepare_native_validation(
        native_snapshot((1, 2, 3, 4, 5, 6)), 600, lease, policy=policy, trust="trusted-policy"
    )
    assert work is not None and work.dispatch
    initial = work.request
    old = datetime.now(UTC) - timedelta(days=2)
    async with postgres.session() as session:
        history = Batch(
            queue_id=queue.id,
            request_key="historical",
            base_sha=work.batch.base_sha,
            policy={},
            deadline=old,
            max_attempts=101,
            max_plans=0,
            state="invalidated",
            finished_at=old,
        )
        session.add(history)
        await session.flush()
        for number in range(1, 102):
            attempt = Attempt(
                batch_id=history.id,
                request_key=str(number),
                number=number,
                deadline=old,
                state="timed_out",
                finished_at=old,
            )
            attempt.created_at = old
            session.add(attempt)
            await session.flush()
            session.add(
                Evidence(
                    attempt_id=attempt.id,
                    key="native-request",
                    reference=initial.model_copy(
                        update={
                            "batch_id": history.id,
                            "attempt_id": attempt.id,
                            "candidate_sha": "b" * 40,
                        }
                    ).model_dump(mode="json"),
                )
            )
    await queue.record_evidence(
        work.attempt.id, "workflow-policy", {"revision": 1, "digest": "a" * 64}, lease
    )
    restarted = await MergeQueue.load(queue.id, queue.bound_routing_id)
    assert restarted is not None

    queue = restarted
    tuned = policy.model_copy(
        update={
            "max_batch_size": 2,
            "collection_seconds": 0,
            "deadline_seconds": 60,
            "max_attempts": 1,
            "validation": policy.validation.model_copy(update={"deadline_seconds": 60}),
        }
    )
    duplicate = await queue.prepare_native_validation(
        native_snapshot((1, 2, 3, 4, 5, 6)), 600, lease, policy=tuned, trust="trusted-policy"
    )
    assert duplicate is not None and not duplicate.dispatch and duplicate.request == initial
    assert duplicate.batch.deadline == work.batch.deadline
    assert duplicate.attempt.deadline == work.attempt.deadline
    await queue.finish_native_validation(
        duplicate, {"error": "B breaks smoke"}, lease, succeeded=False
    )
    assert (
        await queue.prepare_native_validation(
            native_snapshot((1, 2, 3, 4, 5, 6)), 600, lease, policy=policy, trust="trusted-policy"
        )
        is None
    )
    with pytest.raises(RuntimeError, match="lost report"):
        async with queue.native_check_update(native_snapshot((1, 2, 3, 4, 5, 6)), lease) as pending:
            assert pending is not None and pending.state == "recovering"
            assert pending.envelope.contract == initial
            assert pending.envelope.workflow_policy_revision == 1
            raise RuntimeError("lost report")
    restarted = await MergeQueue.load(queue.id, queue.bound_routing_id)
    assert restarted is not None
    queue = restarted
    async with queue.native_check_update(native_snapshot((1, 2, 3, 4, 5, 6)), lease) as replay:
        assert replay == pending
    async with queue.native_check_update(native_snapshot((1, 2, 3, 4, 5, 6)), lease) as repeated:
        assert repeated is None
    async with postgres.session() as session:
        cancelled = await session.execute(
            select(func.count()).select_from(Evidence).where(Evidence.key == "check-cancelled")
        )
        assert cancelled.scalar_one() > 1
    recovered = await queue.prepare_native_validation(
        native_snapshot((1, 3, 4, 5, 6)), 600, lease, policy=tuned, trust="trusted-policy"
    )
    assert recovered is not None and recovered.dispatch
    assert recovered.batch.id == initial.batch_id
    assert recovered.batch.deadline == work.batch.deadline
    assert recovered.batch.max_attempts == policy.max_attempts
    assert recovered.attempt.deadline > recovered.attempt.created_at + timedelta(seconds=100)
    assert recovered.request.candidate_sha == list(native_snapshot((1, 3)).candidates)[-1]
    assert recovered.request.attempt_id != initial.attempt_id
    await queue.bind_validation_run(recovered.attempt.id, 42, lease)
    await queue.finish_native_validation(
        recovered,
        {
            **recovered.request.model_dump(mode="json"),
            "run_id": 42,
            "run_attempt": 1,
            "tested_sha": recovered.request.candidate_sha,
        },
        lease,
        succeeded=True,
    )
    assert await queue.authorize_native_result(recovered, native_snapshot((1, 3, 4, 5, 6)), lease)
    await queue.record_native_landing(recovered, lease)
    next_work = await queue.prepare_native_validation(
        native_snapshot((4, 5, 6), main=recovered.request.candidate_sha, age=700),
        600,
        lease,
        policy=tuned,
        trust="trusted-policy",
    )
    assert next_work is not None and next_work.dispatch
    assert next_work.batch.id != initial.batch_id
    assert (
        next_work.request.candidate_sha
        == list(native_snapshot((4, 5), main=recovered.request.candidate_sha).candidates)[-1]
    )
    assert next_work.batch.max_attempts == 1
    history = await queue.history(initial.batch_id)
    assert history.batch.state == "landed"
    assert [attempt.state for attempt in history.attempts] == ["failed", "succeeded"]
    async with queue.native_check_update(
        native_snapshot((4, 5, 6), main=recovered.request.candidate_sha), lease
    ) as cleanup:
        assert cleanup is not None and cleanup.state == "cancelled"
        assert cleanup.envelope == pending.envelope
    assert (await queue.history(initial.batch_id)).batch.state == "landed"


@pytest.mark.parametrize(
    ("cap", "age", "selected"), [(1, 0, True), (3, 0, False), (3, 90, True), (3, 700, True)]
)
async def test_native_partial_batches_obey_collection_without_expiring_backlog(
    queue, cap, age, selected
):
    policy, lease, _ = await enable_native_validation(queue, cap=cap)
    work = await queue.prepare_native_validation(
        native_snapshot((1,), age=age), 600, lease, policy=policy, trust="trusted-policy"
    )
    assert (work is not None) == selected


@pytest.mark.parametrize(
    "fault",
    [
        "head",
        "review",
        "base",
        "pause",
        "deadline",
        "lease",
        "batch_deadline",
        "rewound_main",
        "deferred",
    ],
)
async def test_native_success_cannot_authorize_stale_or_paused_work(queue, fault):
    policy, lease, operator = await enable_native_validation(queue)
    snapshot = native_snapshot()
    work = await queue.prepare_native_validation(
        snapshot,
        600,
        lease,
        policy=policy,
        trust="trusted-policy",
        allow_main_advance=fault in {"rewound_main", "deferred"},
    )
    assert work is not None
    await queue.bind_validation_run(work.attempt.id, 42, lease)
    await queue.finish_native_validation(
        work,
        {
            **work.request.model_dump(mode="json"),
            "run_id": 42,
            "run_attempt": 1,
            "tested_sha": work.request.candidate_sha,
        },
        lease,
        succeeded=True,
    )
    if fault in {"head", "review"}:
        assert snapshot.queue is not None
        first, *rest = snapshot.queue.entries.nodes
        pull = first.pull.model_copy(
            update={"head_sha": "f" * 40}
            if fault == "head"
            else {"review_decision": "REVIEW_REQUIRED"}
        )
        snapshot = snapshot.model_copy(
            update={
                "queue": snapshot.queue.model_copy(
                    update={
                        "entries": snapshot.queue.entries.model_copy(
                            update={"nodes": (first.model_copy(update={"pull": pull}), *rest)}
                        )
                    }
                )
            }
        )
    elif fault == "base":
        snapshot = native_snapshot(main="f" * 40)
    elif fault == "pause":
        await queue.set_paused(True, actor=operator.id)
    elif fault in {"deadline", "batch_deadline"}:
        async with postgres.session() as session:
            row = Attempt if fault == "deadline" else Batch
            await session.execute(
                update(row)
                .where(row.id == (work.attempt.id if fault == "deadline" else work.batch.id))
                .values(deadline=func.clock_timestamp() - timedelta(seconds=1))
            )
        assert (
            await queue.prepare_native_validation(
                native_snapshot((1, 2, 3, 4)), 600, lease, policy=policy, trust="trusted-policy"
            )
            is None
        )
        assert (await queue.history(work.batch.id)).batch.state == "active"
    elif fault == "lease":
        await queue.release(lease)
        await claim(queue)
        with pytest.raises(LeaseLost):
            await queue.authorize_native_result(work, snapshot, lease)
        return
    if fault == "deferred":
        await queue.record_evidence(work.attempt.id, "dispatch-policy-deferred", {}, lease)
        work = (await queue.native_validation_history(lease))[0]
        snapshot = native_snapshot(main="d" * 40)
    assert not await queue.authorize_native_result(
        work, snapshot, lease, main_advance_from="a" * 40 if fault == "deferred" else None
    )


@pytest.mark.parametrize(
    "fault",
    [
        "replay",
        "slow_dispatch",
        "dispatch_tuning",
        "dispatch_response_lost",
        "dispatch_policy_mismatch",
        "rejected_dispatch_cleanup",
        "report_policy_mismatch",
        "policy_discovery",
        "policy_requires_recovery",
        "policy_adoption_after_success",
        "policy_adoption_with_main_advance",
        "policy_revokes_main_advance",
        "rerun_after_success",
        "validation_failed",
        "report_rejected",
        "issuer",
        "capacity",
        "timeout",
        "allgreen",
        "main_advance",
    ],
)
async def test_native_runtime_recovers_publication_without_revoking_verified_success(
    queue: MergeQueue, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    import hmac
    import json

    from pydantic import SecretStr

    from openswe.github.queue import Artifact, ValidationJob, ValidationRun
    from openswe.merge_queue import dispatch
    from openswe.merge_queue.native import NativeQueueRuntime
    from openswe.merge_queue.validation_contract import ValidationRequest

    policy, lease, _ = await enable_native_validation(queue)
    await queue.release(lease)
    config = QueueConfiguration(
        routing_id=queue.bound_routing_id,
        repository="acme/widgets",
        repository_id=42,
        mode="native",
        dispatch_url="https://dispatcher.example/api/v1/merge-queue/dispatch",
        workflow_digest=hashlib.sha256(b"reviewed workflow").hexdigest(),
        check_app_id=456,
        operator_emails=frozenset({"operator@example.com"}),
        policy=policy,
    )
    config = config.model_copy(update={"sync_workflow_policy": True, "workflow_policy_revision": 1})
    if fault in {
        "main_advance",
        "policy_adoption_with_main_advance",
        "policy_revokes_main_advance",
    }:
        config = config.model_copy(update={"allow_main_advance": True})
    await save_configuration(config)
    repo = Mock(spec=RepoClient)
    repo.raw_file = AsyncMock(return_value=b"reviewed workflow")
    service_digest = "9546f5ca226a05e2efc02fd3be6d2af71e638fad5092d15df3283382b3986515"
    repo.validation_source_digest = AsyncMock(return_value=service_digest)
    repo.native_queue = AsyncMock(return_value=native_snapshot((1, 2, 3, 4, 5, 6)))
    repo.branch_rules = AsyncMock(
        return_value=[
            {
                "type": "merge_queue",
                "parameters": {
                    "merge_method": "MERGE",
                    "grouping_strategy": "HEADGREEN",
                    "max_entries_to_build": 3,
                    "max_entries_to_merge": 3,
                    "min_entries_to_merge": 1,
                    "check_response_timeout_minutes": 10,
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [{"context": "merge-queue", "integration_id": 456}],
                },
            },
        ]
    )
    sent: list[httpx2.Request] = []
    report_status = 502
    policy_revision = 2
    tuned = config.model_copy(update={"policy": policy.model_copy(update={"max_batch_size": 2})})

    async def deliver(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/workflow-policy"):
            body = json.dumps(
                {
                    "nonce": request.url.params["nonce"],
                    "repository": config.repository,
                    "repository_id": config.repository_id,
                    "workflow_digest": service_digest,
                    "workflow_policy_revision": policy_revision,
                    "allow_test_exceptions": fault == "policy_requires_recovery",
                    "validation_suite": "services",
                    "dispatch_actor_id": 123,
                    "check_app_id": 456,
                }
            ).encode()
            return httpx2.Response(
                200,
                content=body,
                headers={
                    "X-Merge-Queue-Signature": hmac.digest(
                        b"s" * 32, b"workflow-policy-response:" + body, "sha256"
                    ).hex()
                },
            )
        sent.append(request)
        report = request.url.path.endswith("/check-reports")
        prefix = b"check-report:" if report else b""
        assert (
            request.headers["X-Merge-Queue-Signature"]
            == hmac.digest(b"s" * 32, prefix + request.content, "sha256").hex()
        )
        mismatch = (fault == "dispatch_policy_mismatch" and len(sent) == 1) or (
            fault == "report_policy_mismatch" and len(sent) == 2
        )
        if mismatch:
            return httpx2.Response(
                412,
                headers={
                    "X-Merge-Queue-Policy-Signature": hmac.digest(
                        b"s" * 32, b"policy-mismatch:" + request.content, "sha256"
                    ).hex()
                },
            )
        if report:
            body = json.loads(request.content)
            assert body["dispatch_body"].encode() in {
                item.content for item in sent if item.url.path.endswith("/dispatch")
            }
            if (
                fault == "rejected_dispatch_cleanup"
                and body["dispatch_body"].encode() == sent[0].content
            ):
                return httpx2.Response(409)
            if body["state"] in {"recovering", "cancelled"}:
                return httpx2.Response(report_status)
            if body["state"] == "success":
                return httpx2.Response(report_status)
        elif fault == "rejected_dispatch_cleanup" and len(sent) == 1:
            return httpx2.Response(409)
        elif fault == "slow_dispatch":
            await asyncio.sleep(6)
        elif fault == "dispatch_tuning":
            await save_configuration(tuned)
        elif fault == "dispatch_response_lost":
            raise httpx2.ReadTimeout("Dispatch response lost", request=request)
        return httpx2.Response(204)

    monkeypatch.setattr(dispatch, "pooled_transport", lambda: httpx2.MockTransport(deliver))

    @asynccontextmanager
    async def open_repo(_queue: MergeQueue) -> AsyncIterator[RepoClient]:
        yield repo

    async def reconcile() -> None:
        if fault in {"policy_discovery", "policy_requires_recovery"}:
            monkeypatch.setenv("MERGE_QUEUE_DISPATCH_SECRET", "s" * 32)
            monkeypatch.setattr(QueueController, "open_repo", lambda self, queue: open_repo(queue))
            controller = QueueController(config)
            controller.queue = queue
            await controller.reconcile(
                QueueJob(queue_id=queue.id, routing_id=queue.bound_routing_id)
            )
            return
        worker = NativeQueueRuntime(
            queue.id,
            42,
            open_repo,
            config=config,
            dispatcher=DispatchGateway(str(config.dispatch_url), SecretStr("s" * 32)),
        )
        await worker.reconcile(QueueJob(queue_id=queue.id, routing_id=queue.bound_routing_id))

    if fault == "policy_requires_recovery":
        await reconcile()
        assert await QueueConfiguration.load(queue.bound_routing_id) == config
        assert sent == []
        return
    if fault in {"issuer", "capacity", "timeout", "allgreen"}:
        rules = repo.branch_rules.return_value
        if fault == "issuer":
            rules[1]["parameters"]["required_status_checks"][0]["integration_id"] = 999
        elif fault == "capacity":
            rules[0]["parameters"]["max_entries_to_merge"] = 2
        elif fault == "timeout":
            rules[0]["parameters"]["check_response_timeout_minutes"] = 2
        else:
            rules[0]["parameters"]["grouping_strategy"] = "ALLGREEN"
        with pytest.raises(ValueError):
            await reconcile()
        assert sent == []
        return
    if fault == "dispatch_tuning":
        with pytest.raises(LeaseLost, match="configuration changed"):
            await reconcile()
        config = tuned
        assert len(sent) == 1
    elif fault == "dispatch_response_lost":
        with pytest.raises(httpx2.ReadTimeout):
            await reconcile()
    elif fault == "report_policy_mismatch":
        with pytest.raises(WorkflowPolicyMismatch):
            await reconcile()
    elif fault == "dispatch_policy_mismatch":
        await reconcile()
        await reconcile()
        assert sent[0].content == sent[1].content
    else:
        await reconcile()
    if fault == "policy_discovery":
        assert json.loads(sent[0].content)["workflow_policy_revision"] == 2
        configured = await QueueConfiguration.load(queue.bound_routing_id)
        assert configured is not None and configured.workflow_policy_revision == 2
        assert configured.policy.validation.suite == "services"
        assert json.loads(sent[0].content)["contract"]["base_sha"] == "a" * 40
        assert json.loads(sent[0].content)["contract"]["version"] == 2
        count = len(sent)
        repo.validation_source_digest.return_value = "e" * 64
        with pytest.raises(ValueError, match="Queue workflow changed"):
            await reconcile()
        assert len(sent) == count
        repo.validation_source_digest.return_value = service_digest
        policy_revision = 1
        count = len(sent)
        await reconcile()
        assert len(sent) == count
        assert await QueueConfiguration.load(queue.bound_routing_id) == configured
        policy_revision = 2
    if fault == "rejected_dispatch_cleanup":
        rejected = ValidationRequest.model_validate(json.loads(sent[0].content)["contract"])
        # Older rejected attempts lack the marker that suppresses receipt-less cleanup.
        async with postgres.session() as session:
            await session.execute(
                delete(Evidence).where(
                    Evidence.attempt_id == rejected.attempt_id, Evidence.key == "dispatch-rejected"
                )
            )
        repo.native_queue.return_value = native_snapshot((1, 3, 4, 5, 6))
        await reconcile()
    dispatched = [r for r in sent if r.url.path.endswith("/dispatch")]
    request = ValidationRequest.model_validate(json.loads(dispatched[-1].content)["contract"])
    run = ValidationRun(
        id=42,
        actor_id=123,
        triggering_actor_id=123,
        repository=request.repository,
        head_repository=request.repository,
        head_branch="main",
        head_sha=request.workflow_sha,
        path=".github/workflows/merge_queue_validate.yml",
        event="workflow_dispatch",
        display_title=request.title,
        run_attempt=1,
        status="completed",
        conclusion="success",
    )
    repo.validation_runs = AsyncMock(return_value=(run,))
    repo.validation_run = AsyncMock(return_value=run)
    repo.validation_jobs = AsyncMock(
        return_value=tuple(
            ValidationJob(
                id=index,
                run_id=42,
                run_attempt=1,
                name=name,
                status="completed",
                conclusion="success",
            )
            for index, name in enumerate((*request.expected_jobs, "candidate-result"), 1)
        )
    )
    repo.validation_artifacts = AsyncMock(
        return_value=(
            Artifact(
                id=7,
                name=f"merge-queue-result-{request.attempt_id}-1",
                expired=False,
            ),
        )
    )
    repo.artifact_json = AsyncMock(
        return_value={
            **request.model_dump(mode="json"),
            "run_id": 42,
            "run_attempt": 1,
            "tested_sha": request.candidate_sha,
        }
    )
    if fault == "main_advance":
        repo.native_queue.return_value = native_snapshot((1, 2, 3, 4, 5, 6), main="d" * 40)
        repo.merge_base = AsyncMock(
            side_effect=lambda base, head: (
                base if base in {request.workflow_sha, "d" * 40, "e" * 40} else request.workflow_sha
            )
        )
        repo.validation_run.return_value = run.model_copy(
            update={"status": "in_progress", "conclusion": None}
        )
        await reconcile()
        progress = json.loads(sent[-1].content)
        assert progress["state"] == "in_progress"
        assert progress["acceptance"]["base_sha"] == "d" * 40
        repo.validation_run.return_value = run
    if fault == "validation_failed":
        repo.validation_run.return_value = run.model_copy(update={"conclusion": "failure"})
        await reconcile()
        assert (await queue.history(request.batch_id)).attempts[0].state == "failed"
        assert json.loads(sent[-1].content)["state"] == "recovering"
        recovery_report = sent[-1].content
        report_status = 204
        await reconcile()
        assert sent[-1].content == recovery_report
        before = len(sent)
        await reconcile()
        assert len(sent) == before
        report_status = 502
        repo.native_queue.return_value = native_snapshot((1, 3, 4, 5, 6))
        await reconcile()
        history = await queue.history(request.batch_id)
        assert [attempt.state for attempt in history.attempts] == ["failed", "running"]
        assert json.loads(sent[-1].content)["state"] == "cancelled"
        assert all(
            json.loads(r.content)["state"] != "success"
            for r in sent
            if r.url.path.endswith("/check-reports")
        )
        return
    with pytest.raises(httpx2.HTTPStatusError):
        await reconcile()
    assert (await queue.history(request.batch_id)).attempts[-1].state == "succeeded"
    report_status = 409 if fault == "report_rejected" else 204
    if fault in {"rerun_after_success", "report_rejected"}:
        repo.validation_run.return_value = run.model_copy(update={"run_attempt": 2})
    if fault == "report_rejected":
        with pytest.raises(httpx2.HTTPStatusError):
            await reconcile()
    else:
        await reconcile()
    history = await queue.history(request.batch_id)
    assert history.attempts[-1].state == "succeeded"
    assert all(evidence.key != "validation-revoked" for evidence in history.evidence)
    assert len([r for r in sent if r.url.path.endswith("/dispatch")]) == (
        2 if fault in {"dispatch_policy_mismatch", "rejected_dispatch_cleanup"} else 1
    )
    successes = [
        r
        for r in sent
        if r.url.path.endswith("/check-reports") and json.loads(r.content)["state"] == "success"
    ]
    assert len(successes) == 2
    assert successes[0].content == successes[1].content

    if fault == "main_advance":
        original = json.loads(successes[0].content)
        assert original["acceptance"]["base_sha"] == "d" * 40
        repo.native_queue.return_value = native_snapshot((1, 2, 3, 4, 5, 6), main="e" * 40)
        await reconcile()
        accepted = json.loads(sent[-1].content)["acceptance"]
        assert accepted["base_sha"] == "e" * 40
        assert accepted["candidate_sha"] != original["acceptance"]["candidate_sha"]
        assert len([r for r in sent if r.url.path.endswith("/dispatch")]) == 1
        repo.native_queue.return_value = native_snapshot((), main=accepted["candidate_sha"])
        repo.merge_base = AsyncMock(
            side_effect=lambda base, head: (
                base if base == accepted["candidate_sha"] else request.workflow_sha
            )
        )
        await reconcile()
        assert (await queue.history(request.batch_id)).batch.state == "landed"

    if fault in {
        "policy_adoption_after_success",
        "policy_adoption_with_main_advance",
        "policy_revokes_main_advance",
    }:
        from openswe.merge_queue.dispatch import WorkflowPolicy

        config = config.model_copy(update={"policy": tuned.policy})
        await save_configuration(config)
        config = await queue.adopt_workflow_policy(
            config,
            WorkflowPolicy(
                nonce=uuid7(),
                repository=config.repository,
                repository_id=config.repository_id,
                workflow_policy_revision=2,
                workflow_digest=service_digest,
                dispatch_actor_id=123,
                check_app_id=456,
                validation_suite="services",
                allow_main_advance=fault == "policy_adoption_with_main_advance",
            ),
        )
        lease = await queue.claim(uuid7(), timedelta(minutes=1), config=config)
        assert lease is not None
        previous = (await queue.native_validation_history(lease))[0]
        assert not await queue.authorize_native_result(previous, native_snapshot(), lease)
        pending_rebuild = native_snapshot().model_copy(update={"main_sha": "e" * 40})
        assert (
            await queue.prepare_native_validation(
                pending_rebuild,
                600,
                lease,
                policy=policy,
                trust=config.native_trust,
                allow_main_advance=config.allow_main_advance,
            )
            is None
        )
        assert (await queue.history(request.batch_id)).batch.state == "active"
        await queue.release(lease)
        repo.native_queue.return_value = native_snapshot(main="e" * 40)
        repo.merge_base = AsyncMock(return_value=request.workflow_sha)
        await reconcile()
        replacement = await queue.history(request.batch_id)
        assert replacement.batch.deadline == history.batch.deadline
        assert replacement.batch.max_attempts == history.batch.max_attempts
        assert replacement.batch.candidate == history.batch.candidate
        assert [attempt.number for attempt in replacement.attempts] == [1, 2]
        replacement_request = next(
            json.loads(r.content)["contract"]
            for r in reversed(sent)
            if r.url.path.endswith("/dispatch")
        )
        assert replacement_request["version"] == 2
        assert replacement_request["base_sha"] == "e" * 40
        assert replacement.batch.policy["policy"]["max_batch_size"] == policy.max_batch_size
        repo.native_queue.return_value = native_snapshot((), main=request.candidate_sha)
        repo.merge_base = AsyncMock(return_value=request.candidate_sha)
        await reconcile()
        history = await queue.history(request.batch_id)
        assert history.batch.state == "landed"
        assert [attempt.state for attempt in history.attempts] == ["succeeded", "cancelled"]
        assert [
            r
            for r in sent
            if r.url.path.endswith("/check-reports") and json.loads(r.content)["state"] == "success"
        ] == successes


async def test_configuration_fences_stale_workers_and_serializes_check_publication(
    queue_controller: QueueController,
) -> None:
    queue = queue_controller.queue
    async with postgres.session() as session:
        await session.execute(
            update(Repository)
            .where(Repository.id == queue.repository_id)
            .values(full_name="Acme/Widgets")
        )
    policy, unused_lease, _ = await enable_native_validation(queue)
    await queue.release(unused_lease)
    config = queue_controller.config.model_copy(
        update={
            "mode": "native",
            "check_app_id": 456,
            "policy": policy,
        }
    )
    await save_configuration(config)
    lease = await queue.claim(uuid7(), timedelta(minutes=1), config=config)
    assert lease is not None
    work = await queue.prepare_native_validation(
        native_snapshot(), 600, lease, policy=policy, trust=config.native_trust
    )
    assert work is not None
    updated = config.model_copy(update={"policy": policy.model_copy(update={"max_batch_size": 2})})
    async with asyncio.timeout(10):
        with pytest.raises(TimeoutError):
            async with queue.native_publication(lease):
                change = asyncio.create_task(save_configuration(updated))
                done, _ = await asyncio.wait({change}, timeout=0.05)
                assert not done
                await asyncio.Event().wait()
        await change
    with pytest.raises(LeaseLost, match="configuration changed"):
        async with queue.native_publication(lease):
            pytest.fail("A stale controller could publish a check")
    with pytest.raises(LeaseLost, match="configuration changed"):
        await queue.finish_native_validation(work, {"error": "old worker"}, lease, succeeded=False)
    await queue.release(lease)
    with pytest.raises(LeaseLost, match="configuration changed"):
        await queue.claim(uuid7(), timedelta(minutes=1), config=config)
    current_lease = await queue.claim(uuid7(), timedelta(minutes=1), config=updated)
    assert current_lease is not None
    resumed = await queue.prepare_native_validation(
        native_snapshot(), 600, current_lease, policy=updated.policy, trust=updated.native_trust
    )
    assert resumed is not None and not resumed.dispatch
    assert resumed.request == work.request and resumed.batch.deadline == work.batch.deadline
    await queue.release(current_lease)
    changed_trust = updated.model_copy(update={"workflow_digest": "b" * 64})
    await save_configuration(changed_trust)
    new_lease = await queue.claim(uuid7(), timedelta(minutes=1), config=changed_trust)
    assert new_lease is not None
    history = await queue.history(work.batch.id)
    assert history.batch.state == "invalidated" and history.attempts[0].state == "cancelled"
    assert not await queue.authorize_native_result(work, native_snapshot(), new_lease)

    async with postgres.session() as session:
        await session.execute(
            update(RepositoryBinding)
            .where(RepositoryBinding.id == queue.bound_routing_id)
            .values(merge_queue_config={"repository": "invalid"})
        )
    with pytest.raises(LeaseLost, match="disabled or invalid"):
        async with queue.native_publication(new_lease):
            pytest.fail("Invalid configuration allowed check publication")
    await queue.release(new_lease)
    with pytest.raises(LeaseLost, match="disabled or invalid"):
        await queue.claim(uuid7(), timedelta(minutes=1), config=changed_trust)


@pytest.mark.parametrize("expiry", ["attempt", "batch", "superseded"])
async def test_native_check_expiry_does_not_cancel_live_policy_replacements(queue, expiry):
    policy, lease, operator = await enable_native_validation(queue)
    snapshot = native_snapshot()
    work = await queue.prepare_native_validation(
        snapshot, 600, lease, policy=policy, trust="trusted-policy"
    )
    assert work is not None
    async with postgres.session() as session:
        if expiry == "superseded":
            await session.execute(
                update(Batch)
                .where(Batch.id == work.batch.id)
                .values(
                    policy={
                        **work.batch.policy,
                        "superseded_attempts": 1,
                        "trust": "replacement-policy",
                    },
                )
            )
        row = Batch if expiry == "batch" else Attempt
        await session.execute(
            update(row)
            .where(row.id == (work.batch.id if row is Batch else work.attempt.id))
            .values(
                deadline=func.clock_timestamp() - timedelta(seconds=1),
            )
        )
    if expiry == "superseded":
        await queue.finish_native_validation(work, {}, lease, succeeded=False)
        replacement = await queue.prepare_native_validation(
            snapshot, 600, lease, policy=policy, trust="replacement-policy"
        )
        assert replacement is not None and replacement.attempt.id != work.attempt.id
    await queue.set_paused(True, actor=operator.id)
    async with queue.native_check_update(snapshot, lease) as cleanup:
        if expiry == "superseded":
            assert cleanup is None
        else:
            assert cleanup is not None and cleanup.state == "cancelled"
    if expiry == "superseded":
        async with queue.native_check_update(native_snapshot(()), lease) as cleanup:
            assert cleanup is not None and cleanup.state == "cancelled"
    assert (await queue.history(work.batch.id)).attempts[0].state == "timed_out"


class TestNativeReplacementCheck:
    @staticmethod
    @pytest.mark.parametrize("expired", [False, True])
    async def test_progress_recovery_and_cleanup_follow_every_target(queue, expired):
        policy, lease, _ = await enable_native_validation(queue)
        work = await queue.prepare_native_validation(
            native_snapshot(),
            600,
            lease,
            policy=policy,
            trust="trusted-policy",
            allow_main_advance=True,
        )
        assert work is not None
        targets = {work.request.candidate_sha}
        for main in ("d" * 40, "e" * 40):
            current = native_snapshot(main=main)
            async with queue.native_check_update(
                current, lease, main_advance_from="a" * 40
            ) as status:
                assert status is not None and status.state == "in_progress"
                assert status.envelope.contract == work.request
                assert status.acceptance is not None and status.acceptance.base_sha == main
                targets.add(status.acceptance.candidate_sha)
        assert len(targets) == 3
        async with queue.native_check_update(current, lease, main_advance_from="a" * 40) as replay:
            assert replay is None
        await queue.finish_native_validation(work, {}, lease, succeeded=False)
        async with queue.native_check_update(
            current, lease, main_advance_from="a" * 40
        ) as recovery:
            assert recovery is not None and recovery.state == "recovering"
            assert recovery.acceptance == status.acceptance
        if expired:
            async with postgres.session() as session:
                await session.execute(
                    update(Attempt)
                    .where(Attempt.id == work.attempt.id)
                    .values(deadline=func.clock_timestamp() - timedelta(seconds=1))
                )
        else:
            current = native_snapshot((), main="e" * 40)
        cancelled = set()
        for _ in targets:
            async with queue.native_check_update(
                current, lease, main_advance_from="a" * 40
            ) as cleanup:
                assert cleanup is not None and cleanup.state == "cancelled"
                cancelled.add(
                    cleanup.acceptance.candidate_sha
                    if cleanup.acceptance
                    else cleanup.envelope.contract.candidate_sha
                )
        assert cancelled == targets
        async with queue.native_check_update(
            current, lease, main_advance_from="a" * 40
        ) as complete:
            assert complete is None


class TestNativeRecovery:
    @pytest.mark.parametrize("change", ["reordered", "rewrite", "budget", "expired"])
    async def test_main_advance_does_not_bypass_membership_ancestry_or_budget(self, queue, change):
        policy, lease, _, initial, work, proposal = await self.failed(
            queue, allow_main_advance=True
        )
        if change == "budget":
            async with postgres.session() as session:
                await session.execute(
                    update(Batch).where(Batch.id == work.batch.id).values(max_attempts=1)
                )
        elif change == "expired":
            async with postgres.session() as session:
                await session.execute(
                    update(Attempt)
                    .where(Attempt.id == work.attempt.id)
                    .values(
                        state="running",
                        finished_at=None,
                        deadline=func.clock_timestamp() - timedelta(seconds=1),
                    )
                )
        current = native_snapshot(
            (2, 1, 3, 4, 5, 6) if change == "reordered" else (1, 2, 3, 4, 5, 6), main="d" * 40
        )
        assert (
            await queue.prepare_native_validation(
                current,
                600,
                lease,
                policy=policy,
                trust="trusted-policy",
                allow_main_advance=True,
                main_advance_from=None if change == "rewrite" else initial.main_sha,
            )
            is None
        )
        history = await queue.history(work.batch.id)
        assert history.batch.deadline == work.batch.deadline and len(history.attempts) == 1
        if change == "budget":
            decision = await queue.decide_native_recovery(
                proposal.model_copy(update={"action": "escalate"}),
                "recovery-thread",
                current,
                lease,
                trust="trusted-policy",
            )
            assert decision.status == "applied"
        elif change == "expired":
            assert history.attempts[0].state == "timed_out"
        else:
            assert history.batch.state == "invalidated"

    @staticmethod
    async def failed(
        queue: MergeQueue,
        *,
        bind: bool = True,
        allow_main_advance: bool = False,
    ) -> tuple[QueuePolicy, Lease, User, NativeQueueSnapshot, NativeWork, RecoveryProposal]:
        policy, lease, operator = await enable_native_validation(queue)
        snapshot = native_snapshot((1, 2, 3, 4, 5, 6))
        work = await queue.prepare_native_validation(
            snapshot,
            600,
            lease,
            policy=policy,
            trust="trusted-policy",
            allow_main_advance=allow_main_advance,
        )
        assert work is not None
        await queue.finish_native_validation(
            work, {"error": "test fixture failure"}, lease, succeeded=False
        )
        if bind:
            await queue.bind_native_recovery(work.batch.id, "recovery-thread", lease)
        proposal = RecoveryProposal(
            request_key=uuid7(),
            batch_id=work.batch.id,
            attempt_id=work.attempt.id,
            candidate_sha=work.request.candidate_sha,
            action="retry",
            reason="Transient runner failure",
        )
        return policy, lease, operator, snapshot, work, proposal

    async def test_test_exception_preserves_failure_and_cannot_follow_main(self, queue):
        _, lease, _, snapshot, work, retry = await self.failed(queue, allow_main_advance=True)
        await queue.record_evidence(work.attempt.id, "validation-run", {"run_id": 42}, lease)
        proposal = retry.model_copy(
            update={
                "action": "except_tests",
                "tests": ("cloud-tests/example.spec.ts: loads",),
                "reason": "The assertion expects a field removed by the documented API contract.",
                "suggested_fix": "Use the documented replacement field.",
            }
        )
        with pytest.raises(ValueError, match="enabled policy"):
            await queue.decide_native_recovery(
                proposal, "recovery-thread", snapshot, lease, trust="trusted-policy"
            )
        saved = await queue.decide_native_recovery(
            proposal,
            "recovery-thread",
            snapshot,
            lease,
            trust="trusted-policy",
            allow_test_exceptions=True,
        )
        restarted = await MergeQueue.load(queue.id, queue.bound_routing_id)
        assert restarted is not None
        work = (await restarted.native_validation_history(lease))[0]
        assert work.run_id == 42 and work.attempt.state == "succeeded"
        assert work.test_exception is not None
        assert proposal.reason in work.test_exception
        history = await restarted.history(work.batch.id)
        assert next(e.reference for e in history.evidence if e.key == "validation-result") == {
            "error": "test fixture failure"
        }
        assert (
            await restarted.decide_native_recovery(
                proposal,
                "recovery-thread",
                snapshot,
                lease,
                trust="trusted-policy",
                allow_test_exceptions=True,
            )
            == saved
        )
        advanced = snapshot.model_copy(update={"main_sha": "e" * 40})
        assert not await restarted.authorize_native_result(
            work, advanced, lease, main_advance_from=work.batch.base_sha
        )

    async def test_retry_is_fresh_idempotent_and_keeps_budgets(self, queue):
        policy, lease, _, snapshot, work, proposal = await self.failed(queue)
        saved = await queue.decide_native_recovery(
            proposal, "recovery-thread", snapshot, lease, trust="trusted-policy"
        )
        with pytest.raises(ValueError, match="bound-thread"):
            await queue.record_plan(work.batch.id, "forged", saved.model_dump(mode="json"), lease)
        restarted = await MergeQueue.load(queue.id, queue.bound_routing_id)
        assert restarted is not None
        retry = await restarted.prepare_native_validation(
            snapshot, 600, lease, policy=policy, trust="trusted-policy"
        )
        assert retry is not None and retry.dispatch
        assert retry.request.attempt_id != work.request.attempt_id
        assert retry.request.candidate_sha == work.request.candidate_sha
        assert retry.request.expected_jobs == work.request.expected_jobs
        assert retry.request.workflow_sha == work.request.workflow_sha
        assert retry.attempt.number == 2 and retry.batch.deadline == work.batch.deadline
        assert retry.batch.max_attempts == work.batch.max_attempts
        assert retry.batch.max_plans == work.batch.max_plans
        assert (
            await restarted.decide_native_recovery(
                proposal, "recovery-thread", snapshot, lease, trust="trusted-policy"
            )
            == saved
        )
        history = await restarted.history(work.batch.id)
        assert len(history.decisions) == 1 and len(history.attempts) == 2
        with pytest.raises(ValueError, match="different input"):
            await restarted.decide_native_recovery(
                proposal.model_copy(update={"reason": "changed"}),
                "recovery-thread",
                snapshot,
                lease,
                trust="trusted-policy",
            )
        with pytest.raises(ValueError, match="current failed"):
            await restarted.decide_native_recovery(
                proposal.model_copy(update={"request_key": uuid7()}),
                "recovery-thread",
                snapshot,
                lease,
                trust="trusted-policy",
            )
        with pytest.raises(PermissionError):
            await restarted.decide_native_recovery(
                proposal, "other-thread", snapshot, lease, trust="trusted-policy"
            )

    @pytest.mark.parametrize(
        "fault",
        [
            "candidate",
            "attempt",
            "head",
            "base",
            "attempt_budget",
            "plan_budget",
            "deadline",
            "lease",
            "policy",
        ],
    )
    async def test_stale_or_exhausted_recovery_changes_nothing(self, queue, fault):
        _, lease, _, snapshot, work, proposal = await self.failed(queue)
        if fault == "candidate":
            proposal = proposal.model_copy(update={"candidate_sha": "b" * 40})
        elif fault == "attempt":
            proposal = proposal.model_copy(update={"attempt_id": uuid7()})
        elif fault == "head":
            proposal = proposal.model_copy(
                update={
                    "action": "defer",
                    "member": RecoveryMember(entry_id="entry-2", number=2, head_sha="f" * 40),
                }
            )
        elif fault == "base":
            snapshot = native_snapshot(main="b" * 40)
        elif fault == "lease":
            await queue.release(lease)
        elif fault == "policy":
            history = await queue.history(work.batch.id)
            async with postgres.session() as session:
                await session.execute(
                    update(Batch)
                    .where(Batch.id == work.batch.id)
                    .values(policy={**history.batch.policy, "trust": "changed-policy"})
                )
        else:
            changes = (
                {"max_attempts": 1}
                if fault == "attempt_budget"
                else (
                    {"max_plans": 0}
                    if fault == "plan_budget"
                    else {"deadline": datetime.now(UTC) - timedelta(seconds=1)}
                )
            )
            async with postgres.session() as session:
                await session.execute(
                    update(Batch).where(Batch.id == work.batch.id).values(**changes)
                )
        with pytest.raises(ValueError):
            await queue.decide_native_recovery(
                proposal, "recovery-thread", snapshot, lease, trust="trusted-policy"
            )
        history = await queue.history(work.batch.id)
        assert not history.decisions and len(history.attempts) == 1
        assert "superseded_attempts" not in history.batch.policy

    @pytest.mark.parametrize(
        "outcome",
        [
            "removed",
            "lost_response",
            "publication_timeout",
            "rejected",
            "forbidden",
            "advanced_before",
            "advanced_after",
            "history_error",
            "ancestry_error",
        ],
    )
    async def test_deferral_through_gateway_requires_fresh_survivor_validation(
        self, queue, monkeypatch, outcome
    ):
        advancing = outcome in {
            "advanced_before",
            "advanced_after",
            "ancestry_error",
        }
        policy, lease, _, snapshot, work, proposal = await self.failed(
            queue, allow_main_advance=advancing
        )
        proposal = proposal.model_copy(
            update={
                "action": "defer",
                "member": RecoveryMember(entry_id="entry-2", number=2, head_sha=f"{2:040x}"),
            }
        )
        await queue.decide_native_recovery(
            proposal, "recovery-thread", snapshot, lease, trust="trusted-policy"
        )
        after = native_snapshot((1, 3, 4, 5, 6), main="d" * 40 if advancing else snapshot.main_sha)
        repo = Mock(spec=RepoClient)
        repo.native_queue = AsyncMock(
            side_effect=[snapshot, snapshot if outcome == "publication_timeout" else after]
        )
        repo.merge_base = AsyncMock(return_value=snapshot.main_sha)
        repo.branch_rules = AsyncMock(
            return_value=[
                {
                    "type": "merge_queue",
                    "parameters": {
                        "merge_method": "MERGE",
                        "grouping_strategy": "HEADGREEN",
                        "max_entries_to_build": 3,
                        "max_entries_to_merge": 3,
                        "min_entries_to_merge": 1,
                        "check_response_timeout_minutes": 10,
                    },
                },
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "required_status_checks": [
                            {"context": "merge-queue", "integration_id": 456}
                        ],
                    },
                },
            ]
        )
        gateway = Mock(spec=DispatchGateway)
        gateway.defer = AsyncMock(
            side_effect={
                "lost_response": httpx2.ReadTimeout("Response lost"),
                "publication_timeout": TimeoutError("Publication deadline expired"),
                "forbidden": DeferralForbidden("GitHub denied queue removal"),
            }.get(outcome)
        )
        if outcome == "rejected":
            request = httpx2.Request("POST", "https://dispatcher.example/deferrals")
            gateway.defer.side_effect = httpx2.HTTPStatusError(
                "Changed PR head",
                request=request,
                response=httpx2.Response(409, request=request),
            )
        runner = NativeQueueRuntime(
            queue.id,
            42,
            Mock(),
            config=Mock(
                spec=QueueConfiguration,
                native_trust="trusted-policy",
                check_app_id=456,
                allow_main_advance=advancing,
            ),
            dispatcher=gateway,
        )

        if outcome in {"history_error", "ancestry_error"}:
            with monkeypatch.context() as patch:
                patch.setattr(
                    queue if outcome == "history_error" else repo,
                    "history" if outcome == "history_error" else "merge_base",
                    AsyncMock(side_effect=RuntimeError("Lookup unavailable")),
                )
                assert await runner._recover(queue, repo, native_snapshot(main="d" * 40), lease)
            assert (await queue.history(work.batch.id)).decisions[0].plan["status"] == "escalated"
            assert await queue.pending_native_recovery(lease) is None
            assert (await queue.snapshot(limit=1))[0]
            gateway.defer.assert_not_awaited()
            return

        if outcome == "advanced_before":
            async with postgres.session() as session:
                await session.execute(
                    update(Batch).where(Batch.id == work.batch.id).values(max_plans=1)
                )
            after = native_snapshot((1, 2, 3, 4, 5, 6), main="d" * 40)
            assert not await runner._recover(queue, repo, after, lease)
            gateway.defer.assert_not_awaited()
            assert (await queue.history(work.batch.id)).decisions[0].plan["status"] == "superseded"
            replacement = await queue.prepare_native_validation(
                after,
                600,
                lease,
                policy=policy,
                trust="trusted-policy",
                allow_main_advance=True,
                main_advance_from=snapshot.main_sha,
            )
            assert replacement is not None and replacement.request.workflow_sha == after.main_sha
            assert replacement.batch.id == work.batch.id
            assert replacement.batch.deadline == work.batch.deadline
            async with queue.native_check_update(
                after, lease, main_advance_from=after.main_sha
            ) as cleanup:
                assert cleanup is not None and cleanup.state == "cancelled"
                assert cleanup.envelope.contract == work.request
                assert cleanup.acceptance is None
            async with queue.native_check_update(
                after, lease, main_advance_from=after.main_sha
            ) as pending:
                assert pending is None
            await queue.finish_native_validation(replacement, {}, lease, succeeded=False)
            retry = proposal.model_copy(
                update={
                    "request_key": uuid7(),
                    "attempt_id": replacement.attempt.id,
                    "candidate_sha": replacement.request.candidate_sha,
                }
            )
            saved = await queue.decide_native_recovery(
                retry, "recovery-thread", after, lease, trust="trusted-policy"
            )
            assert saved.status == "pending"
            return
        assert await runner._recover(queue, repo, snapshot, lease)

        gateway.defer.assert_awaited_once()
        args, kwargs = gateway.defer.call_args
        assert args[0].contract == work.request
        assert args[0].expires_at == work.attempt.deadline
        assert args[1:] == (proposal.request_key, proposal.member)
        assert kwargs == {"expires_at": work.batch.deadline}
        history = await queue.history(work.batch.id)
        if outcome in {"rejected", "forbidden"}:
            assert history.decisions[0].plan["status"] == "escalated"
            assert (
                "HTTPStatusError" if outcome == "rejected" else "GitHub denied queue removal"
            ) in history.decisions[0].plan["detail"]
            assert (await queue.snapshot(limit=1))[0]
            assert await queue.pending_native_recovery(lease) is None
            return
        if outcome == "publication_timeout":
            assert history.decisions[0].plan["status"] == "applying"
            assert not (await queue.snapshot(limit=1))[0]
            assert await runner._recover(queue, repo, after, lease)
            gateway.defer.assert_awaited_once()
            history = await queue.history(work.batch.id)
        assert history.decisions[0].plan["status"] == "applied"
        assert not (await queue.snapshot(limit=1))[0]
        assert await queue.pending_native_recovery(lease) is None
        replacement = await queue.prepare_native_validation(
            after,
            600,
            lease,
            policy=policy,
            trust="trusted-policy",
            allow_main_advance=advancing,
            main_advance_from=snapshot.main_sha if advancing else None,
        )
        assert replacement is not None and replacement.dispatch
        assert replacement.request.attempt_id != work.request.attempt_id
        assert replacement.request.candidate_sha != work.request.candidate_sha
        assert replacement.batch.deadline == work.batch.deadline

    @pytest.mark.parametrize(
        "outcome",
        ["removed", "still_present", "deadline", "requeued", "survivor_changed", "timeout"],
    )
    async def test_crashed_deferral_reconciles_without_removing_twice(self, queue, outcome):
        policy, lease, _, snapshot, work, proposal = await self.failed(queue)
        proposal = proposal.model_copy(
            update={
                "action": "defer",
                "member": RecoveryMember(entry_id="entry-2", number=2, head_sha=f"{2:040x}"),
            }
        )
        decision = await queue.decide_native_recovery(
            proposal, "recovery-thread", snapshot, lease, trust="trusted-policy"
        )
        await queue.start_native_deferral(decision, snapshot, lease)
        after = native_snapshot((1, 3, 4, 5, 6))
        if outcome in {"still_present", "deadline"}:
            after = snapshot
        elif outcome == "requeued":
            after = native_snapshot((1, 3, 4, 5, 6, 2))
            assert after.queue is not None
            nodes = list(after.queue.entries.nodes)
            nodes[-1] = nodes[-1].model_copy(update={"id": "new-entry-2"})
            after = after.model_copy(
                update={
                    "queue": after.queue.model_copy(
                        update={
                            "entries": after.queue.entries.model_copy(
                                update={"nodes": tuple(nodes)}
                            )
                        }
                    )
                }
            )
        elif outcome == "survivor_changed":
            after = native_snapshot((1, 4, 5, 6))
        if outcome in {"removed", "still_present", "deadline"}:
            waiting = await queue.settle_native_deferral(decision, snapshot, lease)
            assert waiting.status == "applying"
            assert not (await queue.snapshot(limit=1))[0]
        await queue.release(lease)
        restarted = await MergeQueue.load(queue.id, queue.bound_routing_id)
        assert restarted is not None
        lease = await claim(restarted)
        repo = Mock(spec=RepoClient)
        writer = Mock(
            side_effect=AssertionError("Must never repeat removal after an applying intent")
        )
        runner = NativeQueueRuntime(queue.id, 42, writer)
        if outcome in {"removed", "still_present", "deadline"}:
            assert await runner._recover(restarted, repo, snapshot, lease)
            pending = await restarted.pending_native_recovery(lease)
            assert pending is not None and pending.status == "applying"
            assert not (await restarted.snapshot(limit=1))[0]
        if outcome in {"still_present", "deadline"}:
            async with postgres.session() as session:
                if outcome == "still_present":
                    await session.execute(
                        update(Decision)
                        .where(Decision.batch_id == work.batch.id)
                        .values(updated_at=func.clock_timestamp() - timedelta(minutes=3))
                    )
                else:
                    await session.execute(
                        update(Batch)
                        .where(Batch.id == work.batch.id)
                        .values(deadline=func.clock_timestamp() - timedelta(seconds=1))
                    )
        if outcome == "timeout":
            await restarted.settle_native_deferral(
                decision, after, lease, error=str(TimeoutError())
            )
        else:
            assert await runner._recover(restarted, repo, after, lease)
        assert await restarted.pending_native_recovery(lease) is None
        history = await restarted.history(work.batch.id)
        status = history.decisions[0].plan["status"]
        if outcome == "removed":
            assert status == "applied"
            replacement = await restarted.prepare_native_validation(
                after, 600, lease, policy=policy, trust="trusted-policy"
            )
            assert replacement is not None and replacement.dispatch
            assert replacement.batch.id == work.batch.id
            assert replacement.batch.deadline == work.batch.deadline
            assert replacement.request.candidate_sha == list(after.candidates)[1]
            assert replacement.request.attempt_id != work.request.attempt_id
            await restarted.finish_native_validation(
                replacement, {"error": "second failure"}, lease, succeeded=False
            )
            second = proposal.model_copy(
                update={
                    "request_key": uuid7(),
                    "attempt_id": replacement.attempt.id,
                    "candidate_sha": replacement.request.candidate_sha,
                    "member": RecoveryMember(entry_id="entry-3", number=3, head_sha=f"{3:040x}"),
                }
            )
            decision = await restarted.decide_native_recovery(
                second, "recovery-thread", after, lease, trust="trusted-policy"
            )
            await restarted.start_native_deferral(decision, after, lease)
            remaining = native_snapshot((1, 4, 5, 6))
            assert (
                await restarted.settle_native_deferral(decision, remaining, lease)
            ).status == "applied"
            final = await restarted.prepare_native_validation(
                remaining, 600, lease, policy=policy, trust="trusted-policy"
            )
            assert final is not None and final.attempt.number == 3
            assert final.batch.deadline == work.batch.deadline
        else:
            assert status == "escalated"
            assert (await restarted.snapshot(limit=1))[0]
            assert (
                await restarted.prepare_native_validation(
                    after, 600, lease, policy=policy, trust="trusted-policy"
                )
                is None
            )

    async def test_human_resume_preserves_deadline(self, queue):
        policy, lease, operator, snapshot, work, proposal = await self.failed(queue)
        proposal = proposal.model_copy(update={"action": "pause"})
        await queue.decide_native_recovery(
            proposal, "recovery-thread", snapshot, lease, trust="trusted-policy"
        )
        assert (await queue.snapshot(limit=1))[0]
        assert (
            await queue.prepare_native_validation(
                snapshot, 600, lease, policy=policy, trust="trusted-policy"
            )
            is None
        )
        with pytest.raises(LeaseLost, match="paused"):
            async with queue.native_publication(lease):
                pytest.fail("Paused recovery must not publish success")
        retry = proposal.model_copy(update={"request_key": uuid7(), "action": "retry"})
        with pytest.raises(ValueError, match="operator resumption"):
            await queue.decide_native_recovery(
                retry, "recovery-thread", snapshot, lease, trust="trusted-policy"
            )
        await queue.set_paused(False, actor=operator.id)
        await queue.decide_native_recovery(
            retry, "recovery-thread", snapshot, lease, trust="trusted-policy"
        )
        next_work = await queue.prepare_native_validation(
            snapshot, 600, lease, policy=policy, trust="trusted-policy"
        )
        assert next_work is not None and next_work.batch.deadline == work.batch.deadline

    @pytest.mark.parametrize("expired", [False, True])
    async def test_outage_resume_survives_restart_without_reviving_expired_work(
        self, queue, expired
    ):
        policy, lease, _, snapshot, work, proposal = await self.failed(queue)
        await queue.decide_native_recovery(
            proposal.model_copy(update={"action": "pause"}),
            "recovery-thread",
            snapshot,
            lease,
            trust="trusted-policy",
        )
        history = await queue.history(work.batch.id)
        pause_id = history.decisions[-1].id
        if expired:
            async with postgres.session() as session:
                await session.execute(
                    update(Batch)
                    .where(Batch.id == work.batch.id)
                    .values(
                        deadline=datetime.now(UTC) - timedelta(seconds=1),
                        state="invalidated",
                        finished_at=datetime.now(UTC) - timedelta(days=100),
                    )
                )
            assert await queue.prune_history(datetime.now(UTC), lease) == 0
        restarted = await MergeQueue.load(queue.id, queue.bound_routing_id)
        assert restarted is not None
        await restarted.resume_native_recovery(
            work.batch.id,
            "recovery-thread",
            pause_id,
            "Service recovered",
            lease,
            trust="trusted-policy",
        )
        assert not (await restarted.snapshot(limit=1))[0]
        history = await restarted.history(work.batch.id)
        assert history.batch.policy["recovery_resumes"] == {str(pause_id): "Service recovered"}
        if expired:
            assert history.batch.state == "invalidated"
            assert "superseded_attempts" not in history.batch.policy
            assert len(history.attempts) == 1
        else:
            retried = await restarted.prepare_native_validation(
                snapshot,
                600,
                lease,
                policy=policy,
                trust="trusted-policy",
            )
            assert retried is not None and retried.dispatch and retried.attempt.number == 2
            assert retried.batch.deadline == work.batch.deadline
        # An idempotent resume cannot clear a subsequent human pause.
        async with postgres.session() as session:
            await session.execute(
                update(MergeQueue)
                .where(MergeQueue.id == queue.id)
                .values(
                    paused=True,
                    recovery_pause_id=None,
                )
            )
        await restarted.resume_native_recovery(
            work.batch.id,
            "recovery-thread",
            pause_id,
            "Service recovered",
            lease,
            trust="trusted-policy",
        )
        assert (await restarted.snapshot(limit=1))[0]

    @pytest.mark.parametrize("pause_request", ["repeat", "manual_hold", "new_pause"])
    async def test_operator_pause_preserves_recovery_unless_manual(self, queue, pause_request):
        _, lease, operator, snapshot, work, proposal = await self.failed(queue)
        await queue.decide_native_recovery(
            proposal.model_copy(update={"action": "pause"}),
            "recovery-thread",
            snapshot,
            lease,
            trust="trusted-policy",
        )
        pause_id = (await queue.history(work.batch.id)).decisions[-1].id
        with pytest.raises(PermissionError, match="bound recovery thread"):
            await queue.resume_native_recovery(
                work.batch.id,
                "another-thread",
                pause_id,
                "Healthy",
                lease,
                trust="trusted-policy",
            )
        operator = await User.get(operator.id)
        assert operator is not None
        access = QueueAccess(
            queue=queue,
            user=operator,
            repo=Mock(spec=RepoClient, can_write=AsyncMock(return_value=True)),
            operator=True,
        )
        if pause_request == "new_pause":
            await set_paused(PauseChange(paused=False), access)
        await set_paused(
            PauseChange(paused=True, manual_hold=pause_request == "manual_hold"), access
        )
        if pause_request == "repeat":
            await queue.resume_native_recovery(
                work.batch.id,
                "recovery-thread",
                pause_id,
                "Healthy",
                lease,
                trust="trusted-policy",
            )
            assert not (await queue.snapshot(limit=1))[0]
        else:
            with pytest.raises(PermissionError, match="operator or another incident"):
                await queue.resume_native_recovery(
                    work.batch.id,
                    "recovery-thread",
                    pause_id,
                    "Healthy",
                    lease,
                    trust="trusted-policy",
                )
            assert (await queue.snapshot(limit=1))[0]

    async def test_escalation_does_not_pause_other_candidates(self, queue):
        _, lease, _, snapshot, _, proposal = await self.failed(queue)
        await queue.decide_native_recovery(
            proposal.model_copy(update={"action": "escalate"}),
            "recovery-thread",
            snapshot,
            lease,
            trust="trusted-policy",
        )
        assert not (await queue.snapshot(limit=1))[0]

    async def test_recovery_binding_and_delivery_receipts_survive_restart(self, queue):
        _, lease, operator, _, work, _ = await self.failed(queue, bind=False)
        settings = RecoverySettings(channel_id="C123456", operator_id=operator.id)
        state = await queue.ensure_recovery_thread(work.batch.id, settings, lease)
        state = state.model_copy(update={"thread_ts": "123.456", "notices": {"event": "123.789"}})
        await queue.update_recovery_thread(work.batch.id, state, lease)
        restarted = await MergeQueue.load(queue.id, queue.bound_routing_id)
        assert restarted is not None
        assert await restarted.ensure_recovery_thread(work.batch.id, settings, lease) == state
        assert (await restarted.recovery_for_thread(state.thread_id)).batch.id == work.batch.id
        with pytest.raises(PermissionError):
            await restarted.recovery_for_thread("unbound-thread")
        with pytest.raises(PermissionError, match="operator changed"):
            await restarted.ensure_recovery_thread(
                work.batch.id,
                settings.model_copy(update={"operator_id": uuid7()}),
                lease,
            )

    async def test_expired_outage_keeps_one_durable_followup(self, queue):
        from openswe.jobs import queue as jobs

        _, lease, operator, snapshot, work, proposal = await self.failed(queue, bind=False)
        settings = RecoverySettings(channel_id="C123456", operator_id=operator.id)
        state = await queue.ensure_recovery_thread(work.batch.id, settings, lease)
        await queue.decide_native_recovery(
            proposal.model_copy(update={"action": "pause"}),
            state.thread_id,
            snapshot,
            lease,
            trust="trusted-policy",
        )
        async with postgres.session() as session:
            await session.execute(
                update(Batch)
                .where(Batch.id == work.batch.id)
                .values(
                    state="invalidated",
                    finished_at=datetime.now(UTC),
                    deadline=datetime.now(UTC) - timedelta(seconds=1),
                )
            )
        restarted = await MergeQueue.load(queue.id, queue.bound_routing_id)
        assert restarted is not None
        service = QueueRecovery(Mock(recovery=settings), restarted)
        history = await restarted.history(work.batch.id)
        state = state.model_copy(
            update={
                "delivered": await service.fingerprint(history),
                "followup_minutes": 30,
            }
        )
        await restarted.update_recovery_thread(work.batch.id, state, lease)
        await service.reconcile(lease)
        first = await jobs.get(f"{RECOVERY_JOB}:{work.batch.id}")
        assert first is not None
        assert first.due_at > datetime.now(UTC) + timedelta(minutes=29)
        await service.reconcile(lease)
        repeated = await jobs.get(f"{RECOVERY_JOB}:{work.batch.id}")
        assert repeated is not None and repeated.id == first.id and repeated.due_at == first.due_at

    async def test_requested_followup_survives_state_change_delivery(self, queue, monkeypatch):
        from openswe.jobs import queue as jobs

        _, lease, operator, _, work, _ = await self.failed(queue, bind=False)
        settings = RecoverySettings(channel_id="C123456", operator_id=operator.id)
        state = await queue.ensure_recovery_thread(work.batch.id, settings, lease)
        service = QueueRecovery(Mock(recovery=settings), queue)
        monkeypatch.setattr(
            service, "bound", AsyncMock(return_value=(await queue.history(work.batch.id), state))
        )
        context = AsyncMock()
        context.__aenter__.return_value = lease
        service.leased = Mock(return_value=context)
        await service.follow_up(5)
        key = f"{RECOVERY_JOB}:{work.batch.id}:followup"
        requested = await jobs.get(key)
        assert requested is not None and requested.payload["investigate"] is True
        assert requested.due_at > datetime.now(UTC) + timedelta(minutes=4)
        await service.schedule(work.batch.id)
        await service.schedule(work.batch.id, minutes=15, replace=True)
        retained = await jobs.get(key)
        assert retained is not None and retained.id == requested.id
        assert retained.due_at == requested.due_at and retained.payload["investigate"] is True
