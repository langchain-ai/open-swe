"""Human operator controls delegated by the authenticated queue gateway."""

import hmac
import logging
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import httpx2
from fastapi import APIRouter, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from openswe.config import ENV
from openswe.database import postgres
from openswe.github.http import GitHubAppUnavailable
from openswe.github.repositories import Repository
from openswe.merge_queue.configuration import QueueConfiguration
from openswe.merge_queue.controller import QueueController
from openswe.merge_queue.models import Batch
from openswe.merge_queue.native import NativeRules
from openswe.merge_queue.recovery import RecoveryDecision, RecoveryMember
from openswe.merge_queue.store import BatchHistory, LeaseLost, MergeQueue, NativeSelection
from openswe.users.models import User

router = APIRouter()
logger = logging.getLogger(__name__)


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str
    repository_id: int = Field(gt=0, strict=True)
    actor_email: str
    expires_at: AwareDatetime
    operation: Literal["status", "pause", "batch_size"]
    expected_version: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(default="", max_length=500)
    paused: bool | None = Field(default=None, strict=True)
    max_batch_size: int | None = Field(default=None, ge=1, le=100, strict=True)


class AttemptStatus(BaseModel):
    id: UUID
    number: int
    state: str
    created_at: datetime
    finished_at: datetime | None
    workflow_url: str | None
    actions: list[str]


class BatchStatus(BaseModel):
    attempts: list[AttemptStatus]
    slack_url: str | None
    members: list[RecoveryMember]

    @classmethod
    async def from_history(cls, history: BatchHistory, repository: str) -> BatchStatus:
        recovery = history.batch.policy.get("recovery")
        recovery = recovery if isinstance(recovery, dict) else {}
        settings = recovery.get("settings")
        settings = settings if isinstance(settings, dict) else {}
        channel, ts = settings.get("channel_id"), recovery.get("thread_ts")
        runs = {
            e.attempt_id: e.reference.get("run_id")
            for e in history.evidence
            if e.key == "validation-run"
        }
        actions: dict[UUID, list[str]] = {}
        for row in history.decisions:
            if row.plan.get("kind") != "native-recovery":
                continue
            decision = RecoveryDecision.model_validate(row.plan)
            if decision.status not in {"applied", "escalated"}:
                continue
            proposal = decision.proposal
            if decision.status == "escalated":
                message = "Robin escalated an unconfirmed PR removal for operator review"
            elif proposal.action == "defer":
                actions.setdefault(proposal.attempt_id, []).extend(
                    f"Robin dequeued PR #{member.number}" for member in await proposal.targets()
                )
                continue
            else:
                message = {
                    "retry": "Robin requested a retry",
                    "pause": "Robin paused the queue",
                    "escalate": "Robin escalated for operator review",
                    "except_tests": "Robin approved a test exception",
                }[proposal.action]
                resumed = history.batch.policy.get("recovery_resumes", {})
                if (
                    proposal.action == "pause"
                    and isinstance(resumed, dict)
                    and str(row.id) in resumed
                ):
                    message += " (since resumed)"
            actions.setdefault(proposal.attempt_id, []).append(message)
        return cls(
            members=[
                RecoveryMember(entry_id=e.id, number=e.pull.number, head_sha=e.pull.head_sha)
                for e in NativeSelection.model_validate(history.batch.candidate).entries
            ],
            attempts=[
                AttemptStatus(
                    id=a.id,
                    number=a.number,
                    state=a.state,
                    created_at=a.created_at,
                    finished_at=a.finished_at,
                    workflow_url=f"https://github.com/{repository}/actions/runs/{runs[a.id]}"
                    if type(runs.get(a.id)) is int
                    else None,
                    actions=actions.get(a.id, []),
                )
                for a in history.attempts
            ],
            slack_url=f"https://app.slack.com/archives/{channel}/p{ts.replace('.', '')}"
            if isinstance(channel, str) and isinstance(ts, str)
            else None,
        )


class ControlStatus(BaseModel):
    version: str
    paused: bool
    pause_owner: Literal["recovery", "operator"] | None
    pause_reason: str | None
    max_batch_size: int
    max_batch_size_limit: int | None
    batch: BatchStatus | None


