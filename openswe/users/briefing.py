"""A person's work in flight, kept current in their concierge conversation."""

import asyncio
from typing import Self

from pydantic import BaseModel

from openswe.github.http import GitHubClient
from openswe.github.pull_request_status import OpenPullRequests, list_open_pull_requests
from openswe.input_messages import RunMessage, briefing_introduction

_PULL_REQUESTS_SHOWN = 10


class Briefing(BaseModel):
    """The open PRs a person authored and the ones waiting on their review."""

    person_id: str
    open_prs: OpenPullRequests
    review_requests: OpenPullRequests

    @classmethod
    async def load(cls, person_id: str, login: str) -> Self:
        """Read as ``login``, so it shows only what they can see on GitHub."""
        async with GitHubClient.as_user(login) as github:
            open_prs, review_requests = await asyncio.gather(
                list_open_pull_requests(github, login, per_page=_PULL_REQUESTS_SHOWN),
                list_open_pull_requests(
                    github, login, per_page=_PULL_REQUESTS_SHOWN, scope="review-requested"
                ),
            )
        return cls(person_id=person_id, open_prs=open_prs, review_requests=review_requests)

    def introduction(self) -> RunMessage:
        return briefing_introduction(
            {
                "id": self.person_id,
                "open_prs": self.open_prs.standings(),
                "review_requests": self.review_requests.standings(),
            }
        )
