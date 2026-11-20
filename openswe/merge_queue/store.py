"""Transactional queue storage; callers supply an authorized repository binding.

Every operation rechecks the repository binding. Fenced writes lock the binding
before the queue. Status reads do not take these locks.
No GitHub calls, admission decisions or job registration live here.
"""

import asyncio
import base64
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal, Self
from uuid import UUID, uuid7

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, TypeAdapter
from sqlalchemy import BigInteger, ForeignKey, Text, delete, func, or_, select, true, tuple_, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.github.pull_requests import PullRequest
from openswe.github.queue import NativeCandidate, NativeEntry, NativeQueueSnapshot
from openswe.github.repositories import Repository
from openswe.merge_queue.admission import ADMISSION_MAX_AGE, Admission, QueuePolicy
from openswe.merge_queue.configuration import QueueConfiguration
from openswe.merge_queue.dispatch import CandidateAcceptance, DispatchEnvelope, WorkflowPolicy
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
from openswe.merge_queue.recovery import (
    RecoveryDecision,
    RecoveryMember,
    RecoveryProposal,
    RecoverySettings,
    RecoveryThread,
)
from openswe.merge_queue.validation_contract import (
    SERVICE_JOBS,
    ValidationDispatch,
    ValidationRequest,
)
from openswe.routing.store import RepositoryBinding


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


class NativeSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    entries: tuple[NativeEntry, ...]

    def candidate(self, snapshot: NativeQueueSnapshot) -> NativeCandidate | None:
        current = (
            {entry.id: entry for entry in snapshot.queue.entries.nodes} if snapshot.queue else {}
        )
        survivors = [entry for entry in self.entries if entry.id in current]
        if not survivors:
            return None
        expected = [(entry.pull.number, entry.pull.head_sha) for entry in survivors]
        for candidate in snapshot.candidates.values():
            if [(pull.number, pull.head_sha) for pull in candidate.members] == expected and all(
                not pull.draft and pull.review_decision == "APPROVED" for pull in candidate.members
            ):
                return candidate
        return None

    async def members(self, snapshot: NativeQueueSnapshot) -> tuple[RecoveryMember, ...]:
        candidate = self.candidate(snapshot)
        if candidate is None or snapshot.queue is None:
            return ()
        return tuple(
            RecoveryMember(
                entry_id=entry.id, number=entry.pull.number, head_sha=entry.pull.head_sha
            )
            for entry in sorted(snapshot.queue.entries.nodes, key=lambda entry: entry.position)[
                : len(candidate.members)
            ]
        )


@dataclass(frozen=True)
class NativeWork:
    batch: Batch
    attempt: Attempt
    request: ValidationRequest
    run_id: int | None
    dispatch: bool = False
    members: tuple[RecoveryMember, ...] = ()
    accepted_candidates: tuple[str, ...] = ()
    dispatch_deferred: bool = False
    test_exception: str | None = None

    async def replacement(
        self, snapshot: NativeQueueSnapshot, *, main_advance_from: str | None
    ) -> CandidateAcceptance | None:
        superseded = self.batch.policy.get("superseded_attempts", 0)
        if type(superseded) is not int:
            raise ValueError("Invalid workflow policy attempt boundary")
        if (
            self.batch.state != "active"
            or self.batch.policy.get("allow_main_advance") is not True
            or main_advance_from != self.batch.base_sha
            or self.request.workflow_sha != self.batch.base_sha
            or self.request.workflow_sha == snapshot.main_sha
            or self.attempt.number <= superseded
            or self.dispatch_deferred
            or self.test_exception is not None
            or self.attempt.state not in {"running", "succeeded", "failed"}
            or not self.members
        ):
            return None
        selection = NativeSelection.model_validate(self.batch.candidate)
        candidate = selection.candidate(snapshot)
        if candidate is None or self.members != await selection.members(snapshot):
            return None
        return CandidateAcceptance(base_sha=snapshot.main_sha, candidate_sha=candidate.head_sha)

    @classmethod
    async def restore(
        cls,
        batch: Batch,
        attempt: Attempt,
        references: dict[str, dict[str, JsonValue]],
    ) -> Self:
        request = ValidationRequest.model_validate(references["native-request"])
        run_id = references.get("validation-run", {}).get("run_id")
        if run_id is not None and type(run_id) is not int:
            raise ValueError("Invalid saved validation run")
        members = TypeAdapter(tuple[RecoveryMember, ...]).validate_python(
            references.get("native-members", {}).get("members", [])
        )
        accepted = tuple(
            CandidateAcceptance.model_validate(reference).candidate_sha
            for key, reference in references.items()
            if key.startswith("gate-acceptance:")
        )
        return cls(
            batch,
            attempt,
            request,
            run_id,
            members=members,
            accepted_candidates=accepted,
            dispatch_deferred="dispatch-policy-deferred" in references,
            test_exception=TypeAdapter(str).validate_python(references["test-exception"]["summary"])
            if "test-exception" in references
            else None,
        )


@dataclass(frozen=True)
class NativeCheckUpdate:
    envelope: DispatchEnvelope
    state: Literal["in_progress", "recovering", "cancelled"]
    acceptance: CandidateAcceptance | None = None


