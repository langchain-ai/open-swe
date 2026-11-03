"""Live pull request cards in Open SWE's Slack messages.

A reply that writes ``{{pr-card <GitHub PR URL>}}`` shows that pull request as a card
in its place: its state, checks, reviews, conversations and mergeability. Open SWE
records every card it posts and redraws it from the pull request's webhooks for
``LIVE_FOR``; then the card says, in its footer, that it no longer updates.
"""

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final, Literal, Self
from urllib.parse import quote
from uuid import UUID, uuid7

from pydantic import BaseModel

from openswe.github.pull_request_status import OpenPullRequest, PullRequestClient
from openswe.github.pull_requests import PullRequest, PullRequestPayload
from openswe.slack.blocks import (
    CONTAINER_TITLE_MAX_CHARS,
    MARKDOWN_TEXT_MAX_CHARS,
    MESSAGE_MAX_BLOCKS,
    Block,
    ButtonElement,
    ContainerBlock,
    ContainerChild,
    actions,
    block_payload,
    button,
    context,
    escape,
    mrkdwn,
    section,
)
from openswe.slack.client import (
    fetch_slack_thread_message_by_ts,
    parse_github_pr_url,
    update_slack_message,
)
from openswe.slack.http import SlackRequestError
from openswe.slack.markdown import markdown_blocks
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_review_url
from openswe.webhooks.event_listeners import EventHandler, EventListener

logger = logging.getLogger(__name__)

LIVE_FOR: Final = timedelta(days=3)
MAX_CARDS: Final = 10
# Webhooks arrive in bursts (a push sets off every check); one redraw covers a burst.
_REFRESH_DELAY: Final = timedelta(seconds=5)
_BLOCK_ID_PREFIX: Final = "open_swe_pr_card:"
_BUTTON_ID: Final = "open_swe_pr_card"
_MARKER: Final = re.compile(r"\{\{\s*pr-card\s+<?(?P<url>[^\s{}<>|]+)(?:\|[^{}<>]*)?>?\s*\}\}")
_NAMES_SHOWN: Final = 3

CardState = Literal["open", "draft", "merged", "closed"]

_STATE_LOOKS: Final[dict[CardState, tuple[str, str]]] = {
    "open": (":large_green_circle:", "Open"),
    "draft": (":white_circle:", "Draft"),
    "merged": (":large_purple_circle:", "Merged"),
    "closed": (":red_circle:", "Closed"),
}


class _Login(BaseModel):
    login: str


class _Named(BaseModel):
    name: str


class CardPull(PullRequestPayload):
    """What a card shows of GitHub's pull request object beyond what a PR row keeps."""

    merged_at: datetime | None = None
    closed_at: datetime | None = None
    merged_by: _Login | None = None
    labels: list[_Named] = []
    requested_reviewers: list[_Login] = []
    requested_teams: list[_Named] = []


