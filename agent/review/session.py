"""A user's review of one pull request, listed in the sidebar like a thread.

It lives on the user's review chat thread, so pins, archive, unread state and
chat all belong to that one person, while the row opens the review page.
"""

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, ValidationError

from agent.thread_ids import review_chat_thread_id, review_scout_thread_id
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

REVIEW_CHAT_SOURCE = "review_chat"

WalkthroughState = Literal["building", "ready", "failed"]
PullRequestState = Literal["draft", "open", "merged", "closed"]


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


class ReviewSessionMetadata(BaseModel):
    """The review-specific fields of a review chat thread's metadata."""

    source: str = ""
    github_login: str = ""
    repo_owner: str = ""
    repo_name: str = ""
    pr_number: int = 0
    walkthrough_state: WalkthroughState | None = None
    walkthrough_requested_at_ms: int | None = None
    walkthrough_ready_at_ms: int | None = None
    last_viewed_at_ms: int | None = None

    @classmethod
    def parse(cls, metadata: Mapping[str, object]) -> Self | None:
        """The review fields, or ``None`` for any thread that is not a review chat."""
        if metadata.get("source") != REVIEW_CHAT_SOURCE:
            return None
        try:
            parsed = cls.model_validate(metadata)
        except ValidationError:
            logger.warning("Review chat thread has malformed metadata", exc_info=True)
            return None
        if not parsed.repo_owner or not parsed.repo_name or parsed.pr_number < 1:
            return None
        return parsed

    def owned_by(self, login: str | None) -> bool:
        return bool(login) and self.github_login.lower() == (login or "").lower()

    @property
    def scout_thread_id(self) -> str:
        return review_scout_thread_id(self.repo_owner, self.repo_name, self.pr_number)

    @property
    def unseen_walkthrough(self) -> bool:
        """A walkthrough finished after the user last opened the review page."""
        return self.walkthrough_ready_at_ms is not None and (
            self.last_viewed_at_ms is None or self.walkthrough_ready_at_ms > self.last_viewed_at_ms
        )


class ReviewSession(BaseModel):
    owner: str
    repo: str
    pr_number: int
    login: str

    @property
    def thread_id(self) -> str:
        return review_chat_thread_id(self.owner, self.repo, self.pr_number, self.login)

    async def open(
        self,
        *,
        title: str,
        url: str,
        state: PullRequestState,
        workspace: str | None,
        walkthrough_ready: bool,
        requested_at_ms: int,
    ) -> None:
        """List this review in the user's sidebar, building its walkthrough unless ready."""
        # TODO: reviewer assignment should call this for each assigned reviewer too.
        opened_at_ms = now_ms()
        client = langgraph_client()
        await client.threads.create(
            thread_id=self.thread_id,
            if_exists="do_nothing",
            metadata={
                "kind": REVIEW_CHAT_SOURCE,
                "source": REVIEW_CHAT_SOURCE,
                "github_login": self.login,
                "repo_owner": self.owner,
                "repo_name": self.repo,
                "pr_number": self.pr_number,
                "created_at_ms": opened_at_ms,
            },
        )
        await client.threads.update(
            thread_id=self.thread_id,
            metadata={
                "title": title or f"{self.owner}/{self.repo}#{self.pr_number}",
                "participant_logins": {self.login.lower(): True},
                "thread_category": "review",
                "workspace": workspace,
                "pr_url": url,
                "pr_title": title,
                "pr_state": state,
                "resolved": False,
                "resolved_at_ms": None,
                "updated_at_ms": opened_at_ms,
                "walkthrough_state": "ready" if walkthrough_ready else "building",
                "walkthrough_requested_at_ms": requested_at_ms,
                "walkthrough_ready_at_ms": opened_at_ms if walkthrough_ready else None,
            },
        )