class QueueControl:
    def __init__(self, config: QueueConfiguration, queue: MergeQueue):
        self.config, self.queue = config, queue

    @classmethod
    async def open(cls, command: ControlRequest) -> QueueControl:
        config = await QueueConfiguration.load()
        if (
            config is None
            or config.mode != "native"
            or config.check_app_id is None
            or config.repository != command.repository
            or config.repository_id != command.repository_id
        ):
            raise HTTPException(404, "No native queue controller configured for this repository")
        repository = await Repository.get(config.repository)
        if repository is None:
            raise HTTPException(404, "Queue repository not found")
        async with postgres.session() as session:
            queue = await session.scalar(
                select(MergeQueue).where(
                    MergeQueue.routing_id == config.routing_id,
                    MergeQueue.repository_id == repository.id,
                    MergeQueue.target_branch == "main",
                    MergeQueue.mode == "native",
                )
            )
        if queue is None:
            raise HTTPException(404, "Native queue not found")
        return cls(config, queue)

    async def limit(self) -> int:
        controller = QueueController(self.config)
        async with controller.open_repo(self.queue) as repo:
            rules = [
                NativeRules.model_validate(rule["parameters"])
                for rule in await repo.branch_rules("main")
                if rule["type"] == "merge_queue"
            ]
        if len(rules) != 1:
            raise HTTPException(409, "Expected one native merge queue rule on main")
        return min(rules[0].max_entries_to_build, rules[0].max_entries_to_merge, 100)

    async def update(self, command: ControlRequest, limit: int | None) -> None:
        if not command.expected_version or len(command.reason.strip()) < 10:
            raise HTTPException(422, "A current queue version and reason are required")
        if command.operation == "pause":
            if command.paused is None or command.max_batch_size is not None:
                raise HTTPException(422, "Pause requires only a paused value")
        elif (
            limit is None
            or command.max_batch_size is None
            or command.max_batch_size > limit
            or command.paused is not None
        ):
            raise HTTPException(422, "Batch size must fit the native GitHub queue limits")
        user = await User.for_email(command.actor_email)
        if user is None:
            raise HTTPException(403, "Sign in to Robin before changing queue settings")
        try:
            await self.queue.operator_update(
                self.config,
                expected_version=command.expected_version,
                actor=user.id,
                paused=command.paused,
                max_batch_size=command.max_batch_size,
            )
        except (ValueError, LeaseLost) as exc:
            raise HTTPException(
                409, "Queue changed or recovery is settling; refresh before trying again"
            ) from exc

    async def status(self, limit: int | None) -> ControlStatus:
        async with postgres.session() as session:
            batch_id = await session.scalar(
                select(Batch.id).where(
                    Batch.queue_id == self.queue.id,
                    Batch.state == "active",
                )
            )
        batch = None
        pause_reason = None
        if batch_id is not None:
            history = await self.queue.history(batch_id)
            for decision in history.decisions:
                if decision.id == self.queue.recovery_pause_id:
                    proposal = decision.plan.get("proposal")
                    reason = proposal.get("reason") if isinstance(proposal, dict) else None
                    if isinstance(reason, str):
                        pause_reason = reason
            batch = await BatchStatus.from_history(history, self.config.repository)

        return ControlStatus(
            version=await self.queue.control_version(self.config),
            paused=self.queue.paused,
            pause_owner=("recovery" if self.queue.recovery_pause_id else "operator")
            if self.queue.paused
            else None,
            pause_reason=pause_reason,
            max_batch_size=self.config.policy.max_batch_size,
            max_batch_size_limit=limit,
            batch=batch,
        )


@router.post("/merge-queue/control", response_model=ControlStatus | None, include_in_schema=False)
async def control_queue(request: Request) -> ControlStatus | None:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 4096:
            raise HTTPException(413, "Queue control request is too large")
    secret = ENV.MERGE_QUEUE_DISPATCH_SECRET.optional()
    if not secret or len(secret) < 32:
        raise HTTPException(404, "Queue controls are disabled")
    signature = hmac.digest(secret.encode(), b"queue-control:" + body, "sha256").hex()
    if not hmac.compare_digest(
        signature.encode(), request.headers.get("X-Merge-Queue-Signature", "").encode()
    ):
        raise HTTPException(401, "Invalid queue control signature")
    try:
        command = ControlRequest.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(422, "Invalid queue control request") from exc
    if not datetime.now(UTC) < command.expires_at <= datetime.now(UTC) + timedelta(seconds=60):
        raise HTTPException(401, "Queue control request expired")
    control = await QueueControl.open(command)
    limit = None
    if command.operation != "pause":
        try:
            limit = await control.limit()
        except httpx2.HTTPError, GitHubAppUnavailable, ValueError, HTTPException:
            if command.operation != "status":
                raise
            logger.warning("Native queue limits unavailable to control panel", exc_info=True)
    if command.operation == "status":
        return await control.status(limit)
    await control.update(command, limit)
    return None
