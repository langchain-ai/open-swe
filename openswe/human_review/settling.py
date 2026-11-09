"""Queued settles: one run per pull request however many webhooks ask for it."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Self

from openswe.human_review.requests import HumanReviewRequest, RequestKind
from openswe.jobs import queue
from openswe.jobs.queue import Job

SETTLE_JOB: Final = "human_review_settle"
SETTLED_KINDS: tuple[RequestKind, ...] = ("standard", "posted")
# Short enough to feel immediate, long enough for a burst of check webhooks to share a run.
_SETTLE_DELAY: Final = timedelta(seconds=2)


@dataclass(frozen=True, slots=True)
class SettleTarget:
    """A pull request, or with no ``number`` a whole repository, whose open requests need settling.

    One push finishes hundreds of checks, and each one's webhook names the same pull
    request. Settling it per webhook queued them all on the request's row lock, each
    holding a database connection while it waited, until the pool ran out. They share
    one queued job per target instead.
    """

    owner: str
    repo: str
    number: int | None = None

    @classmethod
    def from_job(cls, job: Job) -> Self | None:
        owner, repo, number = (job.payload.get(name) for name in ("owner", "repo", "number"))
        if not isinstance(owner, str) or not isinstance(repo, str):
            return None
        return cls(owner, repo, number if isinstance(number, int) else None)

    @classmethod
    async def queue(
        cls, owner: str, repo: str, number: int | None = None, *, after: timedelta = _SETTLE_DELAY
    ) -> None:
        """Settle soon, sharing a run with every other event for the same target."""
        if number is None:
            if not await HumanReviewRequest.open_in_repository(owner, repo, kinds=SETTLED_KINDS):
                return
        elif await HumanReviewRequest.active_for(owner, repo, number) is None:
            return
        target = cls(owner, repo, number)
        await queue.coalesce(
            SETTLE_JOB,
            {"owner": owner, "repo": repo, "number": number},
            key=target.key,
            due_at=datetime.now(UTC) + after,
        )

    @property
    def key(self) -> str:
        target = f"{self.owner}/{self.repo}".lower()
        return f"{SETTLE_JOB}:{target}" + (f"#{self.number}" if self.number is not None else "")
