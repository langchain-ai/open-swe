"""Transactional queue storage; callers supply an already authorized workspace.

Every operation rechecks the repository binding. Mutations lock the binding and
queue row in that order; status reads use a consistent, non-locking snapshot.
No GitHub calls, admission decisions or job registration live here.
"""

import base64
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Self
from uuid import UUID, uuid7

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import BigInteger, ForeignKey, delete, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.github.pull_requests import PullRequest
from openswe.github.repositories import Repository
from openswe.merge_queue.admission import ADMISSION_MAX_AGE, Admission, QueuePolicy
from openswe.merge_queue.git import Candidate
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
from openswe.merge_queue.validation_contract import ValidationDispatch, ValidationRequest
from openswe.workspaces.rows import WorkspaceRepositoryRow, WorkspaceRow


class LeaseLost(ValueError):
    """This controller no longer owns the queue."""


class QueueItemNotFound(ValueError):
    """The requested item does not exist in this queue."""


@dataclass(frozen=True)
class QueueEntry:
    request: MergeRequest
    number: int


class QueueCursor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    queue_id: UUID
    priority: int = Field(strict=True, ge=-(2**31), lt=2**31)
    created_at: AwareDatetime
    id: UUID

    def encode(self) -> str:
        return base64.urlsafe_b64encode(self.model_dump_json().encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, value: str, queue_id: UUID) -> Self:
        payload = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        cursor = cls.model_validate_json(payload)
        if cursor.queue_id != queue_id:
            raise ValueError("Cursor belongs to another queue")
        return cursor


@dataclass(frozen=True)
class BatchHistory:
    batch: Batch
    members: list[BatchMember]
    attempts: list[Attempt]
    evidence: list[Evidence]
    decisions: list[Decision]


@dataclass(frozen=True)
class ValidationWork:
    batch: Batch
    attempt: Attempt | None = None
    request: ValidationRequest | None = None
    run_id: int | None = None
    dispatch: bool = False