class MergeQueue(Record):
    __tablename__ = "merge_queue"

    routing_id: Mapped[UUID | None] = mapped_column(
        "routing_repository_id",
        ForeignKey(RepositoryBinding.id, ondelete="SET NULL"),
        nullable=True,
    )
    repository_id: Mapped[UUID] = mapped_column(ForeignKey(Repository.id))
    target_branch: Mapped[str]
    mode: Mapped[Literal["custom", "native"]] = mapped_column(Text, default="custom")
    native_snapshot: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSONB(none_as_null=True), default=None
    )
    paused: Mapped[bool] = mapped_column(default=True)
    recovery_pause_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("merge_queue_decision.id", ondelete="SET NULL", use_alter=True), default=None
    )
    fence: Mapped[int] = mapped_column(BigInteger, default=0)
    lease_owner: Mapped[UUID | None] = mapped_column(default=None)
    lease_until: Mapped[datetime | None] = mapped_column(default=None)
    configuration_generation: Mapped[str | None] = mapped_column(default=None)

    @property
    def bound_routing_id(self) -> UUID:
        if self.routing_id is None:
            raise ValueError("Queue no longer has a repository binding")
        return self.routing_id

    @classmethod
    async def load(
        cls, queue_id: UUID, routing_id: UUID, *, session: AsyncSession | None = None
    ) -> Self | None:
        async with postgres.session(session) as session:
            return await session.scalar(
                select(cls).where(cls.id == queue_id, cls.routing_id == routing_id)
            )

    @classmethod
    async def register(
        cls,
        routing_id: UUID,
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
            await cls._binding(session, routing_id, repository_id)
            existing = await session.scalar(
                select(cls).where(
                    cls.repository_id == repository_id, cls.target_branch == target_branch
                )
            )
            if existing is not None:
                if existing.routing_id != routing_id:
                    raise ValueError(
                        "Queue belongs to another repository binding; explicit handoff required"
                    )
                return existing
            queue = cls(
                routing_id=routing_id,
                repository_id=repository_id,
                target_branch=target_branch,
                created_by=actor,
                updated_by=actor,
            )
            session.add(queue)
            await session.flush()
            return queue

    @staticmethod
    async def _binding(
        session: AsyncSession, routing_id: UUID, repository_id: UUID
    ) -> RepositoryBinding:
        binding = await session.scalar(
            select(RepositoryBinding)
            .where(
                RepositoryBinding.id == routing_id,
                RepositoryBinding.repository_id == repository_id,
            )
            .with_for_update()
        )
        if binding is None:
            raise ValueError("Repository is not bound to this repository binding")
        return binding

    @asynccontextmanager
    async def _locked(
        self,
        lease: Lease | None = None,
        *,
        generation: str | None = None,
        check_configuration: bool = True,
    ) -> AsyncIterator[tuple[AsyncSession, Self, datetime]]:
        async with postgres.session() as session:
            binding = await self._binding(session, self.bound_routing_id, self.repository_id)
            expected = generation or (
                lease.configuration_generation
                if lease is not None and check_configuration
                else None
            )
            if expected is not None:
                configured = binding.merge_queue_config
                try:
                    if not isinstance(configured, dict):
                        raise ValueError("Queue configuration must be an object")
                    current = QueueConfiguration.model_validate(
                        {
                            **configured,
                            "routing_id": self.routing_id,
                        }
                    )
                except ValueError as exc:
                    raise LeaseLost("Queue configuration is disabled or invalid") from exc
                if current.generation != expected:
                    raise LeaseLost("Queue configuration changed")
            queue = await session.scalar(
                select(type(self))
                .where(
                    type(self).id == self.id,
                    type(self).routing_id == self.routing_id,
                    type(self).repository_id == self.repository_id,
                )
                .with_for_update()
            )
            if queue is None:
                raise ValueError("Queue not found")
            if queue.mode != self.mode:
                raise LeaseLost("Queue mode changed")
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            if lease is not None and (
                queue.lease_owner != lease.owner
                or queue.fence != lease.fence
                or queue.lease_until is None
                or queue.lease_until <= now
            ):
                raise LeaseLost("Queue lease expired or was replaced")
            if (
                lease is not None
                and check_configuration
                and lease.configuration_generation != queue.configuration_generation
            ):
                raise LeaseLost("Queue configuration generation changed")
            yield session, queue, now

    async def adopt_workflow_policy(
        self, config: QueueConfiguration, approved: WorkflowPolicy
    ) -> QueueConfiguration:
        updated = config.with_workflow_policy(approved)
        async with self._locked(generation=config.generation) as (
            session,
            queue,
            now,
        ):
            if updated == config:
                return config
            active = await session.scalar(
                select(Batch).where(Batch.queue_id == queue.id, Batch.state == "active")
            )
            if active is not None and active.policy.get("trust") == config.native_trust:
                policy = QueuePolicy.model_validate(active.policy["policy"])
                assert policy.validation is not None
                policy = policy.model_copy(
                    update={
                        "validation": policy.validation.model_copy(
                            update={"suite": approved.validation_suite}
                        )
                    }
                )
                attempts = list(
                    await session.scalars(select(Attempt).where(Attempt.batch_id == active.id))
                )
                for attempt in attempts:
                    if attempt.state == "running":
                        attempt.state, attempt.finished_at = "cancelled", now
                active.policy = {
                    **active.policy,
                    "policy": policy.model_dump(mode="json"),
                    "trust": updated.native_trust,
                    "superseded_attempts": max((attempt.number for attempt in attempts), default=0),
                    "workflow_policy_refresh": True,
                    "allow_main_advance": approved.allow_main_advance,
                }
            await session.execute(
                update(RepositoryBinding)
                .where(RepositoryBinding.id == self.routing_id)
                .values(
                    merge_queue_config=updated.model_dump(mode="json", exclude={"routing_id"}),
                    updated_at=func.clock_timestamp(),
                    updated_by=None,
                )
            )
            return updated

    @asynccontextmanager
    async def native_publication(self, lease: Lease) -> AsyncIterator[None]:
        """Serialize check publication with configuration changes and queue pauses."""
        # Bound lock waits and network I/O so a slow gateway cannot stall routing writes.
        async with asyncio.timeout(5), self._locked(lease) as (_, queue, _):
            if queue.paused or queue.mode != "native":
                raise LeaseLost("Native queue is paused or disabled")
            yield

    async def enable_native_observation(self) -> None:
        async with self._locked() as (session, queue, now):
            if queue.mode == "native":
                return
            if not queue.paused or (queue.lease_until is not None and queue.lease_until > now):
                raise ValueError("Pause and drain the custom queue before enabling native mode")
            requests = await session.scalar(
                select(MergeRequest.id)
                .where(MergeRequest.queue_id == self.id, MergeRequest.finished_at.is_(None))
                .limit(1)
            )
            batches = await session.scalar(
                select(Batch.id)
                .where(
                    Batch.queue_id == self.id,
                    or_(
                        Batch.state == "active",
                        (Batch.candidate.is_not(None) & Batch.ref_deleted_at.is_(None)),
                        select(Attempt.id)
                        .where(Attempt.batch_id == Batch.id, Attempt.state == "running")
                        .exists(),
                    ),
                )
                .limit(1)
            )
            if requests is not None or batches is not None:
                raise ValueError("Drain custom requests, attempts, and retained refs first")
            queue.mode = "native"
            queue.fence += 1
            queue.lease_owner = queue.lease_until = None
            queue.updated_by = None
        self.mode = "native"

    async def record_native_snapshot(self, snapshot: NativeQueueSnapshot, lease: Lease) -> None:
        async with self._locked(lease, check_configuration=False) as (_, queue, now):
            if queue.mode != "native" or snapshot.branch != queue.target_branch:
                raise ValueError("Queue is not observing this native target")
            queue.native_snapshot = snapshot.model_copy(update={"observed_at": now}).model_dump(
                mode="json"
            )
            queue.updated_by = None

    async def prepare_native_validation(
        self,
        snapshot: NativeQueueSnapshot,
        timeout_seconds: int,
        lease: Lease,
        *,
        policy: QueuePolicy,
        trust: str,
        allow_main_advance: bool = False,
        main_advance_from: str | None = None,
    ) -> NativeWork | None:
        async with self._locked(lease) as (session, queue, now):
            if queue.mode != "native" or snapshot.branch != queue.target_branch:
                raise ValueError("Queue is not validating this native target")
            batch = await session.scalar(
                select(Batch).where(Batch.queue_id == queue.id, Batch.state == "active")
            )
            saved_policy: dict[str, JsonValue] = {
                "policy": policy.model_dump(mode="json"),
                "trust": trust,
                "timeout_seconds": timeout_seconds,
                **({"allow_main_advance": True} if allow_main_advance else {}),
            }
            if batch is not None:
                selection = NativeSelection.model_validate(batch.candidate)
                current_ids = (
                    {entry.id for entry in snapshot.queue.entries.nodes}
                    if snapshot.queue
                    else set()
                )
                refreshing = batch.policy.get("workflow_policy_refresh") is True
                if (
                    refreshing
                    and batch.policy.get("trust") == trust
                    and batch.policy.get("timeout_seconds") == timeout_seconds
                    and selection.candidate(snapshot) is not None
                ):
                    batch.base_sha = snapshot.main_sha
                    batch.policy = {**batch.policy, "workflow_policy_refresh": False}
                valid_main = (
                    main_advance_from == batch.base_sha
                    if batch.policy.get("allow_main_advance") is True
                    else snapshot.main_sha == batch.base_sha
                )
                if (
                    batch.policy.get("trust") != trust
                    or batch.policy.get("timeout_seconds") != timeout_seconds
                    or (not refreshing and not valid_main)
                    or not any(entry.id in current_ids for entry in selection.entries)
                ):
                    await self._invalidate(session, batch, now, None)
                    return None
                if batch.deadline <= now:
                    return None
                policy = QueuePolicy.model_validate(batch.policy["policy"])
            else:
                if queue.paused or snapshot.queue is None:
                    return None
                entries = tuple(
                    sorted(snapshot.queue.entries.nodes, key=lambda entry: entry.position)
                )
                if not entries:
                    return None
                selected = entries[: policy.max_batch_size]
                oldest = min(entry.enqueued_at for entry in selected)
                if len(selected) < policy.max_batch_size and now < oldest + timedelta(
                    seconds=policy.collection_seconds
                ):
                    return None
                selection = NativeSelection(entries=selected)
                if selection.candidate(snapshot) is None:
                    return None
                deadline = now + timedelta(
                    seconds=min(timeout_seconds - 30, policy.deadline_seconds)
                )
                if deadline <= now:
                    return None
                batch = Batch(
                    queue_id=queue.id,
                    request_key=f"native:{uuid7()}",
                    base_sha=snapshot.main_sha,
                    policy=saved_policy,
                    candidate=selection.model_dump(mode="json"),
                    deadline=deadline,
                    max_attempts=policy.max_attempts,
                    max_plans=policy.max_plans,
                )
                session.add(batch)
                await session.flush()
            if queue.paused:
                return None
            if allow_main_advance:
                batch.policy = {**batch.policy, "observed_main_sha": snapshot.main_sha}
            candidate = selection.candidate(snapshot)
            if candidate is None:
                if (
                    allow_main_advance
                    and snapshot.queue is not None
                    and len(snapshot.candidates) == len(snapshot.queue.entries.nodes)
                ):
                    await self._invalidate(session, batch, now, None)
                return None
            members = await selection.members(snapshot) if allow_main_advance else ()
            attempts = list(
                await session.scalars(
                    select(Attempt).where(Attempt.batch_id == batch.id).order_by(Attempt.number)
                )
            )
            superseded = batch.policy.get("superseded_attempts", 0)
            if type(superseded) is not int:
                raise ValueError("Invalid workflow policy attempt boundary")
            for attempt in attempts:
                if attempt.number <= superseded:
                    continue
                references = {
                    row.key: row.reference
                    for row in await session.scalars(
                        select(Evidence).where(Evidence.attempt_id == attempt.id)
                    )
                }
                work = await NativeWork.restore(batch, attempt, references)
                reusable = (
                    attempt.state in {"running", "succeeded"}
                    and await work.replacement(snapshot, main_advance_from=main_advance_from)
                    is not None
                )
                if work.request.candidate_sha != candidate.head_sha and not reusable:
                    if attempt.state == "running":
                        attempt.state, attempt.finished_at = "cancelled", now
                    continue
                if attempt.deadline <= now:
                    if attempt.state == "running":
                        attempt.state, attempt.finished_at = "timed_out", now
                    return None
                if attempt.state not in {"running", "succeeded"}:
                    return None
                deferred = "dispatch-policy-deferred" in references
                if deferred:
                    await session.execute(
                        delete(Evidence).where(
                            Evidence.attempt_id == attempt.id,
                            Evidence.key == "dispatch-policy-deferred",
                        )
                    )
                return replace(work, dispatch=deferred)
            if len(attempts) >= batch.max_attempts:
                return None
            validation = policy.validation
            assert validation is not None
            batch.base_sha = snapshot.main_sha
            if attempts:
                batch.policy = {**batch.policy, "superseded_attempts": attempts[-1].number}
            attempt = Attempt(
                batch_id=batch.id,
                request_key=f"{candidate.head_sha}:{trust}:{len(attempts) + 1}",
                number=len(attempts) + 1,
                deadline=min(batch.deadline, now + timedelta(seconds=validation.deadline_seconds)),
            )
            session.add(attempt)
            await session.flush()
            request = ValidationRequest(
                repository=snapshot.repository,
                version=2 if validation.suite == "services" else 1,
                expected_jobs=SERVICE_JOBS
                if validation.suite == "services"
                else ("candidate-smoke",),
                base_sha=batch.base_sha if validation.suite == "services" else None,
                batch_id=batch.id,
                attempt_id=attempt.id,
                candidate_sha=candidate.head_sha,
                workflow_sha=batch.base_sha,
                dispatch_actor_id=validation.dispatch_actor_id,
            )
            await self._save_evidence(
                session, attempt, "native-request", request.model_dump(mode="json")
            )
            if members:
                await self._save_evidence(
                    session,
                    attempt,
                    "native-members",
                    {"members": [member.model_dump(mode="json") for member in members]},
                )
            return NativeWork(batch, attempt, request, None, dispatch=True, members=members)

    async def finish_native_validation(
        self, work: NativeWork, evidence: dict[str, JsonValue], lease: Lease, *, succeeded: bool
    ) -> None:
        async with self._locked(lease) as (session, _, now):
            attempt = await self._attempt(session, work.attempt.id)
            batch = await self._batch(session, attempt.batch_id)
            if batch.state != "active" or attempt.state != "running":
                return
            if min(attempt.deadline, batch.deadline) <= now:
                attempt.state, attempt.finished_at = "timed_out", now
                return
            if succeeded:
                run = await session.scalar(
                    select(Evidence).where(
                        Evidence.attempt_id == attempt.id, Evidence.key == "validation-run"
                    )
                )
                if (
                    run is None
                    or evidence.get("run_id") != run.reference["run_id"]
                    or evidence.get("run_attempt") != 1
                    or evidence.get("tested_sha") != work.request.candidate_sha
                    or any(
                        evidence.get(key) != value
                        for key, value in work.request.model_dump(mode="json").items()
                    )
                ):
                    raise ValueError("Validation evidence does not match its saved request and run")
            await self._save_evidence(session, attempt, "validation-result", evidence)
            attempt.state, attempt.finished_at = "succeeded" if succeeded else "failed", now

    async def native_validation_history(self, lease: Lease) -> list[NativeWork]:
        """Read the active batch's attempts, newest first, including superseded successes."""
        async with self._locked(lease) as (session, _, _):
            batch = await session.scalar(
                select(Batch).where(Batch.queue_id == self.id, Batch.state == "active")
            )
            if batch is None:
                return []
            history = []
            for attempt in await session.scalars(
                select(Attempt).where(Attempt.batch_id == batch.id).order_by(Attempt.number.desc())
            ):
                references = {
                    row.key: row.reference
                    for row in await session.scalars(
                        select(Evidence).where(Evidence.attempt_id == attempt.id)
                    )
                }
                history.append(await NativeWork.restore(batch, attempt, references))
            return history

    @asynccontextmanager
    async def native_check_update(
        self,
        snapshot: NativeQueueSnapshot,
        lease: Lease,
        *,
        main_advance_from: str | None = None,
        attempt_id: UUID | None = None,
    ) -> AsyncIterator[NativeCheckUpdate | None]:
        """Acknowledge one report only after delivery; fence it against policy changes."""
        async with asyncio.timeout(5), self._locked(lease) as (session, queue, now):
            if queue.mode != "native" or snapshot.branch != queue.target_branch:
                raise ValueError("Queue is not reporting this native target")
            rows = (
                await session.execute(
                    select(Attempt, Batch)
                    .join(Batch, Attempt.batch_id == Batch.id)
                    .where(
                        Batch.queue_id == queue.id,
                        Attempt.id == attempt_id if attempt_id is not None else true(),
                        select(Evidence.id)
                        .where(Evidence.attempt_id == Attempt.id, Evidence.key == "native-request")
                        .exists(),
                        ~select(Evidence.id)
                        .where(
                            Evidence.attempt_id == Attempt.id,
                            Evidence.key.in_(("check-cancelled", "dispatch-rejected")),
                        )
                        .exists(),
                    )
                    .order_by((Batch.state == "active").desc(), Attempt.created_at)
                    .limit(100)
                )
            ).all()
            current_shas = (
                {entry.head_sha for entry in snapshot.queue.entries.nodes}
                if snapshot.queue
                else set()
            )
            for attempt, batch in rows:
                references = {
                    row.key: row.reference
                    for row in await session.scalars(
                        select(Evidence)
                        .where(Evidence.attempt_id == attempt.id)
                        .order_by(Evidence.created_at, Evidence.id)
                    )
                }
                work = await NativeWork.restore(batch, attempt, references)
                request = work.request
                expired = min(attempt.deadline, batch.deadline) <= now
                current = request.candidate_sha in current_shas
                acceptance = (
                    await work.replacement(snapshot, main_advance_from=main_advance_from)
                    if not current
                    else None
                )
                current = current or acceptance is not None
                superseded = batch.policy.get("superseded_attempts", 0)
                if type(superseded) is not int:
                    raise ValueError("Invalid workflow policy attempt boundary")
                # A superseded check on a live SHA can still be used by its replacement.
                if current and (
                    batch.state != "active"
                    or attempt.number <= superseded
                    or attempt.state == "cancelled"
                ):
                    continue
                if (
                    not current
                    and snapshot.queue
                    and len(snapshot.candidates) != len(snapshot.queue.entries.nodes)
                ):
                    continue
                state: Literal["in_progress", "recovering", "cancelled"]
                pending: list[str] = []
                if not current or expired:
                    state = "cancelled"
                    targets: dict[str, CandidateAcceptance | None] = {request.candidate_sha: None}
                    for key, reference in references.items():
                        if key.startswith(
                            ("check-in_progress:", "check-recovering:", "gate-acceptance:")
                        ):
                            target = CandidateAcceptance.model_validate(reference)
                            targets[target.candidate_sha] = target
                    pending = [sha for sha in targets if f"check-cancelled:{sha}" not in references]
                    if not pending:
                        await self._save_evidence(
                            session, attempt, "check-cancelled", {"reported": True}
                        )
                        continue
                    acceptance = targets[pending[-1]]
                elif batch.state == "active" and attempt.state == "failed" and not queue.paused:
                    state = "recovering"
                elif acceptance is not None and attempt.state in {"running", "succeeded"}:
                    state = "in_progress"
                else:
                    continue
                target_sha = acceptance.candidate_sha if acceptance else request.candidate_sha
                marker = (
                    f"check-{state}:{target_sha}"
                    if acceptance or state == "cancelled"
                    else f"check-{state}"
                )
                if marker in references:
                    continue
                if attempt.state == "running" and state == "cancelled":
                    attempt.state, attempt.finished_at = (
                        "timed_out" if expired else "cancelled",
                        now,
                    )
                if attempt.deadline <= now - timedelta(days=1):
                    await self._save_evidence(
                        session, attempt, "check-cancelled", {"reported": False}
                    )
                    continue
                policy = references.get("workflow-policy", {})
                envelope = DispatchEnvelope.model_validate(
                    {
                        "contract": request,
                        "expires_at": attempt.deadline,
                        "workflow_policy_revision": policy.get("revision"),
                        "workflow_digest": policy.get("digest"),
                        "members": [member.model_dump(mode="json") for member in work.members],
                    }
                )
                yield NativeCheckUpdate(envelope, state, acceptance)
                await self._save_evidence(
                    session,
                    attempt,
                    marker,
                    acceptance.model_dump(mode="json") if acceptance else {"reported": True},
                )
                if state == "cancelled" and len(pending) == 1:
                    await self._save_evidence(
                        session, attempt, "check-cancelled", {"reported": True}
                    )
                return
            yield None

    async def record_native_landing(self, work: NativeWork, lease: Lease) -> None:
        async with self._locked(lease) as (session, _, now):
            batch = await self._batch(session, work.batch.id)
            attempt = await self._attempt(session, work.attempt.id)
            if batch.state == "active" and attempt.state == "succeeded":
                batch.state, batch.finished_at = "landed", now
                await session.execute(
                    update(Attempt)
                    .where(Attempt.batch_id == batch.id, Attempt.state == "running")
                    .values(state="cancelled", finished_at=now)
                )

    async def authorize_native_result(
        self,
        work: NativeWork,
        snapshot: NativeQueueSnapshot,
        lease: Lease,
        *,
        main_advance_from: str | None = None,
    ) -> bool:
        async with self._locked(lease) as (session, queue, now):
            batch = await self._batch(session, work.batch.id)
            attempt = await self._attempt(session, work.attempt.id)
            candidate = NativeSelection.model_validate(batch.candidate).candidate(snapshot)
            superseded = batch.policy.get("superseded_attempts", 0)
            if type(superseded) is not int:
                raise ValueError("Invalid workflow policy attempt boundary")
            acceptance = await replace(work, batch=batch, attempt=attempt).replacement(
                snapshot, main_advance_from=main_advance_from
            )
            reused = acceptance is not None
            if (
                queue.paused
                or batch.state != "active"
                or attempt.state != "succeeded"
                or attempt.number <= superseded
                or min(batch.deadline, attempt.deadline) <= now
                or (
                    batch.policy.get("allow_main_advance") is True
                    and main_advance_from != batch.base_sha
                )
                or (batch.base_sha != snapshot.main_sha and not reused)
                or candidate is None
                or (candidate.head_sha != work.request.candidate_sha and not reused)
            ):
                return False
            if acceptance is not None:
                await self._save_evidence(
                    session,
                    attempt,
                    f"gate-acceptance:{candidate.head_sha}",
                    acceptance.model_dump(mode="json"),
                )
            else:
                await self._save_evidence(
                    session, attempt, "gate-intent", work.request.model_dump(mode="json")
                )
            return True

    @asynccontextmanager
    async def _read_snapshot(self) -> AsyncIterator[tuple[AsyncSession, Self]]:
        async with (
            postgres.snapshot_transaction() as conn,
            AsyncSession(bind=conn, expire_on_commit=False) as session,
        ):
            queue = await session.scalar(
                select(type(self))
                .join(
                    RepositoryBinding,
                    (RepositoryBinding.id == type(self).routing_id)
                    & (RepositoryBinding.repository_id == type(self).repository_id),
                )
                .where(
                    type(self).id == self.id,
                    type(self).routing_id == self.routing_id,
                    type(self).repository_id == self.repository_id,
                )
            )
            if queue is None:
                raise QueueItemNotFound("Queue not found or repository binding changed")
            yield session, queue

    async def claim(
        self, owner: UUID, ttl: timedelta, *, config: QueueConfiguration | None = None
    ) -> Lease | None:
        if ttl <= timedelta(0):
            raise ValueError("Lease duration must be positive")
        generation = config.generation if config is not None else None
        async with self._locked(generation=generation) as (session, queue, now):
            if queue.lease_until is not None and queue.lease_until > now:
                return None
            if config is not None:
                repository = await session.scalar(
                    select(Repository.key).where(Repository.id == queue.repository_id)
                )
                if config.mode != queue.mode or repository != config.repository.lower():
                    raise LeaseLost("Queue configuration belongs to another repository or mode")
                if queue.mode == "native" and queue.configuration_generation != generation:
                    active = await session.scalar(
                        select(Batch).where(Batch.queue_id == queue.id, Batch.state == "active")
                    )
                    if active is not None and active.policy.get("trust") != config.native_trust:
                        await self._invalidate(session, active, now, None)
                queue.configuration_generation = generation
            queue.fence += 1
            queue.lease_owner, queue.lease_until = owner, now + ttl
            queue.updated_by = None
            return Lease(owner=owner, fence=queue.fence, configuration_generation=generation)

    async def renew(self, lease: Lease, ttl: timedelta) -> None:
        if ttl <= timedelta(0):
            raise ValueError("Lease duration must be positive")
        async with self._locked(lease) as (_, queue, now):
            queue.lease_until = now + ttl
            queue.updated_by = None

    async def release(self, lease: Lease) -> None:
        async with self._locked(lease, check_configuration=False) as (_, queue, _):
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

    async def set_paused(self, paused: bool, *, actor: UUID, manual_hold: bool = False) -> None:
        async with self._locked() as (_, queue, _):
            if paused and queue.paused and not manual_hold:
                return
            queue.paused, queue.updated_by = paused, actor
            queue.recovery_pause_id = None

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

    async def bind_native_recovery(self, batch_id: UUID, thread_id: str, lease: Lease) -> None:
        """Called by the trusted failure hook, never from a model-supplied thread ID."""
        async with self._locked(lease) as (session, queue, now):
            batch = await self._batch(session, batch_id)
            if queue.mode != "native" or batch.state != "active" or batch.deadline <= now:
                raise ValueError("Recovery requires an active native batch")
            existing = batch.policy.get("recovery_thread_id")
            if not thread_id or (existing is not None and existing != thread_id):
                raise ValueError("Recovery thread cannot be replaced")
            batch.policy = {**batch.policy, "recovery_thread_id": thread_id}

    async def recovery_batches(self, lease: Lease) -> list[UUID]:
        async with self._locked(lease) as (session, queue, _):
            owns_pause = (
                select(Decision.id)
                .where(Decision.batch_id == Batch.id, Decision.id == queue.recovery_pause_id)
                .exists()
            )
            return list(
                await session.scalars(
                    select(Batch.id)
                    .where(
                        Batch.queue_id == self.id,
                        (
                            Batch.policy["recovery"].is_not(None)
                            & (
                                (Batch.state == "active")
                                | owns_pause
                                | Batch.policy["recovery"]["settled"].as_boolean().is_not(True)
                            )
                        )
                        | (
                            (Batch.state == "active")
                            & select(Attempt.id)
                            .where(
                                Attempt.batch_id == Batch.id,
                                Attempt.state.in_(("failed", "timed_out")),
                            )
                            .exists()
                        ),
                    )
                    .order_by(owns_pause.desc(), (Batch.state == "active").desc(), Batch.created_at)
                    .limit(100)
                )
            )

    async def ensure_recovery_thread(
        self, batch_id: UUID, settings: RecoverySettings, lease: Lease
    ) -> RecoveryThread:
        async with self._locked(lease) as (session, _, _):
            batch = await self._batch(session, batch_id)
            if saved := batch.policy.get("recovery"):
                result = RecoveryThread.model_validate(saved)
                if result.settings != settings:
                    raise PermissionError("Recovery destination or operator changed")
                return result
            failed = await session.scalar(
                select(Attempt.id)
                .where(Attempt.batch_id == batch_id, Attempt.state.in_(("failed", "timed_out")))
                .limit(1)
            )
            if failed is None:
                raise ValueError("Recovery requires a failed validation")
            if batch.policy.get("recovery_thread_id") is not None:
                raise PermissionError("Recovery thread cannot be replaced")
            result = RecoveryThread(settings=settings, thread_id=str(uuid7()))
            batch.policy = {
                **batch.policy,
                "recovery_thread_id": result.thread_id,
                "recovery": result.model_dump(mode="json"),
            }
            return result

    async def recovery_for_thread(self, thread_id: str) -> BatchHistory:
        async with self._read_snapshot() as (session, _):
            batch_id = await session.scalar(
                select(Batch.id).where(
                    Batch.queue_id == self.id,
                    Batch.policy["recovery_thread_id"].astext == thread_id,
                )
            )
        if batch_id is None:
            raise PermissionError("Only the bound recovery thread may act")
        return await self.history(batch_id)

    async def update_recovery_thread(
        self, batch_id: UUID, state: RecoveryThread, lease: Lease
    ) -> None:
        async with self._locked(lease) as (session, _, _):
            batch = await self._batch(session, batch_id)
            saved = RecoveryThread.model_validate(batch.policy.get("recovery"))
            if saved.thread_id != state.thread_id or saved.settings != state.settings:
                raise PermissionError("Recovery thread binding changed")
            batch.policy = {**batch.policy, "recovery": state.model_dump(mode="json")}

    async def resume_native_recovery(
        self,
        batch_id: UUID,
        thread_id: str,
        pause_id: UUID,
        reason: str,
        lease: Lease,
        *,
        trust: str,
    ) -> None:
        async with self._locked(lease) as (session, queue, now):
            batch = await self._batch(session, batch_id)
            if batch.policy.get("recovery_thread_id") != thread_id:
                raise PermissionError("Only the bound recovery thread may resume its pause")
            if queue.mode != "native":
                raise ValueError("Recovery policy changed")
            resumed = batch.policy.get("recovery_resumes", {})
            if not isinstance(resumed, dict):
                raise ValueError("Invalid recovery resume history")
            if str(pause_id) in resumed:
                return
            if not queue.paused or queue.recovery_pause_id != pause_id:
                raise PermissionError("This pause belongs to an operator or another incident")
            decision = await session.scalar(
                select(Decision).where(Decision.id == pause_id, Decision.batch_id == batch_id)
            )
            if decision is None:
                raise ValueError("Recovery pause no longer exists")
            saved = RecoveryDecision.model_validate(decision.plan)
            if saved.thread_id != thread_id or saved.proposal.action != "pause":
                raise PermissionError("Only Robin's own outage pause can be resumed")
            latest = await session.scalar(
                select(Attempt)
                .where(Attempt.batch_id == batch_id)
                .order_by(Attempt.number.desc())
                .limit(1)
            )
            policy = {
                **batch.policy,
                "recovery_resumes": {**resumed, str(pause_id): reason},
            }
            if (
                batch.state == "active"
                and batch.deadline > now
                and latest is not None
                and batch.policy.get("trust") == trust
                and latest.state in {"failed", "timed_out"}
                and latest.number < batch.max_attempts
            ):
                policy["superseded_attempts"] = latest.number
            batch.policy = policy
            queue.paused, queue.recovery_pause_id = False, None

    async def _recovery_attempt(
        self,
        session: AsyncSession,
        proposal: RecoveryProposal,
        snapshot: NativeQueueSnapshot,
        now: datetime,
    ) -> tuple[Batch, Attempt]:
        batch = await self._batch(session, proposal.batch_id)
        latest = await session.scalar(
            select(Attempt)
            .where(Attempt.batch_id == batch.id)
            .order_by(Attempt.number.desc())
            .limit(1)
        )
        candidate = NativeSelection.model_validate(batch.candidate).candidate(snapshot)
        reference = await session.scalar(
            select(Evidence).where(
                Evidence.attempt_id == proposal.attempt_id, Evidence.key == "native-request"
            )
        )
        superseded = batch.policy.get("superseded_attempts", 0)
        members = await session.scalar(
            select(Evidence).where(
                Evidence.attempt_id == proposal.attempt_id, Evidence.key == "native-members"
            )
        )
        advisory = (
            proposal.action in {"pause", "escalate"}
            and batch.policy.get("allow_main_advance") is True
            and batch.policy.get("observed_main_sha") == snapshot.main_sha
            and members is not None
            and members.reference.get("members")
            == [
                member.model_dump(mode="json")
                for member in await NativeSelection.model_validate(batch.candidate).members(
                    snapshot
                )
            ]
            and candidate is not None
        )
        if (
            batch.state != "active"
            or batch.deadline <= now
            or snapshot.branch != self.target_branch
            or (snapshot.main_sha != batch.base_sha and not advisory)
            or latest is None
            or latest.id != proposal.attempt_id
            or latest.state not in {"failed", "timed_out"}
            or type(superseded) is not int
            or latest.number <= superseded
            or reference is None
            or reference.reference.get("repository") != snapshot.repository
            or reference.reference.get("candidate_sha") != proposal.candidate_sha
            or candidate is None
            or (candidate.head_sha != proposal.candidate_sha and not advisory)
        ):
            raise ValueError("Recovery does not match the current failed candidate")
        if proposal.action in {"retry", "defer"} and latest.number >= batch.max_attempts:
            raise ValueError("Validation attempt budget exhausted")
        if proposal.member is not None:
            selected = NativeSelection.model_validate(batch.candidate)
            current = snapshot.queue.entries.nodes if snapshot.queue else ()
            if not any(
                [await proposal.member.matches(entry) for entry in selected.entries]
            ) or not any([await proposal.member.matches(entry) for entry in current]):
                raise ValueError("Deferral does not match a current selected PR head")
        return batch, latest

    async def decide_native_recovery(
        self,
        proposal: RecoveryProposal,
        thread_id: str,
        snapshot: NativeQueueSnapshot,
        lease: Lease,
        *,
        trust: str,
        allow_test_exceptions: bool = False,
    ) -> RecoveryDecision:
        """Caller supplies the authenticated thread; a proposal cannot grant its own authority."""
        async with self._locked(lease) as (session, queue, now):
            batch = await self._batch(session, proposal.batch_id)
            if queue.mode != "native" or batch.policy.get("recovery_thread_id") != thread_id:
                raise PermissionError("Only the bound recovery thread may decide for this batch")
            decisions = list(
                await session.scalars(select(Decision).where(Decision.batch_id == batch.id))
            )
            for row in decisions:
                if row.request_key == str(proposal.request_key):
                    saved = RecoveryDecision.model_validate(row.plan)
                    if saved.proposal != proposal or saved.thread_id != thread_id:
                        raise ValueError("Recovery key was already used with different input")
                    return saved
            if queue.paused:
                raise ValueError("Queue is paused; operator resumption is required")
            if batch.policy.get("trust") != trust:
                raise ValueError("Recovery policy changed")
            batch, attempt = await self._recovery_attempt(session, proposal, snapshot, now)
            if await Decision.budget_used(decisions) >= batch.max_plans:
                raise ValueError("Recovery decision budget exhausted")
            if any(
                row.plan.get("kind") == "native-recovery"
                and row.plan.get("status") in {"pending", "applying"}
                for row in decisions
            ):
                raise ValueError("A recovery action is already pending")
            selected_ids = {
                entry.id for entry in NativeSelection.model_validate(batch.candidate).entries
            }
            saved = RecoveryDecision(
                proposal=proposal,
                members=tuple(
                    RecoveryMember(
                        entry_id=entry.id, number=entry.pull.number, head_sha=entry.pull.head_sha
                    )
                    for entry in (snapshot.queue.entries.nodes if snapshot.queue else ())
                    if entry.id in selected_ids
                ),
                thread_id=thread_id,
                generation=lease.configuration_generation,
                status="pending" if proposal.action == "defer" else "applied",
            )
            recorded = Decision(
                batch_id=batch.id,
                request_key=str(proposal.request_key),
                plan=saved.model_dump(mode="json"),
                lease_owner=lease.owner,
                fence=lease.fence,
            )
            session.add(recorded)
            await session.flush()
            if proposal.action == "except_tests":
                if (
                    not allow_test_exceptions
                    or attempt.state != "failed"
                    or attempt.deadline <= now
                ):
                    raise ValueError("Test exceptions require enabled policy and a failed attempt")
                run = await session.scalar(
                    select(Evidence).where(
                        Evidence.attempt_id == attempt.id, Evidence.key == "validation-run"
                    )
                )
                if run is None or type(run.reference.get("run_id")) is not int:
                    raise ValueError("No workflow run is bound to this failed attempt")
                await self._save_evidence(
                    session,
                    attempt,
                    "test-exception",
                    {
                        "summary": f"{proposal.reason}\nTests: {', '.join(proposal.tests)}\nSuggested fix: {proposal.suggested_fix}"
                    },
                )
                attempt.state = "succeeded"
            if proposal.action == "retry":
                batch.policy = {**batch.policy, "superseded_attempts": attempt.number}
            elif proposal.action == "pause":
                queue.paused = True
                queue.recovery_pause_id = recorded.id
            return saved

    async def pending_native_recovery(self, lease: Lease) -> RecoveryDecision | None:
        async with self._locked(lease) as (session, _, _):
            row = await session.scalar(
                select(Decision)
                .join(Batch)
                .where(
                    Batch.queue_id == self.id,
                    Decision.plan["kind"].astext == "native-recovery",
                    Decision.plan["status"].astext.in_(("pending", "applying")),
                )
                .order_by(Decision.id)
                .limit(1)
            )
            return RecoveryDecision.model_validate(row.plan) if row is not None else None

    async def start_native_deferral(
        self,
        decision: RecoveryDecision,
        snapshot: NativeQueueSnapshot,
        lease: Lease,
    ) -> None:
        async with self._locked(lease) as (session, queue, now):
            batch, _ = await self._recovery_attempt(session, decision.proposal, snapshot, now)
            row = (
                await session.scalars(
                    select(Decision).where(
                        Decision.batch_id == batch.id,
                        Decision.request_key == str(decision.proposal.request_key),
                    )
                )
            ).one()
            if (
                queue.paused
                or RecoveryDecision.model_validate(row.plan) != decision
                or decision.status != "pending"
                or decision.generation != lease.configuration_generation
                or batch.policy.get("recovery_thread_id") != decision.thread_id
            ):
                raise ValueError("Deferral authority changed")
            row.plan = decision.model_copy(update={"status": "applying"}).model_dump(mode="json")

    async def settle_native_deferral(
        self,
        decision: RecoveryDecision,
        snapshot: NativeQueueSnapshot,
        lease: Lease,
        *,
        error: str | None = None,
        supersede: bool = False,
        main_advance_from: str | None = None,
    ) -> RecoveryDecision:
        async with self._locked(lease) as (session, queue, now):
            batch = await self._batch(session, decision.proposal.batch_id)
            row = (
                await session.scalars(
                    select(Decision).where(
                        Decision.batch_id == batch.id,
                        Decision.request_key == str(decision.proposal.request_key),
                    )
                )
            ).one()
            saved = RecoveryDecision.model_validate(row.plan)
            if supersede:
                if saved != decision or saved.status != "pending":
                    raise ValueError("Deferral is no longer pending")
                result = saved.model_copy(
                    update={
                        "status": "superseded",
                        "detail": "Main advanced before removal; validate the current candidate before diagnosing its PRs.",
                    }
                )
                row.plan = result.model_dump(mode="json")
                return result
            if saved.status not in {"pending", "applying"}:
                return saved
            member = saved.proposal.member
            assert member is not None
            entries = snapshot.queue.entries.nodes if snapshot.queue else ()
            original = {
                entry.entry_id: entry
                for entry in saved.members
                if entry.entry_id != member.entry_id
            }
            survivors = [entry for entry in entries if entry.id in original]
            if error is None:
                target = [
                    entry
                    for entry in entries
                    if entry.id == member.entry_id or entry.pull.number == member.number
                ]
                if (
                    saved.status != "applying"
                    or batch.state != "active"
                    or saved.generation != lease.configuration_generation
                    or snapshot.queue is None
                    or (
                        snapshot.main_sha != batch.base_sha
                        and not (
                            batch.policy.get("allow_main_advance") is True
                            and main_advance_from == batch.base_sha
                        )
                    )
                    or len(survivors) != len(original)
                    or any([not await original[entry.id].matches(entry) for entry in survivors])
                ):
                    error = "Surviving membership or authority changed; operator review required"
                elif target:
                    if (
                        len(target) == 1
                        and await member.matches(target[0])
                        and now < min(batch.deadline, row.updated_at + timedelta(minutes=2))
                    ):
                        # GitHub can lag a dequeue. Reconcile again without rewriting the
                        # applying timestamp or resending the mutation, including after restart.
                        return saved
                    error = "Removal outcome is ambiguous; operator review required"
            result = saved.model_copy(
                update={
                    "status": "escalated" if error is not None else "applied",
                    "detail": (error or "Deferral outcome could not be verified")
                    if error is not None
                    else "Selected queue entry is absent; replacement must pass validation",
                }
            )
            row.plan = result.model_dump(mode="json")
            attempt = await self._attempt(session, saved.proposal.attempt_id)
            await self._save_evidence(
                session,
                attempt,
                f"deferral:{saved.proposal.request_key}",
                {
                    "snapshot": snapshot.model_dump(mode="json"),
                    "error": error,
                },
            )
            if error is not None:
                queue.paused = True
                queue.recovery_pause_id = None
            return result

    async def record_plan(
        self,
        batch_id: UUID,
        request_key: str,
        plan: dict[str, JsonValue],
        lease: Lease,
    ) -> Decision:
        """Save a proposal for deterministic validation; this authorizes no action."""
        if plan.get("kind") == "native-recovery":
            raise ValueError("Executable recovery requires the bound-thread validation path")
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
        async with self._locked(lease, check_configuration=False) as (session, queue, now):
            if before > now:
                raise ValueError("Retention cutoff cannot be in the future")
            batches = list(
                await session.scalars(
                    select(Batch.id)
                    .where(
                        Batch.queue_id == self.id,
                        Batch.finished_at < before,
                        ~select(Decision.id)
                        .where(
                            Decision.batch_id == Batch.id,
                            Decision.id == queue.recovery_pause_id,
                        )
                        .exists(),
                        or_(
                            Batch.policy["recovery"].is_(None),
                            Batch.policy["recovery"]["settled"].as_boolean().is_(True),
                        ),
                        true()
                        if self.mode == "native"
                        else or_(Batch.candidate.is_(None), Batch.ref_deleted_at.is_not(None)),
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
