"""Points earned on GitHub: a human's first review of someone else's pull request."""

import logging

from pydantic import BaseModel, ConfigDict, ValidationError

from agent.database import postgres
from agent.points.ledger import Point

logger = logging.getLogger(__name__)


class _GitHubUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    login: str
    type: str = "User"


class _Review(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user: _GitHubUser | None = None


class _PullRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int
    user: _GitHubUser | None = None


class _Owner(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str


class _Repository(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    owner: _Owner


class ReviewSubmitted(BaseModel):
    """GitHub's ``pull_request_review`` webhook with action ``submitted``."""

    model_config = ConfigDict(extra="ignore")

    review: _Review
    pull_request: _PullRequest
    repository: _Repository


async def award_review(payload: dict[str, object]) -> None:
    """Webhook entry point for a submitted pull request review."""
    if not postgres.configured():
        return
    try:
        event = ReviewSubmitted.model_validate(payload)
    except ValidationError:
        logger.warning("GitHub review event has an unexpected shape", exc_info=True)
        return
    reviewer = event.review.user
    author = event.pull_request.user
    if reviewer is None or reviewer.type != "User":
        return
    if author is not None and author.id == reviewer.id:
        return
    await Point.award(
        github_id=reviewer.id,
        github_login=reviewer.login,
        reason="reviewed",
        repository_key=f"{event.repository.owner.login}/{event.repository.name}",
        pr_number=event.pull_request.number,
    )
