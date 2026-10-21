"""Decide, with Jev, which pull requests a Slack post shares for review."""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Literal

from langchain_typesafe import Choice

from openswe.prompts import prompt
from openswe.slack.channels import HISTORY_MAX_MESSAGES, SlackChannel
from openswe.slack.client import (
    GitHubPrRef,
    fetch_slack_thread_messages,
    get_slack_user_names,
    parse_github_pr_url,
)
from openswe.slack.payloads import SlackMessage
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_review_pr_url
from openswe.utils.jev import select_jev_choices

logger = logging.getLogger(__name__)

_URL = re.compile(r"https?://[^\s|<>]+", re.I)
_CACHED_POSTS = 256
_CHANNEL_CONTEXT_SECONDS = 24 * 60 * 60
# Jev 1.13 reads 32k tokens per question; four characters to the token, leaving room for the question.
_STATE_MAX_CHARS = 24_000 * 4
_MIN_MESSAGE_CHARS = 200
_CUT = " […] "
_POST_MARKER = ">>> "

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


def _cut_middle(line: str, cap: int) -> str:
    if len(line) <= cap:
        return line
    head = max(cap - len(_CUT), 0) // 2
    return line[:head] + _CUT + line[len(line) - head :]


def _fit(lines: list[str], budget: int) -> list[str]:
    """Cut the middle of the longest lines, then drop the oldest, until ``lines`` fit ``budget``."""
    lines = list(lines)
    while lines:
        room = budget - len(lines)
        if sum(map(len, lines)) <= room:
            return lines
        low, high = 0, max(map(len, lines))
        while low < high:
            cap = (low + high + 1) // 2
            if sum(min(len(line), cap) for line in lines) <= room:
                low = cap
            else:
                high = cap - 1
        if low >= _MIN_MESSAGE_CHARS or len(lines) == 1:
            return [_cut_middle(line, low) for line in lines]
        lines.pop(0)
    return lines


@dataclass(frozen=True)
class PullRequestPost:
    """A person's Slack message linking pull requests."""

    channel_id: str
    slack_user_id: str
    text: str
    message_ts: str
    # The thread the message was posted in, empty for a top-level message.
    reply_thread_ts: str = ""

    def __post_init__(self) -> None:
        if self.reply_thread_ts == self.message_ts:
            object.__setattr__(self, "reply_thread_ts", "")

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
        answers = await select_jev_choices(await self._transcript(), questions=questions)
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

    async def _context(self) -> list[SlackMessage]:
        """The thread up to the post, or the channel's previous day up to it; empty when private."""
        channel = await SlackChannel.load(self.channel_id)
        if channel is None or not channel.public:
            return []
        if not self.reply_thread_ts:
            oldest = f"{float(self.message_ts) - _CHANNEL_CONTEXT_SECONDS:.6f}"
            return await channel.messages(
                HISTORY_MAX_MESSAGES, oldest=oldest, latest=self.message_ts
            )
        replies = [
            message
            for item in await fetch_slack_thread_messages(self.channel_id, self.reply_thread_ts)
            if (message := SlackMessage.parse(item)) is not None and not message.is_noise
        ]
        return sorted(
            (message for message in replies if message.sort_key <= float(self.message_ts)),
            key=lambda message: message.sort_key,
        )

    async def _transcript(self) -> str:
        """The surrounding conversation with the post marked last, cut to fit Jev's context."""
        context = [message for message in await self._context() if message.ts != self.message_ts]
        names = await get_slack_user_names(
            list({message.user for message in context if message.user} | {self.slack_user_id})
        )

        def line(user: str, text: str) -> str:
            return f"{names.get(user) or user or 'bot'}: {' '.join(text.split())}"

        earlier = [line(message.user, message.text or "") for message in context]
        post = _POST_MARKER + line(self.slack_user_id, self.text)
        return "\n".join(_fit([*earlier, post], _STATE_MAX_CHARS))


_classifications: dict[PullRequestPost, asyncio.Future[dict[str, PostIntent | None]]] = {}
