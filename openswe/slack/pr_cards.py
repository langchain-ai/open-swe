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
from uuid import UUID, uuid7

from pydantic import BaseModel
from sqlalchemy import ForeignKey, Index, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column, relationship

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.pull_request_key import PullRequestKey
from openswe.github.pull_request_status import OpenPullRequest, PullRequestClient
from openswe.github.pull_requests import PullRequest, PullRequestPayload
from openswe.github.repositories import Repository
from openswe.jobs import queue
from openswe.slack.blocks import (
    MARKDOWN_TEXT_MAX_CHARS,
    MESSAGE_MAX_BLOCKS,
    Block,
    ContextBlock,
    SectionBlock,
    block_payload,
    context,
    escape,
    section,
)
from openswe.slack.channels import SlackChannel
from openswe.slack.client import (
    fetch_slack_thread_message_by_ts,
    parse_github_pr_url,
    update_slack_message,
)
from openswe.slack.http import SlackRequestError
from openswe.slack.markdown import markdown_blocks
from openswe.users import User

logger = logging.getLogger(__name__)

LIVE_FOR: Final = timedelta(days=3)
REFRESH_JOB: Final = "slack_pr_card_refresh"
EXPIRE_JOB: Final = "slack_pr_card_expire"
MAX_CARDS: Final = 10
# Webhooks arrive in bursts (a push sets off every check); one redraw covers a burst.
_REFRESH_DELAY: Final = timedelta(seconds=5)
_BLOCK_ID_PREFIX: Final = "open_swe_pr_card:"
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

    def blocks(self, card_id: UUID, *, live_until: datetime | None) -> list[Block]:
        """The card, every block tagged with ``card_id`` so a redraw can find it.

        ``live_until`` is when its live updates end; ``None`` for a card nobody updates.
        """
        emoji, _ = _STATE_LOOKS[self.state]
        drawn: list[SectionBlock | ContextBlock] = [
            section(f"{emoji}  *<{self.record.url}|{escape(self.pull.title)}>*"),
            context(self._summary()),
        ]
        if lines := self._status_lines():
            drawn.append(section("\n".join(lines)))
        drawn.append(context(self._footer(live_until)))
        blocks: list[Block] = []
        for index, block in enumerate(drawn):
            block["block_id"] = f"{_BLOCK_ID_PREFIX}{card_id}:{index}"
            blocks.append(block)
        return blocks

    def _summary(self) -> str:
        pull = self.pull
        _, word = _STATE_LOOKS[self.state]
        if self.state == "merged" and pull.merged_at is not None:
            by = f" by {escape(pull.merged_by.login)}" if pull.merged_by else ""
            word = f"Merged{by} {self._when(pull.merged_at)}"
        elif self.state == "closed" and pull.closed_at is not None:
            word = f"Closed {self._when(pull.closed_at)}"
        label = f"*{escape(self.record.label)}*"
        parts = [f"{label} by {escape(pull.author)}" if pull.author else label, word]
        if pull.head_ref and pull.base_ref:
            parts.append(f"`{escape(pull.head_ref)}` → `{escape(pull.base_ref)}`")
        if pull.additions is not None and pull.deletions is not None:
            files = (
                f" in {self._plural(pull.changed_files, 'file')}"
                if pull.changed_files is not None
                else ""
            )
            parts.append(f"+{pull.additions} −{pull.deletions}{files}")
        if pull.labels:
            parts.append(f":label: {self._names([label.name for label in pull.labels])}")
        return "  ·  ".join(parts)

    def _status_lines(self) -> list[str]:
        """What stands between an open pull request and its merge; nothing once it is done."""
        status = self.status
        if self.state in {"merged", "closed"} or status is None:
            return []
        if not status.status_available:
            return [":grey_question: Status unavailable from GitHub"]
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
            lines.append(
                f":grey_question: {self._plural(len(missing), 'required check')} not reported: "
                f"{self._names(missing)}"
            )
        if pending := status.pending_checks:
            lines.append(f":hourglass_flowing_sand: {self._plural(len(pending), 'check')} running")
        elif status.ci == "passing" and not failing:
            lines.append(":white_check_mark: Checks passing")
        elif status.ci == "unknown":
            lines.append(":grey_question: Checks finished without a clear result")
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
            lines.append(f":eyes: Waiting on review from {self._names(waiting)}")
        elif status.review_required and not changes:
            lines.append(":eyes: Needs an approving review")
        elif not reviewers and self.state == "open":
            lines.append(":eyes: No reviews yet")
        if unresolved := status.unresolved_threads:
            lines.append(f":speech_balloon: {self._plural(unresolved, 'unresolved conversation')}")
        return lines

    def _merge_lines(self, status: OpenPullRequest) -> list[str]:
        base = f"`{escape(self.pull.base_ref)}`" if self.pull.base_ref else "the base branch"
        if status.mergeable is False or status.merge_state == "dirty":
            return [f":warning: Merge conflicts with {base}"]
        lines: list[str] = []
        if status.merge_state == "behind":
            lines.append(f":arrow_down: Behind {base}")
        if self.state == "draft":
            return lines
        if status.auto_merge:
            lines.append(":hourglass: Merges automatically once everything passes")
        elif status.merge_state in {"clean", "has_hooks"}:
            lines.append(":rocket: Ready to merge")
        return lines

    @staticmethod
    def _when(moment: datetime) -> str:
        """A time each reader sees in their own time zone."""
        fallback = f"{moment:%b %-d, %Y %H:%M} UTC"
        return f"<!date^{int(moment.timestamp())}^{{date_short_pretty}} at {{time}}|{fallback}>"

    @staticmethod
    def _names(names: Sequence[str]) -> str:
        shown = ", ".join(escape(name) for name in names[:_NAMES_SHOWN])
        rest = len(names) - _NAMES_SHOWN
        return f"{shown} and {rest} more" if rest > 0 else shown

    @staticmethod
    def _plural(count: int, noun: str) -> str:
        return f"{count} {noun}" if count == 1 else f"{count} {noun}s"

    def _footer(self, live_until: datetime | None) -> str:
        if live_until is None:
            return f"As of {self._when(self.read_at)}"
        if live_until > self.read_at:
            return f"Updating live until {self._when(live_until)}"
        return f"No longer updating  ·  as of {self._when(self.read_at)}"


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
                blocks.extend(part[1].blocks(part[0], live_until=live_until))
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
            await SlackPullRequestCard.record(
                self.cards,
                channel_id,
                thread_ts,
                message_ts,
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


class SlackPullRequestCard(Base):
    """A posted card Open SWE redraws when its pull request changes, until ``live_until``."""

    __tablename__ = "slack_pull_request_card"
    __table_args__ = (
        Index(
            "slack_pull_request_card_message",
            "channel_id",
            "message_ts",
            "pull_request_id",
            unique=True,
        ),
        Index("slack_pull_request_card_live", "pull_request_id", "live_until"),
    )

    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("pull_request.id", ondelete="CASCADE"))
    channel_id: Mapped[str] = mapped_column(ForeignKey("slack_channel.id"))
    thread_ts: Mapped[str]
    message_ts: Mapped[str]
    live_until: Mapped[datetime]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    pull_request: Mapped[PullRequest] = relationship(init=False, lazy="joined")

    @property
    def live(self) -> bool:
        return self.live_until > datetime.now(UTC)

    @classmethod
    async def record(
        cls,
        cards: Sequence[tuple[UUID, PullRequestCard]],
        channel_id: str,
        thread_ts: str,
        message_ts: str,
        *,
        live_until: datetime,
        by: UUID | None,
    ) -> None:
        async with postgres.session() as session:
            await SlackChannel.ensure(session, channel_id)
            await session.execute(
                insert(cls)
                .values(
                    [
                        {
                            "id": card_id,
                            "pull_request_id": card.record.id,
                            "channel_id": channel_id,
                            "thread_ts": thread_ts,
                            "message_ts": message_ts,
                            "live_until": live_until,
                            "created_by": by,
                            "updated_by": by,
                        }
                        for card_id, card in cards
                    ]
                )
                .on_conflict_do_nothing()
            )
        for card_id, _ in cards:
            await queue.schedule(
                EXPIRE_JOB,
                {"card_id": str(card_id)},
                due_at=live_until,
                key=f"{EXPIRE_JOB}:{card_id}",
            )

    @classmethod
    async def pull_request_changed(cls, key: PullRequestKey) -> None:
        """Queue one redraw of the pull request's live cards, if it has any."""
        if not postgres.configured():
            return
        number = int(key.rsplit("/", 1)[1])
        try:
            async with postgres.session() as session:
                pull_request_id = await session.scalar(
                    select(cls.pull_request_id)
                    .join(PullRequest, PullRequest.id == cls.pull_request_id)
                    .join(Repository, Repository.id == PullRequest.repository_id)
                    .where(
                        Repository.key == key.repo_full_name,
                        PullRequest.number == number,
                        cls.live_until > datetime.now(UTC),
                    )
                    .limit(1)
                )
            if pull_request_id is None:
                return
            job_key = f"{REFRESH_JOB}:{pull_request_id}"
            # A redraw still waiting will read this change too; one already running may not.
            waiting = await queue.get(job_key)
            if waiting is not None and waiting.claim_token is None:
                return
            await queue.schedule(
                REFRESH_JOB,
                {"pull_request_id": str(pull_request_id)},
                due_at=datetime.now(UTC) + _REFRESH_DELAY,
                key=job_key,
            )
        except Exception:
            logger.exception("Could not queue a Slack PR card redraw", extra={"pr_key": key})

    @classmethod
    async def refresh(cls, pull_request_id: UUID) -> int:
        """Redraw every live card of the pull request; how many were redrawn."""
        async with postgres.session() as session:
            cards = list(
                await session.scalars(
                    select(cls).where(
                        cls.pull_request_id == pull_request_id,
                        cls.live_until > datetime.now(UTC),
                    )
                )
            )
        if not cards:
            return 0
        drawn = await PullRequestCard.read(cards[0].pull_request)
        for card in cards:
            await card.show(drawn)
        return len(cards)

    @classmethod
    async def expire(cls, card_id: UUID) -> None:
        """Draw the card one last time, saying it no longer updates."""
        async with postgres.session() as session:
            card = await session.get(cls, card_id)
        if card is None:
            return
        await card.show(await PullRequestCard.read(card.pull_request))

    async def show(self, drawn: PullRequestCard) -> None:
        """Put ``drawn`` in place of this card in its Slack message, leaving the rest alone."""
        message = await fetch_slack_thread_message_by_ts(
            self.channel_id, self.thread_ts, self.message_ts
        )
        blocks = message.get("blocks") if message is not None else None
        text = message.get("text") if message is not None else None
        if not isinstance(blocks, list) or not isinstance(text, str):
            logger.info(
                "Slack PR card message is gone or unreadable",
                extra={"slack_channel": self.channel_id, "slack_message_ts": self.message_ts},
            )
            return
        prefix = f"{_BLOCK_ID_PREFIX}{self.id}:"
        mine = [
            index
            for index, block in enumerate(blocks)
            if isinstance(block, dict) and str(block.get("block_id", "")).startswith(prefix)
        ]
        if not mine:
            await self.forget()
            return
        blocks_before, after = blocks[: mine[0]], blocks[mine[-1] + 1 :]
        redrawn = block_payload(drawn.blocks(self.id, live_until=self.live_until))
        try:
            await update_slack_message(
                self.channel_id, self.message_ts, text, blocks=[*blocks_before, *redrawn, *after]
            )
        except SlackRequestError as exc:
            if exc.code in {"message_not_found", "channel_not_found", "cant_update_message"}:
                await self.forget()
                return
            raise

    async def forget(self) -> None:
        """Stop tracking a card whose message or blocks are gone."""
        async with postgres.session() as session:
            await session.execute(delete(type(self)).where(type(self).id == self.id))
        await queue.cancel_key(f"{EXPIRE_JOB}:{self.id}")