@dataclass(frozen=True, slots=True)
class PullRequestCard:
    """One pull request as GitHub described it at ``read_at``, drawn as Block Kit."""

    record: PullRequest
    pull: CardPull
    status: OpenPullRequest | None
    read_at: datetime

    @classmethod
    async def read(cls, record: PullRequest) -> Self:
        """Read the pull request as the GitHub App; raises when GitHub cannot answer."""
        async with PullRequestClient.as_app(record.owner, record.repo, record.number) as client:
            pull = await client.mergeable_pull()
            if pull is None:
                raise ValueError("GitHub did not answer the pull request")
            status = await client.load(pull=pull)
        return cls(record, CardPull.model_validate(pull), status, datetime.now(UTC))

    @property
    def state(self) -> CardState:
        if self.pull.merged or self.pull.merged_at is not None:
            return "merged"
        if self.pull.state == "closed":
            return "closed"
        return "draft" if self.pull.draft else "open"

    @property
    def fallback(self) -> str:
        """The card as a Markdown link, for notifications and clients without blocks."""
        title = self.pull.title.replace("[", "(").replace("]", ")")
        return f"[{self.record.label}: {title}]({self.record.url})"

    def block(self, card_id: UUID, *, live_until: datetime | None) -> ContainerBlock:
        """The card as a full-width container, tagged with ``card_id`` so a redraw can find it.

        ``live_until`` is when its live updates end; ``None`` for a card nobody updates.
        """
        pull = self.pull
        children: list[ContainerChild] = []
        if lines := self._status_lines():
            children.append(section("\n".join(lines)))
        children.append(context(self._details(live_until)))
        children.append(actions(*self._buttons()))
        title = pull.title
        if len(title) > CONTAINER_TITLE_MAX_CHARS:
            title = title[: CONTAINER_TITLE_MAX_CHARS - 1].rstrip() + "…"
        drawn: ContainerBlock = {
            "type": "container",
            "block_id": f"{_BLOCK_ID_PREFIX}{card_id}",
            "width": "full",
            "has_header_divider": True,
            "rich_text_title": {
                "type": "rich_text",
                "elements": [
                    {
                        "type": "rich_text_section",
                        "elements": [
                            {
                                "type": "link",
                                "url": self.record.url,
                                "text": title,
                                "style": {"bold": True},
                            }
                        ],
                    }
                ],
            },
            "subtitle": mrkdwn(self._state_line()),
            "child_blocks": children,
        }
        if pull.author:
            path = f"u/{pull.author_id}" if pull.author_id is not None else quote(pull.author)
            drawn["icon"] = {
                "type": "image",
                "image_url": f"https://avatars.githubusercontent.com/{path}?size=72",
                "alt_text": pull.author,
            }
        return drawn

    def _state_line(self) -> str:
        pull = self.pull
        emoji, word = _STATE_LOOKS[self.state]
        if self.state == "merged" and pull.merged_at is not None:
            word = f"Merged {self._when(pull.merged_at, time=False)}"
        elif self.state == "closed" and pull.closed_at is not None:
            word = f"Closed {self._when(pull.closed_at, time=False)}"
        line = f"{emoji} {word} · {escape(self.record.label)}"
        author = f" by {escape(pull.author)}"
        return line + author if pull.author else line

    def _details(self, live_until: datetime | None) -> str:
        """Diffstat, branches and labels, then whether the card is live."""
        pull = self.pull
        parts: list[str] = []
        if pull.additions is not None and pull.deletions is not None:
            files = (
                f" in {self._plural(pull.changed_files, 'file')}"
                if pull.changed_files is not None
                else ""
            )
            parts.append(f"+{pull.additions} −{pull.deletions}{files}")
        if pull.head_ref and pull.base_ref:
            parts.append(f"`{escape(pull.head_ref)}` → `{escape(pull.base_ref)}`")
        if pull.labels:
            parts.append(f":label: {self._names([label.name for label in pull.labels])}")
        return " · ".join([*parts, self._footer(live_until)])

    def _buttons(self) -> list[ButtonElement]:
        buttons: list[ButtonElement] = []
        if review := dashboard_review_url(self.record.owner, self.record.repo, self.record.number):
            buttons.append(button("Review", action_id=f"{_BUTTON_ID}_review", url=review))
        buttons.append(button("GitHub", action_id=f"{_BUTTON_ID}_github", url=self.record.url))
        return buttons

    def _status_lines(self) -> list[str]:
        """What stands between an open pull request and its merge; nothing once it is done."""
        status = self.status
        if self.state in {"merged", "closed"} or status is None:
            return []
        if not status.status_available:
            return [":grey_question: Status unavailable"]
        return [
            *self._check_lines(status),
            *self._review_lines(status),
            *self._merge_lines(status),
        ]

    def _check_lines(self, status: OpenPullRequest) -> list[str]:
        lines: list[str] = []
        if failing := status.failing_checks:
            heading = (
                f"*{escape(failing[0])}* failing"
                if len(failing) == 1
                else f"*{len(failing)} checks failing*: {self._names(failing)}"
            )
            lines.append(f":x: {heading}")
        if missing := status.missing_checks:
            lines.append(f":grey_question: {self._plural(len(missing), 'required check')} missing")
        if pending := status.pending_checks:
            lines.append(f":hourglass_flowing_sand: {self._plural(len(pending), 'check')} running")
        elif status.ci == "passing" and not failing:
            lines.append(":white_check_mark: Checks passing")
        elif status.ci == "unknown":
            lines.append(":grey_question: Checks inconclusive")
        return lines

    def _review_lines(self, status: OpenPullRequest) -> list[str]:
        reviewers = status.reviewers or []
        changes = [r.login for r in reviewers if r.state == "changes_requested"]
        approved = [r.login for r in reviewers if r.state == "approved"]
        waiting = [r.login for r in self.pull.requested_reviewers] + [
            f"@{team.name}" for team in self.pull.requested_teams
        ]
        lines: list[str] = []
        if changes:
            lines.append(f":warning: Changes requested by {self._names(changes)}")
        if approved:
            lines.append(f":white_check_mark: Approved by {self._names(approved)}")
        if waiting:
            lines.append(f":eyes: Waiting on {self._names(waiting)}")
        elif status.review_required and not changes and self.state == "open":
            lines.append(":eyes: Needs an approving review")
        elif not reviewers and self.state == "open":
            lines.append(":eyes: No reviews yet")
        if unresolved := status.unresolved_threads:
            lines.append(f":speech_balloon: {self._plural(unresolved, 'unresolved comment')}")
        return lines

    def _merge_lines(self, status: OpenPullRequest) -> list[str]:
        base = f"`{escape(self.pull.base_ref)}`" if self.pull.base_ref else "base"
        if status.mergeable is False or status.merge_state == "dirty":
            return [f":warning: Conflicts with {base}"]
        lines: list[str] = []
        if status.merge_state == "behind":
            lines.append(f":arrow_down: Behind {base}")
        if self.state == "draft":
            return lines
        if status.auto_merge:
            lines.append(":hourglass: Auto-merges once it passes")
        elif status.merge_state in {"clean", "has_hooks"}:
            lines.append(":rocket: Ready to merge")
        return lines

    @staticmethod
    def _when(moment: datetime, *, time: bool = True) -> str:
        """A time each reader sees in their own time zone."""
        shown = "{date_short_pretty} at {time}" if time else "{date_short_pretty}"
        return f"<!date^{int(moment.timestamp())}^{shown}|{moment:%b %-d}>"

    @staticmethod
    def _names(names: Sequence[str]) -> str:
        shown = ", ".join(escape(name) for name in names[:_NAMES_SHOWN])
        rest = len(names) - _NAMES_SHOWN
        return f"{shown} +{rest}" if rest > 0 else shown

    @staticmethod
    def _plural(count: int, noun: str) -> str:
        return f"{count} {noun}" if count == 1 else f"{count} {noun}s"

    def _footer(self, live_until: datetime | None) -> str:
        if live_until is None:
            return f"As of {self._when(self.read_at)}"
        if live_until > self.read_at:
            return f"Live until {self._when(live_until, time=False)}"
        return f"No longer updating · as of {self._when(self.read_at, time=False)}"


