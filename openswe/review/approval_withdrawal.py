"""Withdrawing Robin approvals that the pull request's head or latest review no longer supports."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Final

from pydantic import BaseModel

from openswe.github.http import GitHubClient
from openswe.github.pull_request_status import PullRequestClient
from openswe.jobs import queue
from openswe.jobs.queue import Job

logger = logging.getLogger(__name__)

_RETRY_DELAY: Final = timedelta(minutes=1)


class ApprovalWithdrawal(BaseModel):
    """Dismiss Robin's approvals of anything but the live head; a refusal is retried from the job queue."""

    owner: str
    repo: str
    number: int
    # Keep approvals of the head GitHub reports when this runs; otherwise dismiss them all.
    keep_head: bool = True
    # The review this follows: only its login's approvals, and none newer than it, are dismissed.
    review_id: int | None = None

    JOB: ClassVar[str] = "review_approval_withdrawal"

    @property
    def reason(self) -> str:
        if self.keep_head:
            return "New commits were pushed after Robin approved this pull request."
        return "Robin's latest review of this pull request does not approve it."

    async def run(self, pull: PullRequestClient) -> str:
        """Dismiss the approvals and return the live head; raises unless GitHub dismissed them all."""
        head = (await pull.pull()).get("head")
        if not isinstance(head, dict) or not isinstance(sha := head.get("sha"), str):
            raise ValueError("GitHub answered the pull request without its head")
        refused = await pull.withdraw_open_swe_approvals(
            reason=self.reason,
            keep_commit=sha if self.keep_head else None,
            scope_to_review_id=self.review_id,
        )
        if refused:
            raise RuntimeError(f"GitHub did not dismiss Robin reviews {refused}")
        return sha

    async def attempt(self, *, token: str | None = None) -> str | None:
        """Run now, as ``token`` or else the App; the live head, or ``None`` once queued to retry."""
        try:
            async with self.pull_request(token) as pull:
                return await self.run(pull)
        except Exception:
            logger.warning(
                "Robin approvals may still stand; retrying their withdrawal",
                extra={"repository": f"{self.owner}/{self.repo}", "pr_number": self.number},
                exc_info=True,
            )
        await self.queue()
        return None

    async def queue(self, *, delay: timedelta = _RETRY_DELAY) -> Job:
        return await queue.schedule(
            self.JOB, self.model_dump(mode="json"), due_at=datetime.now(UTC) + delay
        )

    @classmethod
    async def retry(cls, job: Job) -> None:
        """The queued withdrawal; raising retries it with backoff until the queue gives up and logs it."""
        withdrawal = cls.model_validate(job.payload)
        async with withdrawal.pull_request(None) as pull:
            await withdrawal.run(pull)

    @asynccontextmanager
    async def pull_request(self, token: str | None) -> AsyncGenerator[PullRequestClient]:
        if token is None:
            async with PullRequestClient.as_app(self.owner, self.repo, self.number) as pull:
                yield pull
            return
        async with GitHubClient.connect(token=token) as github:
            yield github.repo(self.owner, self.repo).pull_request(self.number)
