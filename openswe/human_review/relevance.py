"""How much each pull request on a To Review list is waiting on its person in particular.

A row scores three things, from what PostgreSQL has cached:

- Obligation: why it is on the list, Open SWE's own assignment above everything.
- Necessity: the changed files they own, divided by one more than the other people who
  could approve them instead, so a sole owner counts fully and one of many barely.
- Context: whether they are in the Slack channel its review was posted to or it was built in.

A cache miss scores nothing and queues a refresh for the next read.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Self
from uuid import UUID

from openswe.github.pull_request_status import OpenPullRequest, ReviewAssignment
from openswe.github.pull_requests import PullRequest
from openswe.human_review.code_owners import CodeOwnerCache
from openswe.human_review.requests import HumanReviewRequest
from openswe.slack.channel_members import SlackChannelMember

OBLIGATION: Final[Mapping[ReviewAssignment, float]] = {
    "assigned": 120,
    "requested": 30,
    "tentative": 15,
    "mentioned": 6,
    "draft": 6,
    "speculative": 0,
}
NECESSITY: Final = 60
REVIEW_CHANNEL: Final = 8
KITCHEN_CHANNEL: Final = 4


@dataclass(frozen=True, slots=True)
class Signals:
    """What the caches say about one pull request and one person."""

    # Others who could approve the files this person owns; ``None`` when they own none.
    alternatives: int | None = None
    in_review_channel: bool = False
    in_kitchen_channel: bool = False

    @property
    def score(self) -> float:
        necessity = NECESSITY / (1 + self.alternatives) if self.alternatives is not None else 0
        return (
            necessity
            + REVIEW_CHANNEL * self.in_review_channel
            + KITCHEN_CHANNEL * self.in_kitchen_channel
        )


@dataclass(frozen=True, slots=True)
class Relevance:
    """Each row's signals for one person, by lowercased ``(repo, number)``."""

    signals: Mapping[tuple[str, int], Signals]

    def score(self, row: OpenPullRequest) -> float:
        signals = self.signals.get(row.key, Signals())
        return OBLIGATION[row.assignment or "mentioned"] + signals.score

    @classmethod
    async def of(cls, user_id: UUID, rows: list[OpenPullRequest]) -> Self:
        changed = {
            row.key: datetime.fromisoformat(row.updated_at) if row.updated_at else None
            for row in rows
        }
        owned, built, posted = await asyncio.gather(
            CodeOwnerCache.alternatives_for(user_id, changed),
            PullRequest.built_in(list(changed)),
            HumanReviewRequest.posted_in(list(changed)),
        )
        joined = await SlackChannelMember.joined(
            user_id, {*built.values(), *(c for channels in posted.values() for c in channels)}
        )
        return cls(
            {
                key: Signals(
                    alternatives=owned.get(key),
                    in_review_channel=bool(posted.get(key, set()) & joined),
                    in_kitchen_channel=built.get(key) in joined,
                )
                for key in changed
            }
        )
