"""Decide, with Jev, which pull requests a Slack post shares for review."""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Literal

from langchain_typesafe import Choice

from openswe.prompts import prompt
from openswe.slack.client import GitHubPrRef, parse_github_pr_url
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_review_pr_url
from openswe.utils.jev import select_jev_choices

logger = logging.getLogger(__name__)

_URL = re.compile(r"https?://[^\s|<>]+", re.I)
_CACHED_POSTS = 256

PostIntent = Literal["review", "not_ready", "other", "investigate"]
_CRITERIA: dict[PostIntent, str] = {
    "review": "slack/pr-post-intent-review",
    "not_ready": "slack/pr-post-intent-not-ready",
    "other": "slack/pr-post-intent-other",
    "investigate": "slack/pr-post-intent-investigate",
}


def linked_pull_requests(text: str) -> list[GitHubPrRef]:
    """Distinct GitHub PRs linked directly or through this deployment's review page."""
    refs: dict[str, GitHubPrRef] = {}
    for match in _URL.finditer(text):
        url = match.group().rstrip(".,;:!?)]\"'")
        try:
            ref = parse_github_pr_url(dashboard_review_pr_url(url) or url)
        except ValueError:
            logger.debug("Ignoring malformed PR link", exc_info=True)
            continue
        if ref is not None and ref.number > 0:
            refs[ref.url.lower()] = ref
    return list(refs.values())


@dataclass(frozen=True)
class PullRequestPost:
    """A person's Slack message linking pull requests."""

    channel_id: str
    slack_user_id: str
    text: str

    async def intent(self, ref: GitHubPrRef) -> PostIntent | None:
        """Jev's reading of why ``ref`` was posted.

        Only posters with failing-check reactions on are classified; ``None`` for
        everyone else, and whenever Jev has no confident answer.
        """
        return (await self._intents()).get(ref.url.lower())

    async def shares_for_review(self, ref: GitHubPrRef) -> bool:
        """Whether to treat ``ref`` as posted for review right away."""
        return await self.intent(ref) in (None, "review")

    async def _intents(self) -> dict[str, PostIntent | None]:
        # The watch and the review offer both ask about the same post, so they share one call.
        task = _classifications.get(self)
        if task is None:
            task = asyncio.ensure_future(self._classify())
            _classifications[self] = task
            while len(_classifications) > _CACHED_POSTS:
                _classifications.pop(next(iter(_classifications)))
        return await asyncio.shield(task)

    async def _classify(self) -> dict[str, PostIntent | None]:
        user = await User.for_identity("slack", self.slack_user_id)
        if user is None or not user.typed_preferences.pr_failure_reactions:
            return {}
        refs = linked_pull_requests(self.text)
        if not refs:
            return {}
        criteria: dict[str, str] = {intent: prompt(name) for intent, name in _CRITERIA.items()}
        questions = {
            f"pr_{index}": Choice(
                instructions=prompt("slack/pr-post-intent-instructions", url=ref.url),
                criteria=criteria,
            )
            for index, ref in enumerate(refs)
        }
        answers = await select_jev_choices(self.text, questions=questions)
        intents: dict[str, PostIntent | None] = {
            ref.url.lower(): next(
                (intent for intent in _CRITERIA if intent == answers[f"pr_{index}"]), None
            )
            for index, ref in enumerate(refs)
        }
        logger.info(
            "Classified a pull request post",
            extra={"slack_channel": self.channel_id, "pr_post_intents": intents},
        )
        return intents


_classifications: dict[PullRequestPost, asyncio.Future[dict[str, PostIntent | None]]] = {}
