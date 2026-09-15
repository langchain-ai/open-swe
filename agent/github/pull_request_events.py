"""Turn GitHub webhooks into pull request rows, checks and review threads.

A ``pull_request`` webhook carries the pull request itself, so its row is
written straight from the payload. ``check_run`` and ``status`` name a commit
instead: they resolve to pull requests either by the numbers GitHub attaches or
by head sha, and a pull request we have never fully read is synced first, since
a check cannot hang off a row that does not exist.

GitHub publishes no webhook for resolving or unresolving a review thread, so
any review activity on a pull request re-reads its whole review-thread set.

Ingestion is best effort: every entry point swallows and logs its failures so a
webhook delivery is never rejected over a storage problem.
"""

import logging
from datetime import datetime
from typing import Annotated, Self

from pydantic import AliasPath, BaseModel, BeforeValidator, Field, ValidationError

from agent.github.app import get_github_app_installation_token
from agent.github.pull_request_sync import sync_pull_request, sync_review_threads
from agent.github.pull_request_terms import parse_identity
from agent.github.pull_requests import (
    GithubText,
    PullRequest,
    PullRequestCheck,
    PullRequestEvent,
)

logger = logging.getLogger(__name__)


class _PullRequestRef(BaseModel):
    number: int


_PullRequestRefs = Annotated[list[_PullRequestRef], BeforeValidator(lambda value: value or [])]


class _RepositoryEvent(BaseModel):
    repo_full_name: GithubText = Field("", validation_alias=AliasPath("repository", "full_name"))
    repo_private: bool | None = Field(None, validation_alias=AliasPath("repository", "private"))

    @classmethod
    def parse(cls, payload: object) -> Self | None:
        try:
            return cls.model_validate(payload)
        except ValidationError:
            return None

    @property
    def repository(self) -> tuple[str, str] | None:
        """``(owner, repo)``, validated the way every GitHub request path is."""
        identity = parse_identity(self.repo_full_name, 1)
        return None if identity is None else (identity[0], identity[1])


class CheckRunEvent(_RepositoryEvent):
    """The slice of a GitHub ``check_run`` webhook a stored check is built from."""

    external_id: int = Field(validation_alias=AliasPath("check_run", "id"))
    head_sha: GithubText = Field("", validation_alias=AliasPath("check_run", "head_sha"))
    name: GithubText = Field("", validation_alias=AliasPath("check_run", "name"))
    status: GithubText = Field("", validation_alias=AliasPath("check_run", "status"))
    conclusion: GithubText = Field("", validation_alias=AliasPath("check_run", "conclusion"))
    details_url: GithubText = Field("", validation_alias=AliasPath("check_run", "details_url"))
    html_url: GithubText = Field("", validation_alias=AliasPath("check_run", "html_url"))
    started_at: datetime | None = Field(None, validation_alias=AliasPath("check_run", "started_at"))
    completed_at: datetime | None = Field(
        None, validation_alias=AliasPath("check_run", "completed_at")
    )
    pull_requests: _PullRequestRefs = Field(
        default_factory=list, validation_alias=AliasPath("check_run", "pull_requests")
    )

    def to_check(self) -> PullRequestCheck:
        return PullRequestCheck(
            head_sha=self.head_sha,
            kind="check_run",
            external_id=str(self.external_id),
            name=self.name,
            status=self.status,
            conclusion=self.conclusion,
            details_url=self.details_url or self.html_url,
            github_updated_at=self.completed_at or self.started_at,
        )


class StatusEvent(_RepositoryEvent):
    """The slice of a GitHub ``status`` webhook a stored check is built from."""

    head_sha: GithubText = Field("", validation_alias="sha")
    context: GithubText = ""
    state: GithubText = ""
    target_url: GithubText = ""
    updated_at: datetime | None = None

    def to_check(self) -> PullRequestCheck:
        return PullRequestCheck(
            head_sha=self.head_sha,
            kind="status",
            external_id=self.context,
            name=self.context,
            conclusion=self.state,
            details_url=self.target_url,
            github_updated_at=self.updated_at,
        )


class ReviewEvent(_RepositoryEvent):
    """The pull request a ``pull_request_review``/``_comment`` webhook is about."""

    number: int | None = Field(None, validation_alias=AliasPath("pull_request", "number"))


async def ingest_pull_request_event(payload: object) -> None:
    """Store the pull request a ``pull_request`` webhook describes."""
    try:
        event = PullRequestEvent.parse(payload)
        pull_request = event.to_pull_request() if event is not None else None
        if event is None or pull_request is None:
            return
        await pull_request.save(repository_private=event.repo_private)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to store pull request from webhook", exc_info=True)


async def ingest_check_event(payload: object, event_type: str) -> None:
    """Record a ``check_run`` or ``status`` against every pull request it covers."""
    try:
        await _ingest_check_event(payload, event_type)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to store check from webhook",
            extra={"github_event": event_type},
            exc_info=True,
        )


async def ingest_review_event(payload: object) -> None:
    """Re-read a pull request's review threads after any review activity."""
    try:
        await _ingest_review_event(payload)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to refresh review threads from webhook", exc_info=True)


async def _ingest_check_event(payload: object, event_type: str) -> None:
    event: CheckRunEvent | StatusEvent | None = (
        CheckRunEvent.parse(payload) if event_type == "check_run" else StatusEvent.parse(payload)
    )
    if event is None or not event.head_sha:
        return
    if isinstance(event, StatusEvent) and not event.context:
        return
    repository = event.repository
    if repository is None:
        return
    owner, repo = repository
    refs = event.pull_requests if isinstance(event, CheckRunEvent) else []
    numbers = [ref.number for ref in refs] or [
        row.number for row in await PullRequest.for_head_sha(owner, repo, event.head_sha)
    ]
    if not numbers:
        logger.debug(
            "No stored pull request matches a check event",
            extra={
                "github_event": event_type,
                "pr_repo_full_name": f"{owner}/{repo}",
                "pr_head_sha": event.head_sha,
            },
        )
        return
    for number in numbers:
        row, _ = await _resolve(owner, repo, number)
        if row is None:
            continue
        await row.upsert_check(event.to_check())


async def _ingest_review_event(payload: object) -> None:
    event = ReviewEvent.parse(payload)
    if event is None or event.number is None:
        return
    repository = event.repository
    if repository is None:
        return
    owner, repo = repository
    row, synced = await _resolve(owner, repo, event.number)
    if row is None or synced:
        return
    token = await get_github_app_installation_token(repositories=[repo])
    if token is None:
        logger.debug(
            "No installation token for a review-thread refresh",
            extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": event.number},
        )
        return
    await sync_review_threads(owner, repo, event.number, token=token)


async def _resolve(owner: str, repo: str, number: int) -> tuple[PullRequest | None, bool]:
    """The stored row, and whether this call had to read it from GitHub first."""
    row = await PullRequest.get(owner, repo, number)
    if row is not None and row.last_synced_at is not None:
        return row, False
    token = await get_github_app_installation_token(repositories=[repo])
    if token is None:
        logger.debug(
            "No installation token to sync a pull request named by a webhook",
            extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
        )
        return row, False
    synced = await sync_pull_request(owner, repo, number, token=token)
    return synced or row, synced is not None
