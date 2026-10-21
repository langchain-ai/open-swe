"""Transactional queue storage; callers supply an already authorized workspace.

Every operation rechecks the repository binding. The binding and queue row are
locked in that order, serializing ordering, budgets and lease changes. No GitHub
calls, admission decisions or job registration live here.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Self
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import BigInteger, ForeignKey, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.github.pull_requests import PullRequest
from openswe.github.repositories import Repository
from openswe.merge_queue.models import (
    Attempt,
    AttemptResult,
    Batch,
    BatchMember,
    Decision,
    Evidence,
    Lease,
    MergeRequest,
    Record,
)
from openswe.workspaces.rows import WorkspaceRepositoryRow, WorkspaceRow


class LeaseLost(ValueError):
    """This controller no longer owns the queue."""


@dataclass(frozen=True)
class BatchHistory:
    batch: Batch
    members: list[BatchMember]
    attempts: list[Attempt]
    evidence: list[Evidence]
    decisions: list[Decision]


class MergeQueue(Record):
    __tablename__ = "merge_queue"

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey(WorkspaceRow.id))
    repository_id: Mapped[UUID] = mapped_column(ForeignKey(Repository.id))
    target_branch: Mapped[str]
    paused: Mapped[bool] = mapped_column(default=True)
    fence: Mapped[int] = mapped_column(BigInteger, default=0)
    lease_owner: Mapped[UUID | None] = mapped_column(default=None)
    lease_until: Mapped[datetime | None] = mapped_column(default=None)

    @classmethod
    async def register(
        cls,
        workspace_id: UUID,
        repository_id: UUID,
        target_branch: str,
        *,
        actor: UUID | None = None,
    ) -> Self:
        target_branch = target_branch.removeprefix("refs/heads/")
        if (
            not target_branch
            or target_branch.strip() != target_branch
            or target_branch.startswith("refs/")
        ):
            raise ValueError("Expected a branch name")
        async with postgres.session() as session:
            await cls._binding(session, workspace_id, repository_id)
            existing = await session.scalar(
                select(cls).where(
                    cls.repository_id == repository_id, cls.target_branch == target_branch
                )
            )
            if existing is not None:
                if existing.workspace_id != workspace_id:
                    raise ValueError(
                        "Queue belongs to another workspace; explicit handoff required"
                    )
                return existing
            queue = cls(
                workspace_id=workspace_id,
                repository_id=repository_id,
                target_branch=target_branch,
                created_by=actor,
                updated_by=actor,
            )
            session.add(queue)
            await session.flush()
            return queue

    @staticmethod
    async def _binding(session: AsyncSession, workspace_id: UUID, repository_id: UUID) -> None:
        binding = await session.scalar(
            select(WorkspaceRepositoryRow)
            .where(
                WorkspaceRepositoryRow.workspace_id == workspace_id,
                WorkspaceRepositoryRow.repository_id == repository_id,
            )
            .with_for_update()
        )
        if binding is None:
            raise ValueError("Repository is not bound to this workspace")

    @asynccontextmanager
    async def _locked(
        self, lease: Lease | None = None
    ) -> AsyncIterator[tuple[AsyncSession, Self, datetime]]:
        async with postgres.session() as session:
            await self._binding(session, self.workspace_id, self.repository_id)
            queue = await session.scalar(
                select(type(self))
                .where(
                    type(self).id == self.id,
                    type(self).workspace_id == self.workspace_id,
                    type(self).repository_id == self.repository_id,
                )
                .with_for_update()
            )
            if queue is None:
                raise ValueError("Queue not found")
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            if lease is not None and (
                queue.lease_owner != lease.owner
                or queue.fence != lease.fence
                or queue.lease_until is None
                or queue.lease_until <= now
            ):
                raise LeaseLost("Queue lease expired or was replaced")
            yield session, queue, now

    async def claim(self, owner: UUID, ttl: timedelta) -> Lease | None:
        if ttl <= timedelta(0):
            raise ValueError("Lease duration must be positive")
        async with self._locked() as (_, queue, now):
            if queue.lease_until is not None and queue.lease_until > now:
                return None
            queue.fence += 1
            queue.lease_owner, queue.lease_until = owner, now + ttl
            queue.updated_by = None
            return Lease(owner=owner, fence=queue.fence)

    async def renew(self, lease: Lease, ttl: timedelta) -> None:
        if ttl <= timedelta(0):
            raise ValueError("Lease duration must be positive")
        async with self._locked(lease) as (_, queue, now):
            queue.lease_until = now + ttl
            queue.updated_by = None

    async def release(self, lease: Lease) -> None:
        async with self._locked(lease) as (_, queue, _):
            queue.lease_owner = queue.lease_until = None
            queue.updated_by = None

    async def request_merge(
        self, pull_request_id: UUID, head_sha: str, request_key: str, *, actor: UUID | None = None
    ) -> MergeRequest:
        """Record intent. Replaying its key never replaces the subsequently observed head."""
        async with self._locked() as (session, queue, _):
            existing = await session.scalar(
                select(MergeRequest).where(
                    MergeRequest.queue_id == queue.id,
                    MergeRequest.request_key == request_key,
                )
            )
            if existing is not None:
                if existing.pull_request_id != pull_request_id:
                    raise ValueError("Request key was already used with different input")
                return existing
            pull = await session.scalar(
                select(PullRequest).where(
                    PullRequest.id == pull_request_id,
                    PullRequest.repository_id == queue.repository_id,
                )
            )
            if pull is None or pull.base_ref != queue.target_branch:
                raise ValueError("Pull request does not target this queue")
            active = await session.scalar(
                select(MergeRequest.id).where(
                    MergeRequest.queue_id == queue.id,
                    MergeRequest.pull_request_id == pull_request_id,
                    MergeRequest.finished_at.is_(None),
                )
            )
            if active is not None:
                raise ValueError("Pull request already has an active merge request")
            request = MergeRequest(
                queue_id=queue.id,
                pull_request_id=pull_request_id,
                head_sha=head_sha,
                request_key=request_key,
                created_by=actor,
                updated_by=actor,
            )
            session.add(request)
            await session.flush()
            return request

    async def requests(self) -> list[MergeRequest]:
        async with self._locked() as (session, queue, _):
            return list(
                await session.scalars(
                    select(MergeRequest)
                    .where(
                        MergeRequest.queue_id == queue.id,
                        MergeRequest.finished_at.is_(None),
                    )
                    .order_by(
                        MergeRequest.priority.desc(), MergeRequest.created_at, MergeRequest.id
                    )
                )
            )

    async def _request(self, session: AsyncSession, request_id: UUID) -> MergeRequest:
        request = await session.scalar(
            select(MergeRequest).where(
                MergeRequest.queue_id == self.id,
                MergeRequest.id == request_id,
            )
        )
        if request is None:
            raise ValueError("Merge request not found")
        return request

    async def refresh_head(self, request_id: UUID, head_sha: str, lease: Lease) -> None:
        """A changed head keeps Auto merge intent but discards its admission and candidate."""
        async with self._locked(lease) as (session, _, now):
            request = await self._request(session, request_id)
            if request.finished_at is not None:
                raise ValueError("Request is no longer active")
            if request.head_sha == head_sha:
                return
            if request.state == "batched":
                batch = await self._active_batch(session)
                await self._invalidate(session, batch, now, None)
            request.head_sha, request.state, request.updated_by = (
                head_sha,
                "waiting_for_review",
                None,
            )

    async def admit(self, request_id: UUID, head_sha: str, lease: Lease) -> None:
        """Persist an admission already checked by the controller."""
        async with self._locked(lease) as (session, _, _):
            request = await self._request(session, request_id)
            if request.head_sha != head_sha:
                raise ValueError("Admission was checked against an obsolete head")
            if request.state not in ("waiting_for_review", "queued"):
                raise ValueError("Request is no longer awaiting admission")
            request.state, request.updated_by = "queued", None

    async def set_priority(
        self, request_id: UUID, priority: int, *, actor: UUID | None = None
    ) -> None:
        async with self._locked() as (session, _, _):
            request = await self._request(session, request_id)
            if request.state not in ("waiting_for_review", "queued"):
                raise ValueError("Only unbatched requests can be reordered")
            request.priority, request.updated_by = priority, actor

    async def withdraw(self, request_id: UUID, *, actor: UUID | None = None) -> None:
        async with self._locked() as (session, _, now):
            request = await self._request(session, request_id)
            if request.state == "withdrawn":
                return
            if request.state == "merged":
                raise ValueError("Merged requests cannot be withdrawn")
            if request.state == "batched":
                batch = await self._active_batch(session)
                await self._invalidate(session, batch, now, actor)
            request.state, request.finished_at, request.updated_by = "withdrawn", now, actor

    async def create_batch(
        self,
        request_ids: Sequence[UUID],
        *,
        request_key: str,
        base_sha: str,
        policy: dict[str, JsonValue],
        deadline: datetime,
        max_attempts: int,
        max_plans: int,
        lease: Lease,
    ) -> Batch:
        if not request_ids or len(set(request_ids)) != len(request_ids):
            raise ValueError("A batch needs distinct requests")
        if deadline.tzinfo is None or max_attempts < 1 or max_plans < 0:
            raise ValueError("Invalid batch deadline or budget")
        async with self._locked(lease) as (session, _, now):
            existing = await session.scalar(
                select(Batch).where(
                    Batch.queue_id == self.id,
                    Batch.request_key == request_key,
                )
            )
            if existing is not None:
                members = await session.scalars(
                    select(BatchMember.request_id)
                    .where(
                        BatchMember.batch_id == existing.id,
                    )
                    .order_by(BatchMember.position)
                )
                if (
                    list(members) != list(request_ids)
                    or existing.base_sha != base_sha
                    or existing.policy != policy
                    or existing.deadline != deadline
                    or existing.max_attempts != max_attempts
                    or existing.max_plans != max_plans
                ):
                    raise ValueError("Batch key was already used with different input")
                return existing
            if deadline <= now:
                raise ValueError("Batch deadline has passed")
            if await session.scalar(
                select(Batch.id).where(Batch.queue_id == self.id, Batch.state == "active")
            ):
                raise ValueError("Queue already has an active batch")
            requests = [await self._request(session, request_id) for request_id in request_ids]
            if any(request.state != "queued" for request in requests):
                raise ValueError("All batch members must be queued")
            batch = Batch(
                queue_id=self.id,
                request_key=request_key,
                base_sha=base_sha,
                policy=deepcopy(policy),
                deadline=deadline,
                max_attempts=max_attempts,
                max_plans=max_plans,
            )
            session.add(batch)
            await session.flush()
            for position, request in enumerate(requests):
                session.add(
                    BatchMember(
                        batch_id=batch.id,
                        request_id=request.id,
                        position=position,
                        head_sha=request.head_sha,
                    )
                )
                request.state, request.updated_by = "batched", None
            return batch

    async def _batch(self, session: AsyncSession, batch_id: UUID) -> Batch:
        batch = await session.scalar(
            select(Batch).where(Batch.id == batch_id, Batch.queue_id == self.id)
        )
        if batch is None:
            raise ValueError("Batch not found")
        return batch

    async def _active_batch(self, session: AsyncSession) -> Batch:
        return (
            await session.scalars(
                select(Batch).where(Batch.queue_id == self.id, Batch.state == "active")
            )
        ).one()

    async def invalidate(self, batch_id: UUID, lease: Lease) -> None:
        async with self._locked(lease) as (session, _, now):
            batch = await self._batch(session, batch_id)
            if batch.state == "invalidated":
                return
            if batch.state != "active":
                raise ValueError("Batch is no longer active")
            await self._invalidate(session, batch, now, None)

    async def _invalidate(
        self,
        session: AsyncSession,
        batch: Batch,
        now: datetime,
        actor: UUID | None,
    ) -> None:
        requests = await session.scalars(
            select(MergeRequest)
            .join(BatchMember)
            .where(
                MergeRequest.queue_id == self.id,
                BatchMember.batch_id == batch.id,
            )
        )
        for request in requests:
            request.state, request.updated_by = "queued", actor
        attempts = await session.scalars(
            select(Attempt).where(
                Attempt.batch_id == batch.id,
                Attempt.state == "running",
            )
        )
        for attempt in attempts:
            attempt.state, attempt.finished_at, attempt.updated_by = "cancelled", now, actor
        batch.state, batch.finished_at, batch.updated_by = "invalidated", now, actor

    async def start_attempt(
        self, batch_id: UUID, request_key: str, deadline: datetime, lease: Lease
    ) -> Attempt:
        async with self._locked(lease) as (session, _, now):
            batch = await self._batch(session, batch_id)
            existing = await session.scalar(
                select(Attempt).where(
                    Attempt.batch_id == batch.id,
                    Attempt.request_key == request_key,
                )
            )
            if existing is not None:
                if existing.deadline != deadline:
                    raise ValueError("Attempt key was already used with different input")
                return existing
            if (
                deadline.tzinfo is None
                or not now < deadline <= batch.deadline
                or batch.state != "active"
            ):
                raise ValueError("Batch is inactive or deadline is invalid")
            attempts = list(
                await session.scalars(select(Attempt).where(Attempt.batch_id == batch.id))
            )
            if any(attempt.state == "running" for attempt in attempts):
                raise ValueError("Batch already has a running attempt")
            if len(attempts) >= batch.max_attempts:
                raise ValueError("Attempt budget exhausted")
            attempt = Attempt(
                batch_id=batch.id,
                request_key=request_key,
                number=len(attempts) + 1,
                deadline=deadline,
            )
            session.add(attempt)
            await session.flush()
            return attempt

    async def _attempt(self, session: AsyncSession, attempt_id: UUID) -> Attempt:
        attempt = await session.scalar(
            select(Attempt)
            .join(Batch)
            .where(
                Attempt.id == attempt_id,
                Batch.queue_id == self.id,
            )
        )
        if attempt is None:
            raise ValueError("Attempt not found")
        return attempt

    async def finish_attempt(self, attempt_id: UUID, result: AttemptResult, lease: Lease) -> None:
        async with self._locked(lease) as (session, _, now):
            attempt = await self._attempt(session, attempt_id)
            if attempt.state == result:
                return
            if attempt.state != "running":
                raise ValueError("Attempt already finished with a different result")
            if result == "succeeded" and attempt.deadline <= now:
                raise ValueError("Expired attempt cannot succeed")
            attempt.state, attempt.finished_at, attempt.updated_by = result, now, None

    async def record_evidence(
        self,
        attempt_id: UUID,
        key: str,
        reference: dict[str, JsonValue],
        lease: Lease,
    ) -> Evidence:
        async with self._locked(lease) as (session, _, _):
            attempt = await self._attempt(session, attempt_id)
            existing = await session.scalar(
                select(Evidence).where(Evidence.attempt_id == attempt.id, Evidence.key == key)
            )
            if existing is not None:
                if existing.reference != reference:
                    raise ValueError("Evidence key was already used with different input")
                return existing
            evidence = Evidence(attempt_id=attempt.id, key=key, reference=deepcopy(reference))
            session.add(evidence)
            await session.flush()
            return evidence

    async def record_plan(
        self,
        batch_id: UUID,
        request_key: str,
        plan: dict[str, JsonValue],
        lease: Lease,
    ) -> Decision:
        """Save a proposal for deterministic validation; this authorizes no action."""
        async with self._locked(lease) as (session, _, now):
            batch = await self._batch(session, batch_id)
            existing = await session.scalar(
                select(Decision).where(
                    Decision.batch_id == batch.id,
                    Decision.request_key == request_key,
                )
            )
            if existing is not None:
                if existing.plan != plan:
                    raise ValueError("Plan key was already used with different input")
                return existing
            if batch.state != "active" or batch.deadline <= now:
                raise ValueError("Batch is inactive or expired")
            used = (
                await session.execute(
                    select(func.count()).select_from(Decision).where(Decision.batch_id == batch.id)
                )
            ).scalar_one()
            if used >= batch.max_plans:
                raise ValueError("Planning budget exhausted")
            decision = Decision(
                batch_id=batch.id,
                request_key=request_key,
                plan=deepcopy(plan),
                lease_owner=lease.owner,
                fence=lease.fence,
            )
            session.add(decision)
            await session.flush()
            return decision

    async def history(self, batch_id: UUID) -> BatchHistory:
        async with self._locked() as (session, _, _):
            batch = await self._batch(session, batch_id)
            return BatchHistory(
                batch=batch,
                members=list(
                    await session.scalars(
                        select(BatchMember)
                        .where(BatchMember.batch_id == batch.id)
                        .order_by(BatchMember.position)
                    )
                ),
                attempts=list(
                    await session.scalars(
                        select(Attempt).where(Attempt.batch_id == batch.id).order_by(Attempt.number)
                    )
                ),
                evidence=list(
                    await session.scalars(
                        select(Evidence)
                        .join(Attempt)
                        .where(Attempt.batch_id == batch.id)
                        .order_by(Evidence.id)
                    )
                ),
                decisions=list(
                    await session.scalars(
                        select(Decision).where(Decision.batch_id == batch.id).order_by(Decision.id)
                    )
                ),
            )

    async def prune_history(self, before: datetime, lease: Lease, *, limit: int = 100) -> int:
        """Bounded retention. Never delete live work or requests referenced by retained batches.

        Request keys remain idempotent for the retained history window.
        """
        if before.tzinfo is None or limit < 1 or limit > 1000:
            raise ValueError("Expected an aware cutoff and a limit between 1 and 1000")
        async with self._locked(lease) as (session, _, now):
            if before > now:
                raise ValueError("Retention cutoff cannot be in the future")
            batches = list(
                await session.scalars(
                    select(Batch.id)
                    .where(
                        Batch.queue_id == self.id,
                        Batch.finished_at < before,
                        ~select(Attempt.id)
                        .where(Attempt.batch_id == Batch.id, Attempt.state == "running")
                        .exists(),
                    )
                    .order_by(Batch.finished_at, Batch.id)
                    .limit(limit)
                )
            )
            await session.execute(
                delete(Batch).where(Batch.queue_id == self.id, Batch.id.in_(batches))
            )
            requests = list(
                await session.scalars(
                    select(MergeRequest.id)
                    .where(
                        MergeRequest.queue_id == self.id,
                        MergeRequest.finished_at < before,
                        ~select(BatchMember.id)
                        .where(BatchMember.request_id == MergeRequest.id)
                        .exists(),
                    )
                    .order_by(MergeRequest.finished_at, MergeRequest.id)
                    .limit(limit)
                )
            )
            await session.execute(
                delete(MergeRequest).where(
                    MergeRequest.queue_id == self.id, MergeRequest.id.in_(requests)
                )
            )
            return len(batches) + len(requests)