@dataclass(slots=True)
class CardedMessage:
    """A reply's Markdown with its ``{{pr-card …}}`` markers read into cards.

    ``parts`` holds the Markdown between cards and each card with the id it is drawn
    and tracked under. ``unavailable`` names the pull requests no card could be read for;
    those stay in the text as links.
    """

    parts: list[str | tuple[UUID, PullRequestCard]]
    unavailable: list[str] = field(default_factory=list)

    @classmethod
    async def parse(cls, message: str) -> Self:
        """Read every pull request ``message`` asks a card for; one with none reads nothing."""
        matches = list(_MARKER.finditer(message))
        if not matches:
            return cls([message])
        refs = {
            ref.url.lower(): ref
            for match in matches
            if (ref := parse_github_pr_url(match["url"])) is not None and ref.number > 0
        }
        urls = list(refs)[:MAX_CARDS]
        results = await asyncio.gather(
            *(cls._read(refs[url].owner, refs[url].repo, refs[url].number) for url in urls),
            return_exceptions=True,
        )
        cards: dict[str, PullRequestCard] = {}
        unavailable: list[str] = []
        for url, result in zip(urls, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning(
                    "Could not read a pull request for its Slack card",
                    extra={"pr_url": url},
                    exc_info=result,
                )
                unavailable.append(refs[url].url)
            else:
                cards[url] = result
        parts: list[str | tuple[UUID, PullRequestCard]] = []
        prose = ""
        start = 0
        for match in matches:
            ref = parse_github_pr_url(match["url"])
            card = cards.pop(ref.url.lower(), None) if ref is not None else None
            prose += message[start : match.start()]
            if card is not None:
                parts.extend([prose, (uuid7(), card)])
                prose = ""
            else:
                prose += ref.url if ref else match["url"]
            start = match.end()
        parts.append(prose + message[start:])
        return cls(parts, unavailable)

    @staticmethod
    async def _read(owner: str, repo: str, number: int) -> PullRequestCard:
        record = await PullRequest(owner=owner, repo=repo, number=number).ensure()
        return await PullRequestCard.read(record)

    @property
    def cards(self) -> list[tuple[UUID, PullRequestCard]]:
        return [part for part in self.parts if isinstance(part, tuple)]

    @property
    def text(self) -> str:
        """The message with each card as a link, for the notification and the transcript."""
        return "".join(part if isinstance(part, str) else part[1].fallback for part in self.parts)

    def blocks(self, *, live_until: datetime | None) -> list[Block] | None:
        """Markdown and cards in order; ``None`` when they cannot fit in one message."""
        if not self.cards:
            return markdown_blocks(self.text)
        prose = [part.strip("\n") for part in self.parts if isinstance(part, str)]
        budget = MARKDOWN_TEXT_MAX_CHARS if sum(map(len, prose)) <= MARKDOWN_TEXT_MAX_CHARS else 0
        blocks: list[Block] = []
        for part in self.parts:
            if isinstance(part, tuple):
                blocks.append(part[1].block(part[0], live_until=live_until))
            elif part.strip():
                drawn = markdown_blocks(part.strip("\n"), budget=budget)
                if drawn is None:
                    return None
                blocks.extend(drawn)
        return blocks if len(blocks) <= MESSAGE_MAX_BLOCKS else None

    async def track(
        self, channel_id: str, thread_ts: str, message_ts: str, *, requester: str | None
    ) -> None:
        """Keep the posted cards live until ``live_until``; ``requester`` is a Slack user id.

        A card that cannot be tracked stays posted as it was drawn, so this only logs.
        """
        if self.live_until is None:
            return
        try:
            person = await User.for_identity("slack", requester) if requester else None
            await SlackPullRequestCards.track(
                self.cards,
                CardPlacement(channel_id=channel_id, thread_ts=thread_ts, message_ts=message_ts),
                live_until=self.live_until,
                by=person.id if person else None,
            )
        except Exception:
            logger.exception(
                "Could not keep Slack PR cards live",
                extra={"slack_channel": channel_id, "slack_message_ts": message_ts},
            )

    @property
    def live_until(self) -> datetime | None:
        """When the cards stop updating; ``None`` without cards."""
        cards = self.cards
        return min(card.read_at for _, card in cards) + LIVE_FOR if cards else None

    @property
    def report(self) -> dict[str, object]:
        """What the agent should know about cards it asked for and did not get."""
        if not self.unavailable:
            return {}
        return {
            "pr_cards_unavailable": self.unavailable,
            "pr_cards_hint": (
                "Open SWE's GitHub App could not read these pull requests, so they were posted "
                "as plain links instead of cards."
            ),
        }


class CardPlacement(BaseModel):
    """Where a posted card sits: its Slack message."""

    channel_id: str
    thread_ts: str
    message_ts: str


class SlackPullRequestCards(EventHandler):
    """Redraws posted cards when their pull request changes, and once more when they expire."""

    name = "slack_pr_card"
    state = CardPlacement
    delay = _REFRESH_DELAY

    @classmethod
    async def track(
        cls,
        cards: Sequence[tuple[UUID, PullRequestCard]],
        placement: CardPlacement,
        *,
        live_until: datetime,
        by: UUID | None,
    ) -> None:
        for card_id, card in cards:
            await EventListener.start(
                cls,
                placement,
                card.record.id,
                expires_at=live_until,
                by=by,
                listener_id=card_id,
            )

    async def heard(self, listeners: Sequence[EventListener]) -> None:
        drawn = await PullRequestCard.read(listeners[0].pull_request)
        for listener in listeners:
            await self.show(listener, drawn)

    async def expired(self, listener: EventListener) -> None:
        await self.show(listener, await PullRequestCard.read(listener.pull_request))

    async def show(self, listener: EventListener, drawn: PullRequestCard) -> None:
        """Put ``drawn`` in place of the listener's card, leaving the rest of its message alone."""
        placement = listener.state_as(CardPlacement)
        message = await fetch_slack_thread_message_by_ts(
            placement.channel_id, placement.thread_ts, placement.message_ts
        )
        blocks = message.get("blocks") if message is not None else None
        text = message.get("text") if message is not None else None
        if not isinstance(blocks, list) or not isinstance(text, str):
            logger.info(
                "Slack PR card message is gone or unreadable",
                extra={
                    "slack_channel": placement.channel_id,
                    "slack_message_ts": placement.message_ts,
                },
            )
            return
        block_id = f"{_BLOCK_ID_PREFIX}{listener.id}"
        mine = next(
            (
                index
                for index, block in enumerate(blocks)
                if isinstance(block, dict) and block.get("block_id") == block_id
            ),
            None,
        )
        if mine is None:
            await listener.forget()
            return
        updated = list(blocks)
        updated[mine] = block_payload([drawn.block(listener.id, live_until=listener.expires_at)])[0]
        try:
            await update_slack_message(
                placement.channel_id, placement.message_ts, text, blocks=updated
            )
        except SlackRequestError as exc:
            if exc.code in {"message_not_found", "channel_not_found", "cant_update_message"}:
                await listener.forget()
                return
            raise
