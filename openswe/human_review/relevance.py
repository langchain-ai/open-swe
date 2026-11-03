"""How closely each pull request on a To Review list concerns its person.

Each row scores why it is on the list plus what PostgreSQL has cached about the
person and the pull request: whether they own its changed files, alone or with
others, and whether they are in the Slack channels its review was posted to or
it was built in. A cache miss scores nothing and queues a refresh for the next read.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, Self
from uuid import UUID

from openswe.github.pull_request_status import OpenPullRequest, ReviewAssignment
from openswe.github.pull_requests import PullRequest
from openswe.human_review.code_owners import CodeOwnerCache
from openswe.human_review.requests import HumanReviewRequest
from openswe.slack.channel_members import SlackChannelMember

Signal = Literal["sole_owner", "owner", "review_channel", "kitchen_channel"]

# Open SWE's own assignment outweighs every other reason put together.
WEIGHTS: Final[Mapping[ReviewAssignment | Signal, int]] = {
    "assigned": 64,
    "sole_owner": 32,
    "requested": 16,
    "owner": 16,
    "tentative": 8,
    "review_channel": 8,
    "mentioned": 4,
    "draft": 4,
    "kitchen_channel": 2,
    "speculative": 0,
}


@dataclass(frozen=True, slots=True)
class Relevance:
    """Each row's signals for one person, by lowercased ``(repo, number)``."""

    signals: Mapping[tuple[str, int], frozenset[Signal]]

    def score(self, row: OpenPullRequest) -> int:
        reasons = self.signals.get(row.key, frozenset())
        return WEIGHTS[row.assignment or "mentioned"] + sum(WEIGHTS[s] for s in reasons)

    @classmethod
    async def of(cls, user_id: UUID, rows: list[OpenPullRequest]) -> Self:
        changed = {
            row.key: datetime.fromisoformat(row.updated_at) if row.updated_at else None
            for row in rows
        }
        owned, built, posted = await asyncio.gather(
            CodeOwnerCache.owned_by(user_id, changed),
            PullRequest.built_in(list(changed)),
            HumanReviewRequest.posted_in(list(changed)),
        )
        joined = await SlackChannelMember.joined(
            user_id, {*built.values(), *(c for channels in posted.values() for c in channels)}
        )
        signals: dict[tuple[str, int], frozenset[Signal]] = {}
        for key in changed:
            found: set[Signal] = set()
            if key in owned:
                found.add("sole_owner" if owned[key] else "owner")
            if posted.get(key, set()) & joined:
                found.add("review_channel")
            if built.get(key) in joined:
                found.add("kitchen_channel")
            signals[key] = frozenset(found)
        return cls(signals)
