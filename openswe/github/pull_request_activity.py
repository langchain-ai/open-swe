"""What a person does to a pull request in the dashboard, noted for its agent threads."""

import logging
from typing import Literal

from openswe.github.pull_requests import PullRequest
from openswe.prompts import prompt

logger = logging.getLogger(__name__)

type PullRequestActivity = Literal[
    "merge",
    "close",
    "mark-ready",
    "update-branch",
    "resolve-threads",
    "thread-resolved",
    "thread-unresolved",
    "label-added",
    "label-removed",
    "comment",
    "inline-comment",
    "reply",
    "review",
    "re-review",
    "human-review-requested",
    "human-review-dismissed",
    "merge-queued",
]

_MAX_TEXT = 4000


async def note_pull_request_activity(
    owner: str,
    repo: str,
    number: int,
    login: str,
    activity: PullRequestActivity,
    *,
    text: str = "",
    **details: str | int,
) -> None:
    """Queue what ``login`` did for the PR's threads' next runs; never fails the action."""
    try:
        pull_request = await PullRequest.load(owner, repo, number)
        text = text.strip()
        await pull_request.note_for_threads(
            login,
            prompt(
                "runs/pull-request-activity",
                activity=activity,
                login=login,
                pr_url=pull_request.url,
                text=text[:_MAX_TEXT],
                truncated=len(text) > _MAX_TEXT,
                **details,
            ),
        )
    except Exception:
        logger.exception(
            "Could not note pull request activity for its threads",
            extra={
                "pr_repo_full_name": f"{owner}/{repo}",
                "pr_number": number,
                "pr_activity": activity,
            },
        )
