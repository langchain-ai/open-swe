"""Write each GitHub delivery into the mirror as it arrives.

Runs inline in the webhook route, before any other handler, so whatever a
delivery triggers reads a mirror that already includes it. A delivery that
cannot be written is logged and left to ``sync``: the sweep notices what the
mirror missed.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Final
from uuid import UUID

from pydantic import AliasPath, BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from openswe.database import postgres
from openswe.github.mirror.ci import (
    CheckRun,
    CheckRunPayload,
    CommitStatus,
    StatusPayload,
    WorkflowRun,
    WorkflowRunPayload,
)
from openswe.github.mirror.conversation import (
    IssueComment,
    IssueCommentPayload,
    Review,
    ReviewComment,
    ReviewCommentPayload,
    ReviewPayload,
    ReviewThread,
    ThreadEventPayload,
    delete_by_github_id,
)
from openswe.github.mirror.sync import PullRequestSync, RepositorySweep
from openswe.github.pull_request_key import PullRequestKey
from openswe.github.pull_request_status import pull_request_identity
from openswe.github.pull_requests import PullRequest, PullRequestEvent
from openswe.github.repositories import Repository
from openswe.ui_invalidations import Topic

logger = logging.getLogger(__name__)

_OPEN_STATES: Final = ("open", "draft")


class _Delivery(BaseModel):
    action: str = ""
    repo_full_name: str = Field("", validation_alias=AliasPath("repository", "full_name"))


async def ingest(event_type: str, payload: Mapping[str, object]) -> None:
    """Write what one delivery says; never raises."""
    handler = _HANDLERS.get(event_type)
    if handler is None:
        return
    try:
        await handler(payload)
    except Exception:
        logger.exception(
            "Could not mirror a GitHub delivery",
            extra={"github_event": event_type, "github_action": payload.get("action")},
        )


async def remember_comment(owner: str, repo: str, number: int, created: object) -> None:
    """Store a conversation comment Open SWE just posted, ahead of its delivery; never raises."""
    try:
        comment = IssueCommentPayload.model_validate(created)
        stored = await PullRequest(owner=owner, repo=repo, number=number).ensure()
        async with postgres.session() as session:
            await IssueComment.store(session, [comment.to_row(stored.id)])
            await _invalidate(session, [stored])
    except Exception:
        logger.exception("Could not mirror a posted comment", extra=_pr_extra(owner, repo, number))


async def remember_review_comment(owner: str, repo: str, number: int, created: object) -> None:
    """Store an inline comment Open SWE just posted, ahead of its delivery; never raises."""
    try:
        comment = ReviewCommentPayload.model_validate(created)
        stored = await PullRequest(owner=owner, repo=repo, number=number).ensure()
        async with postgres.session() as session:
            await ReviewComment.store(session, [comment.to_row(stored.id)])
            await _invalidate(session, [stored])
    except Exception:
        logger.exception(
            "Could not mirror a posted review comment", extra=_pr_extra(owner, repo, number)
        )


async def remember_thread_resolution(node_id: str, *, resolved: bool) -> None:
    """Store a resolution Open SWE just made, ahead of its delivery; never raises."""
    try:
        async with postgres.session() as session:
            thread = await session.scalar(
                select(ReviewThread).where(ReviewThread.node_id == node_id)
            )
            if thread is None:
                return
            thread.resolved = resolved
            pull = await session.get(PullRequest, thread.pull_request_id)
            if pull is not None:
                await _invalidate(session, [pull])
    except Exception:
        logger.exception("Could not mirror a thread resolution", extra={"thread_node_id": node_id})


def _pr_extra(owner: str, repo: str, number: int) -> dict[str, object]:
    return {"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number}


async def _pull_request(payload: Mapping[str, object]) -> None:
    stored = await _save_pull_request(payload)
    if stored is None:
        return
    sync = PullRequestSync(stored.owner, stored.repo, stored.number)
    if stored.synced_at is None or payload.get("action") == "synchronize":
        await sync.schedule(full=True)
    elif stored.state in _OPEN_STATES and stored.mergeable is None:
        await sync.schedule(full=False)


async def _review(payload: Mapping[str, object]) -> None:
    stored = await _save_pull_request(payload)
    if stored is None:
        return
    row = ReviewPayload.model_validate(payload.get("review")).to_row(stored.id)
    async with postgres.session() as session:
        if row is not None:
            await Review.store(session, [row])
        await _invalidate(session, [stored])
    # The review decision is GitHub's to compute.
    await PullRequestSync(stored.owner, stored.repo, stored.number).schedule(full=False)


async def _review_comment(payload: Mapping[str, object]) -> None:
    stored = await _save_pull_request(payload)
    if stored is None:
        return
    comment = ReviewCommentPayload.model_validate(payload.get("comment"))
    async with postgres.session() as session:
        if payload.get("action") == "deleted":
            await delete_by_github_id(session, ReviewComment, comment.id)
        else:
            await ReviewComment.store(session, [comment.to_row(stored.id)])
        await _invalidate(session, [stored])


async def _review_thread(payload: Mapping[str, object]) -> None:
    stored = await _save_pull_request(payload)
    action = payload.get("action")
    if stored is None or action not in {"resolved", "unresolved"}:
        return
    thread = ThreadEventPayload.model_validate(payload.get("thread"))
    row = thread.to_row(stored.id, resolved=action == "resolved")
    async with postgres.session() as session:
        await ReviewComment.store(session, [c.to_row(stored.id) for c in thread.comments])
        if row is not None:
            await _mark_thread(session, row)
        await _invalidate(session, [stored])


class _IssueCommentDelivery(_Delivery):
    number: int = Field(validation_alias=AliasPath("issue", "number"))
    on_pull_request: object = Field(None, validation_alias=AliasPath("issue", "pull_request"))


async def _issue_comment(payload: Mapping[str, object]) -> None:
    delivery = _IssueCommentDelivery.model_validate(payload)
    identity = pull_request_identity(
        {"repo_full_name": delivery.repo_full_name, "number": delivery.number}
    )
    if delivery.on_pull_request is None or identity is None:
        return
    stored = await PullRequest(owner=identity[0], repo=identity[1], number=identity[2]).ensure()
    comment = IssueCommentPayload.model_validate(payload.get("comment"))
    async with postgres.session() as session:
        if delivery.action == "deleted":
            await delete_by_github_id(session, IssueComment, comment.id)
        else:
            await IssueComment.store(session, [comment.to_row(stored.id)])
        await _invalidate(session, [stored])
    if stored.synced_at is None:
        await PullRequestSync(*identity).schedule(full=True)


async def _check_run(payload: Mapping[str, object]) -> None:
    delivery = _Delivery.model_validate(payload)
    run = CheckRunPayload.model_validate(payload.get("check_run"))
    async with postgres.session() as session:
        repository_id = await Repository.ensure_id(session, delivery.repo_full_name)
        await CheckRun.store(session, [run.to_row(repository_id)])
        await _invalidate(session, await _with_head(session, repository_id, run.head_sha))


class _CheckSuiteDelivery(_Delivery):
    head_sha: str = Field(validation_alias=AliasPath("check_suite", "head_sha"))


async def _check_suite(payload: Mapping[str, object]) -> None:
    delivery = _CheckSuiteDelivery.model_validate(payload)
    if delivery.action != "completed":
        return
    async with postgres.session() as session:
        repository_id = await Repository.ensure_id(session, delivery.repo_full_name)
        pulls = await _with_head(session, repository_id, delivery.head_sha)
    # Finished checks can unblock a merge; GitHub recomputes the merge state.
    for pull in pulls:
        if pull.state in _OPEN_STATES:
            await PullRequestSync(pull.owner, pull.repo, pull.number).schedule(full=False)


async def _workflow_run(payload: Mapping[str, object]) -> None:
    delivery = _Delivery.model_validate(payload)
    run = WorkflowRunPayload.model_validate(payload.get("workflow_run"))
    async with postgres.session() as session:
        repository_id = await Repository.ensure_id(session, delivery.repo_full_name)
        await WorkflowRun.put(session, [run.to_row(repository_id)])
        await _invalidate(session, await _with_head(session, repository_id, run.head_sha))


async def _status(payload: Mapping[str, object]) -> None:
    delivery = _Delivery.model_validate(payload)
    status = StatusPayload.model_validate(payload)
    async with postgres.session() as session:
        repository_id = await Repository.ensure_id(session, delivery.repo_full_name)
        await CommitStatus.store(session, [status.to_row(repository_id)])
        await _invalidate(session, await _with_head(session, repository_id, status.sha))


class _PushDelivery(_Delivery):
    ref: str = ""
    deleted: bool = False


async def _push(payload: Mapping[str, object]) -> None:
    delivery = _PushDelivery.model_validate(payload)
    branch = delivery.ref.removeprefix("refs/heads/")
    if delivery.deleted or branch == delivery.ref:
        return
    async with postgres.session() as session:
        repository_id = await Repository.ensure_id(session, delivery.repo_full_name)
        based = list(
            await session.scalars(
                select(PullRequest).where(
                    PullRequest.repository_id == repository_id,
                    PullRequest.base_ref == branch,
                    PullRequest.state.in_(_OPEN_STATES),
                )
            )
        )
    # A moved base changes whether every pull request into it still merges.
    for pull in based:
        await PullRequestSync(pull.owner, pull.repo, pull.number).schedule(full=False)


class _InstalledRepository(BaseModel):
    full_name: str


class _InstallationDelivery(BaseModel):
    action: str = ""
    repositories: list[_InstalledRepository] = Field(default_factory=list)
    repositories_added: list[_InstalledRepository] = Field(default_factory=list)


async def _installation(payload: Mapping[str, object]) -> None:
    delivery = _InstallationDelivery.model_validate(payload)
    if delivery.action not in {"created", "added", "unsuspend"}:
        return
    for repository in delivery.repositories + delivery.repositories_added:
        await Repository(full_name=repository.full_name).save()
        await RepositorySweep.schedule(repository.full_name)


async def _save_pull_request(payload: Mapping[str, object]) -> PullRequest | None:
    event = PullRequestEvent.parse(payload)
    record = event.to_pull_request() if event is not None else None
    if event is None or record is None:
        return None
    return await record.save(repository_private=event.repo_private)


async def _mark_thread(session: AsyncSession, row: ReviewThread) -> None:
    statement = insert(ReviewThread).values(row.values())
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=list(ReviewThread.natural_key),
            set_={
                "node_id": statement.excluded.node_id,
                "resolved": statement.excluded.resolved,
                "updated_at": func.clock_timestamp(),
            },
        )
    )


async def _with_head(session: AsyncSession, repository_id: UUID, sha: str) -> list[PullRequest]:
    if not sha:
        return []
    return list(
        await session.scalars(
            select(PullRequest).where(
                PullRequest.repository_id == repository_id, PullRequest.head_sha == sha
            )
        )
    )


async def _invalidate(session: AsyncSession, pulls: Sequence[PullRequest]) -> None:
    for pull in pulls:
        await Topic.PULL_REQUESTS.invalidate(
            session, key=PullRequestKey.of(pull.owner, pull.repo, pull.number)
        )


type _Handler = Callable[[Mapping[str, object]], Awaitable[None]]
_HANDLERS: Final[dict[str, _Handler]] = {
    "pull_request": _pull_request,
    "pull_request_review": _review,
    "pull_request_review_comment": _review_comment,
    "pull_request_review_thread": _review_thread,
    "issue_comment": _issue_comment,
    "check_run": _check_run,
    "check_suite": _check_suite,
    "workflow_run": _workflow_run,
    "status": _status,
    "push": _push,
    "installation": _installation,
    "installation_repositories": _installation,
}
