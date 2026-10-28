"""Human-only queue controls; admission and GitHub mutations belong to the worker."""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.oauth import session_user_id
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.github.http import GitHubClient, RepoClient
from openswe.github.pull_requests import PullRequest
from openswe.merge_queue import controller
from openswe.merge_queue.git import SHA
from openswe.merge_queue.models import RequestState
from openswe.merge_queue.store import MergeQueue, QueueCursor, QueueItemNotFound
from openswe.users.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/merge-queue/{workspace_id}", tags=["merge-queue"])


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MergeIntent(RequestBody):
    number: int = Field(gt=0, strict=True)
    head_sha: SHA
    request_key: UUID
    dependencies: tuple[Annotated[int, Field(gt=0, strict=True)], ...] = Field(
        default=(), max_length=100
    )


class PriorityChange(RequestBody):
    priority: int = Field(ge=-100, le=100, strict=True)


class PauseChange(RequestBody):
    paused: bool = Field(strict=True)


class RequestView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    head_sha: str
    state: RequestState
    priority: int
    dependencies: list[int]
    blocked_reason: str | None
    created_by: UUID | None
    created_at: datetime


class EntryView(BaseModel):
    number: int
    request: RequestView


class QueueStatus(BaseModel):
    queue_id: UUID
    repository: str
    paused: bool
    operator: bool
    items: list[EntryView]
    next_cursor: str | None
    active_batch_id: UUID | None


class AttemptView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    state: Literal["running", "succeeded", "failed", "timed_out", "cancelled"]
    created_at: datetime
    finished_at: datetime | None


class BatchView(BaseModel):
    id: UUID
    state: str
    candidate: dict[str, JsonValue] | None
    attempts: list[AttemptView]


@dataclass
class QueueAccess:
    queue: MergeQueue
    user: User
    repo: RepoClient
    operator: bool

    def require_custom(self) -> None:
        if self.queue.mode == "native":
            raise HTTPException(409, "Use GitHub to change native queue membership and order")

    def require_operator(self) -> None:
        if not self.operator:
            raise HTTPException(403, "Queue operator access required")

    async def require_writer(self) -> None:
        if not await self.repo.can_write(self.user.github_login):
            raise HTTPException(403, "Repository write access required")


async def queue_access(
    workspace_id: UUID, session: dict[str, str] = SESSION_DEP
) -> AsyncIterator[QueueAccess]:
    running = controller.CONTROLLER
    if running is None or running.config.workspace_id != workspace_id:
        raise HTTPException(404, "Queue is not enabled for this workspace")
    # A stable user ID must survive login renames; never use an API-key actor here.
    user_id = session_user_id(session)
    user = await User.for_session(user_id, session["sub"])
    if user is None or user.id != user_id or not user.github_login:
        raise HTTPException(401, "Sign in again to use the queue")
    queue = await MergeQueue.load(running.queue.id, workspace_id)
    if queue is None:
        raise HTTPException(404, "Queue not found")
    await require_repo_access_for_user(user.github_login, running.config.repository)
    if queue.mode == "native" and running.config.check_app_id is None:
        raise HTTPException(409, "Native queue observation is read-only; use GitHub queue controls")
    async with GitHubClient.as_user(user.github_login) as github:
        repo = github.repo(*running.config.repository.split("/", 1))
        if (await repo.info()).get("id") != running.config.repository_id:
            raise HTTPException(409, "Repository identity changed")
        try:
            yield QueueAccess(
                queue, user, repo, user.email.lower() in running.config.operator_emails
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except QueueItemNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc


ACCESS = Depends(queue_access)


@router.get("")
async def queue_status(
    page_size: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
    access: QueueAccess = ACCESS,
) -> QueueStatus:
    try:
        after = QueueCursor.decode(cursor, access.queue.id) if cursor is not None else None
    except ValueError as exc:
        raise HTTPException(400, "Invalid queue cursor") from exc
    paused, entries, batch_id = await access.queue.snapshot(limit=page_size + 1, cursor=after)
    return QueueStatus(
        queue_id=access.queue.id,
        repository=access.repo.full_name,
        paused=paused,
        operator=access.operator,
        items=[
            EntryView(number=entry.number, request=RequestView.model_validate(entry.request))
            for entry in entries[:page_size]
        ],
        next_cursor=(
            QueueCursor.model_validate(entries[page_size - 1].request).encode()
            if len(entries) > page_size
            else None
        ),
        active_batch_id=batch_id,
    )


@router.get("/batches/{batch_id}")
async def batch_status(batch_id: UUID, access: QueueAccess = ACCESS) -> BatchView:
    history = await access.queue.history(batch_id)
    return BatchView(
        id=history.batch.id,
        state=history.batch.state,
        candidate=history.batch.candidate,
        attempts=[AttemptView.model_validate(attempt) for attempt in history.attempts],
    )


@router.post("/requests")
@audit_endpoint
async def request_merge(body: MergeIntent, access: QueueAccess = ACCESS) -> RequestView:
    access.require_custom()
    await access.require_writer()
    pull = await access.repo.pull_request(body.number).queue_state()
    if (
        pull.state != "OPEN"
        or pull.merged
        or pull.branch != access.queue.target_branch
        or pull.head != body.head_sha
        or any(
            name is None or name.lower() != access.repo.full_name.lower()
            for name in (pull.head_repository, pull.base_repository)
        )
    ):
        raise HTTPException(409, "PR must be open at the requested head in this repository")
    stored = await PullRequest.load(access.repo.owner, access.repo.name, body.number)
    stored.base_ref = pull.branch
    stored = await stored.save()
    requested = await access.queue.request_merge(
        stored.id,
        pull.head,
        str(body.request_key),
        actor=access.user.id,
        dependencies=body.dependencies,
    )
    return RequestView.model_validate(requested)


@router.delete("/requests/{request_id}", status_code=204)
@audit_endpoint
async def withdraw_request(request_id: UUID, access: QueueAccess = ACCESS) -> None:
    access.require_custom()
    await access.require_writer()
    try:
        await access.queue.withdraw(request_id, actor=access.user.id, operator=access.operator)
    except QueueItemNotFound:
        logger.debug("Merge request already absent", extra={"request_id": str(request_id)})


@router.patch("/requests/{request_id}/priority", status_code=204)
@audit_endpoint
async def set_priority(
    request_id: UUID, body: PriorityChange, access: QueueAccess = ACCESS
) -> None:
    access.require_custom()
    access.require_operator()
    await access.require_writer()
    await access.queue.set_priority(request_id, body.priority, actor=access.user.id)


@router.patch("/pause", status_code=204)
@audit_endpoint
async def set_paused(body: PauseChange, access: QueueAccess = ACCESS) -> None:
    access.require_operator()
    await access.require_writer()
    await access.queue.set_paused(body.paused, actor=access.user.id)