class MergeQueue(Record):
    __tablename__ = "merge_queue"

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey(WorkspaceRow.id, ondelete="CASCADE"))
    repository_id: Mapped[UUID] = mapped_column(ForeignKey(Repository.id))
    target_branch: Mapped[str]
    paused: Mapped[bool] = mapped_column(default=True)
    fence: Mapped[int] = mapped_column(BigInteger, default=0)
    lease_owner: Mapped[UUID | None] = mapped_column(default=None)
    lease_until: Mapped[datetime | None] = mapped_column(default=None)

    @classmethod
    async def load(cls, queue_id: UUID, workspace_id: UUID) -> Self | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(cls).where(cls.id == queue_id, cls.workspace_id == workspace_id)
            )

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

    @asynccontextmanager
    async def _read_snapshot(self) -> AsyncIterator[tuple[AsyncSession, Self]]:
        async with (
            postgres.snapshot_transaction() as conn,
            AsyncSession(bind=conn, expire_on_commit=False) as session,
        ):
            queue = await session.scalar(
                select(type(self))
                .join(
                    WorkspaceRepositoryRow,
                    (WorkspaceRepositoryRow.workspace_id == type(self).workspace_id)
                    & (WorkspaceRepositoryRow.repository_id == type(self).repository_id),
                )
                .where(
                    type(self).id == self.id,
                    type(self).workspace_id == self.workspace_id,
                    type(self).repository_id == self.repository_id,
                )
            )
            if queue is None:
                raise QueueItemNotFound("Queue not found or repository binding changed")
            yield session, queue

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
        self,
        pull_request_id: UUID,
        head_sha: str,
        request_key: str,
        *,
        actor: UUID | None = None,
        dependencies: tuple[int, ...] = (),
    ) -> MergeRequest:
        """Record intent. Replaying its key never replaces the subsequently observed head."""
        if len(set(dependencies)) != len(dependencies) or any(
            type(number) is not int or number <= 0 for number in dependencies
        ):
            raise ValueError("Dependencies must be distinct positive PR numbers")
        async with self._locked() as (session, queue, _):
            existing = await session.scalar(
                select(MergeRequest).where(
                    MergeRequest.queue_id == queue.id,
                    MergeRequest.request_key == request_key,
                )
            )
            if existing is not None:
                if existing.pull_request_id != pull_request_id or existing.dependencies != list(
                    dependencies
                ):
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
            entries = await self._entries(session)
            graph = {entry.number: entry.request.dependencies for entry in entries}
            graph[pull.number] = list(dependencies)
            pending, visited = list(dependencies), set()
            while pending:
                dependency = pending.pop()
                if dependency == pull.number:
                    raise ValueError("PR dependency cycle")
                if dependency not in visited:
                    visited.add(dependency)
                    pending.extend(graph.get(dependency, []))
            request = MergeRequest(
                queue_id=queue.id,
                pull_request_id=pull_request_id,
                head_sha=head_sha,
                request_key=request_key,
                dependencies=list(dependencies),
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
            raise QueueItemNotFound("Merge request not found")
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
            request.admission, request.queued_at = None, None
            request.blocked_reason, request.blocked_base_sha = None, None
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

    async def set_paused(self, paused: bool, *, actor: UUID) -> None:
        async with self._locked() as (_, queue, _):
            queue.paused, queue.updated_by = paused, actor

    async def withdraw(
        self, request_id: UUID, *, actor: UUID | None = None, operator: bool = False
    ) -> None:
        async with self._locked() as (session, _, now):
            request = await self._request(session, request_id)
            if actor is not None and request.created_by != actor and not operator:
                raise PermissionError("Only the requester or a queue operator may withdraw")
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
            return await self._save_batch(
                session,
                requests,
                request_key=request_key,
                base_sha=base_sha,
                policy=policy,
                deadline=deadline,
                max_attempts=max_attempts,
                max_plans=max_plans,
            )

    async def _save_batch(
        self,
        session: AsyncSession,
        requests: Sequence[MergeRequest],
        *,
        request_key: str,
        base_sha: str,
        policy: dict[str, JsonValue],
        deadline: datetime,
        max_attempts: int,
        max_plans: int,
    ) -> Batch:
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
            raise QueueItemNotFound("Batch not found")
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
            attempt.state = "timed_out" if attempt.deadline <= now else "cancelled"
            attempt.finished_at, attempt.updated_by = now, actor
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
            return await self._save_evidence(session, attempt, key, reference)

    async def _save_evidence(
        self, session: AsyncSession, attempt: Attempt, key: str, reference: dict[str, JsonValue]
    ) -> Evidence:
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

    async def advance_validation(
        self, policy: QueuePolicy, lease: Lease, *, start: UUID | None = None
    ) -> ValidationWork | None:
        """Expire stale state and return pending work, atomically starting an attempt if requested.

        Only the call that creates an attempt returns dispatch=True; subsequent calls
        resume discovery. Inactive, expired, or completed work returns None.
        """
        async with self._locked(lease) as (session, queue, now):
            batch = await session.scalar(
                select(Batch).where(Batch.queue_id == self.id, Batch.state == "active")
            )
            if batch is None or (start is not None and batch.id != start):
                return None
            if batch.policy != policy.model_dump(mode="json") or batch.deadline <= now:
                await self._invalidate(session, batch, now, None)
                return None
            validation = policy.validation
            if validation is None or batch.candidate is None or batch.published_at is None:
                return None
            if queue.target_branch != "main":
                raise ValueError("Validation workflow requires the main target branch")
            attempt = await session.scalar(
                select(Attempt)
                .where(Attempt.batch_id == batch.id)
                .order_by(Attempt.number.desc())
                .limit(1)
            )
            if attempt is not None:
                if attempt.state != "running":
                    return None
                if attempt.deadline <= now:
                    attempt.state, attempt.finished_at, attempt.updated_by = "timed_out", now, None
                    return None
                evidence = {
                    row.key: row.reference
                    for row in await session.scalars(
                        select(Evidence).where(Evidence.attempt_id == attempt.id)
                    )
                }
                saved = ValidationDispatch.model_validate(evidence.get("validation-request"))
                run_id = evidence.get("validation-run", {}).get("run_id")
                if run_id is not None and (type(run_id) is not int or run_id <= 0):
                    raise ValueError("Invalid saved validation run")
                if saved.policy_digest != policy.fingerprint:
                    raise ValueError("Saved validation policy changed")
                return ValidationWork(batch, attempt, saved.contract, run_id)
            if queue.paused or batch.candidate_error:
                return None
            if start is None:
                return ValidationWork(batch)
            await self._candidate_entries(session, batch, now)
            candidate = Candidate.model_validate(batch.candidate)
            attempt = Attempt(
                batch_id=batch.id,
                request_key="smoke-validation",
                number=1,
                deadline=min(batch.deadline, now + timedelta(seconds=validation.deadline_seconds)),
            )
            session.add(attempt)
            await session.flush()
            repository = (
                await session.scalars(
                    select(Repository.full_name).where(Repository.id == queue.repository_id)
                )
            ).one()
            request = ValidationRequest(
                repository=repository,
                batch_id=batch.id,
                attempt_id=attempt.id,
                candidate_sha=candidate.sha,
                workflow_sha=batch.base_sha,
                dispatch_actor_id=validation.dispatch_actor_id,
            )
            await self._save_evidence(
                session,
                attempt,
                "validation-request",
                ValidationDispatch(
                    contract=request,
                    policy_digest=policy.fingerprint,
                    lease_owner=lease.owner,
                    fence=lease.fence,
                ).model_dump(mode="json"),
            )
            return ValidationWork(batch, attempt, request, dispatch=True)

    async def bind_validation_run(self, attempt_id: UUID, run_id: int, lease: Lease) -> None:
        if type(run_id) is not int or run_id <= 0:
            raise ValueError("Invalid validation run")
        async with self._locked(lease) as (session, _, now):
            attempt = await self._attempt(session, attempt_id)
            batch = await self._batch(session, attempt.batch_id)
            if batch.state != "active" or attempt.state != "running" or attempt.deadline <= now:
                raise ValueError("Validation attempt is inactive or expired")
            await self._save_evidence(session, attempt, "validation-run", {"run_id": run_id})

    async def finish_validation(
        self, attempt_id: UUID, reference: dict[str, JsonValue], lease: Lease, *, succeeded: bool
    ) -> bool:
        async with self._locked(lease) as (session, _, now):
            attempt = await self._attempt(session, attempt_id)
            batch = await self._batch(session, attempt.batch_id)
            if batch.state != "active" or attempt.state != "running":
                return False
            if min(attempt.deadline, batch.deadline) <= now:
                attempt.state, attempt.finished_at, attempt.updated_by = "timed_out", now, None
                return False
            evidence = {
                row.key: row.reference
                for row in await session.scalars(
                    select(Evidence).where(Evidence.attempt_id == attempt.id)
                )
            }
            request = ValidationDispatch.model_validate(evidence.get("validation-request")).contract
            if any(
                reference.get(key) != value
                for key, value in request.model_dump(mode="json").items()
            ):
                raise ValueError("Validation evidence does not match its request")
            if succeeded:
                await self._candidate_entries(session, batch, now)
                run_id = evidence.get("validation-run", {}).get("run_id")
                if (
                    run_id is None
                    or reference.get("run_id") != run_id
                    or reference.get("run_attempt") != 1
                    or reference.get("tested_sha") != request.candidate_sha
                ):
                    raise ValueError("Validation evidence does not match its run")
            await self._save_evidence(
                session, attempt, "validation-result", {**reference, "coverage": "smoke"}
            )
            attempt.state = "succeeded" if succeeded else "failed"
            attempt.finished_at, attempt.updated_by = now, None
            return True

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
        async with self._read_snapshot() as (session, _):
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
                        (Batch.candidate.is_(None) | Batch.ref_deleted_at.is_not(None)),
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

    async def snapshot(
        self, *, limit: int, cursor: QueueCursor | None = None
    ) -> tuple[bool, list[QueueEntry], UUID | None]:
        async with self._read_snapshot() as (session, queue):
            entries = await self._entries(session, limit=limit, cursor=cursor)
            batch_id = await session.scalar(
                select(Batch.id).where(Batch.queue_id == queue.id, Batch.state == "active")
            )
            return queue.paused, entries, batch_id

    async def _entries(
        self, session: AsyncSession, *, limit: int | None = None, cursor: QueueCursor | None = None
    ) -> list[QueueEntry]:
        query = (
            select(MergeRequest, PullRequest.number)
            .join(PullRequest)
            .where(MergeRequest.queue_id == self.id, MergeRequest.finished_at.is_(None))
            .order_by(MergeRequest.priority.desc(), MergeRequest.created_at, MergeRequest.id)
            .limit(limit)
        )
        if cursor is not None:
            query = query.where(
                (MergeRequest.priority < cursor.priority)
                | (
                    (MergeRequest.priority == cursor.priority)
                    & (
                        tuple_(MergeRequest.created_at, MergeRequest.id)
                        > (cursor.created_at, cursor.id)
                    )
                )
            )
        rows = await session.execute(query)
        return [QueueEntry(request, number) for request, number in rows.tuples()]

    async def entries(self, lease: Lease) -> list[QueueEntry]:
        async with self._locked(lease) as (session, _, _):
            return await self._entries(session)

    async def repository_name(self, lease: Lease) -> str:
        async with self._locked(lease) as (session, _, _):
            return (
                await session.scalars(
                    select(Repository.full_name).where(Repository.id == self.repository_id)
                )
            ).one()

    async def refresh_admission(
        self,
        request_id: UUID,
        head_sha: str,
        admission: Admission | None,
        lease: Lease,
        *,
        reason: str | None = None,
        terminal: bool = False,
        merged: bool = False,
    ) -> None:
        async with self._locked(lease) as (session, _, now):
            request = await self._request(session, request_id)
            if request.finished_at is not None:
                return
            if request.head_sha != head_sha or (
                admission is not None and admission.head_sha != head_sha
            ):
                raise ValueError("Admission was checked against an obsolete head")
            previous = Admission.model_validate(request.admission) if request.admission else None
            changed = (
                previous is not None
                and admission is not None
                and previous.model_dump(exclude={"checked_at"})
                != admission.model_dump(exclude={"checked_at"})
            )
            if request.state == "batched" and (admission is None or changed):
                await self._invalidate(session, await self._active_batch(session), now, None)
            request.admission = admission.model_dump(mode="json") if admission else None
            if terminal or (
                admission is not None and request.blocked_base_sha != admission.base_sha
            ):
                request.blocked_base_sha = None
            if request.blocked_base_sha is None:
                request.blocked_reason = reason
            request.updated_by = None
            request.last_checked_at = now
            if terminal:
                request.state, request.finished_at = ("merged" if merged else "withdrawn"), now
            elif admission is None or request.blocked_base_sha is not None:
                request.state, request.queued_at = "waiting_for_review", None
            elif request.state != "batched":
                request.state = "queued"
                request.queued_at = request.queued_at or now

    @staticmethod
    def _ready(
        request: MergeRequest,
        now: datetime,
        base_sha: str,
        policy: QueuePolicy,
        preceding: set[int],
    ) -> bool:
        if request.admission is None or request.blocked_reason or request.blocked_base_sha:
            return False
        admission = Admission.model_validate(request.admission)
        return (
            admission.head_sha == request.head_sha
            and admission.base_sha == base_sha
            and admission.policy_digest == policy.fingerprint
            and timedelta(0) <= now - admission.checked_at <= ADMISSION_MAX_AGE
            and not (set(request.dependencies) - set(admission.merged_dependencies) - preceding)
        )

    async def schedule(self, base_sha: str, policy: QueuePolicy, lease: Lease) -> Batch | None:
        async with self._locked(lease) as (session, queue, now):
            active = await session.scalar(
                select(Batch).where(Batch.queue_id == queue.id, Batch.state == "active")
            )
            if active is not None and (
                active.base_sha != base_sha
                or active.policy != policy.model_dump(mode="json")
                or active.deadline <= now
            ):
                await self._invalidate(session, active, now, None)
                return None
            if queue.paused:
                return None
            if active is not None:
                return active
            entries = await self._entries(session)
            selected: list[MergeRequest] = []
            preceding: set[int] = set()
            while len(selected) < policy.max_batch_size:
                for entry in entries:
                    request = entry.request
                    if (
                        entry.number not in preceding
                        and request.state == "queued"
                        and self._ready(request, now, base_sha, policy, preceding)
                    ):
                        selected.append(request)
                        preceding.add(entry.number)
                        break
                else:
                    break
            if not selected:
                return None
            oldest = min(request.queued_at or now for request in selected)
            if len(selected) < policy.max_batch_size and now < oldest + timedelta(
                seconds=policy.collection_seconds
            ):
                return None
            return await self._save_batch(
                session,
                selected,
                request_key=str(uuid7()),
                base_sha=base_sha,
                policy=policy.model_dump(mode="json"),
                deadline=now + timedelta(seconds=policy.deadline_seconds),
                max_attempts=policy.max_attempts,
                max_plans=policy.max_plans,
            )

    async def candidate_inputs(
        self, batch_id: UUID, lease: Lease
    ) -> tuple[Batch, list[QueueEntry]]:
        async with self._locked(lease) as (session, queue, now):
            batch = await self._batch(session, batch_id)
            if queue.paused or batch.state != "active" or batch.deadline <= now:
                raise ValueError("Candidate is paused, inactive or expired")
            return batch, await self._candidate_entries(session, batch, now)

    async def _candidate_entries(
        self, session: AsyncSession, batch: Batch, now: datetime
    ) -> list[QueueEntry]:
        rows = await session.execute(
            select(MergeRequest, PullRequest.number, BatchMember.head_sha)
            .select_from(MergeRequest)
            .join(BatchMember)
            .join(PullRequest, PullRequest.id == MergeRequest.pull_request_id)
            .where(BatchMember.batch_id == batch.id, MergeRequest.queue_id == self.id)
            .order_by(BatchMember.position)
        )
        preceding: set[int] = set()
        entries: list[QueueEntry] = []
        policy = QueuePolicy.model_validate(batch.policy)
        for request, number, head in rows.tuples():
            if (
                request.state != "batched"
                or head != request.head_sha
                or not self._ready(request, now, batch.base_sha, policy, preceding)
            ):
                raise ValueError("Candidate admission is stale")
            entries.append(QueueEntry(request, number))
            preceding.add(number)
        if not entries:
            raise ValueError("Candidate has no members")
        return entries

    async def begin_build(self, batch_id: UUID, lease: Lease) -> bool:
        async with self._locked(lease) as (session, queue, now):
            batch = await self._batch(session, batch_id)
            if queue.paused or batch.state != "active" or batch.deadline <= now:
                return False
            if batch.published_at or batch.candidate_error:
                return False
            if batch.build_attempts >= QueuePolicy.model_validate(batch.policy).max_build_attempts:
                await self._fail_candidate(
                    session, batch, now, "Candidate build retry budget exhausted"
                )
                return False
            batch.build_attempts += 1
            return True

    async def candidate_failed(
        self, batch_id: UUID, reason: str, lease: Lease, *, conflicting_pr: int | None = None
    ) -> None:
        async with self._locked(lease) as (session, _, now):
            batch = await self._batch(session, batch_id)
            if batch.state == "active":
                await self._fail_candidate(
                    session, batch, now, reason, conflicting_pr=conflicting_pr
                )

    async def _fail_candidate(
        self,
        session: AsyncSession,
        batch: Batch,
        now: datetime,
        reason: str,
        *,
        conflicting_pr: int | None = None,
    ) -> None:
        query = (
            select(MergeRequest)
            .join(BatchMember)
            .join(PullRequest, PullRequest.id == MergeRequest.pull_request_id)
            .where(BatchMember.batch_id == batch.id, MergeRequest.queue_id == self.id)
        )
        if conflicting_pr is not None:
            query = query.where(PullRequest.number == conflicting_pr)
        blocked = list(await session.scalars(query))
        if not blocked:
            raise ValueError("Conflicting PR is not a member of this batch")
        await self._invalidate(session, batch, now, None)
        batch.candidate_error = reason
        for request in blocked:
            request.state, request.queued_at = "waiting_for_review", None
            request.blocked_reason = reason
            request.blocked_base_sha = batch.base_sha if conflicting_pr is not None else None

    async def save_candidate(
        self, batch_id: UUID, candidate: Candidate, lease: Lease, *, published: bool = False
    ) -> None:
        async with self._locked(lease) as (session, queue, now):
            batch = await self._batch(session, batch_id)
            if (
                queue.paused
                or batch.state != "active"
                or batch.deadline <= now
                or batch.candidate_error
            ):
                raise ValueError("Candidate is not active")
            repository = (
                await session.scalars(
                    select(Repository.full_name).where(Repository.id == queue.repository_id)
                )
            ).one()
            rows = await session.execute(
                select(BatchMember.head_sha, PullRequest.number)
                .select_from(BatchMember)
                .join(MergeRequest, MergeRequest.id == BatchMember.request_id)
                .join(PullRequest, PullRequest.id == MergeRequest.pull_request_id)
                .where(BatchMember.batch_id == batch.id)
                .order_by(BatchMember.position)
            )
            if (
                candidate.repository.lower() != f"https://github.com/{repository}.git".lower()
                or candidate.branch != queue.target_branch
                or candidate.base_sha != batch.base_sha
                or list(rows.tuples()) != [(pull.sha, pull.number) for pull in candidate.pulls]
            ):
                raise ValueError("Candidate does not match the saved batch")
            manifest = candidate.model_dump(mode="json")
            if batch.candidate is not None and batch.candidate != manifest:
                raise ValueError("Candidate manifest is immutable")
            if published and batch.candidate is None:
                raise ValueError("Save the manifest before publishing")
            batch.candidate = manifest
            if published:
                batch.published_at = batch.published_at or now

    async def expired_candidates(self, before: datetime, lease: Lease) -> list[Batch]:
        async with self._locked(lease) as (session, _, now):
            if before.tzinfo is None or before > now:
                raise ValueError("Invalid retention cutoff")
            return list(
                await session.scalars(
                    select(Batch)
                    .where(
                        Batch.queue_id == self.id,
                        Batch.finished_at < before,
                        Batch.candidate.is_not(None),
                        Batch.ref_deleted_at.is_(None),
                    )
                    .order_by(Batch.finished_at, Batch.id)
                    .limit(20)
                )
            )

    async def candidate_deleted(self, batch_id: UUID, sha: str, lease: Lease) -> None:
        async with self._locked(lease) as (session, _, now):
            batch = await self._batch(session, batch_id)
            if (
                batch.state == "active"
                or batch.candidate is None
                or Candidate.model_validate(batch.candidate).sha != sha
            ):
                raise ValueError("Candidate is active or does not match deletion")
            batch.ref_deleted_at = now
