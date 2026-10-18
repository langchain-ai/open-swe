"""Pull request slacklets: one Slack message kept in step with a pull request.

The database owns each slacklet's state, which changes only by appending typed
commands. Each applied command is posted once as an immutable reply in the
slacklet's Slack thread and queued for the thread's agent; the slacklet message
is the only thing ever edited, to show the state after the latest command.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Self
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.ci import CommitChecks
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest
from openswe.github.repositories import Repository
from openswe.prompts import prompt
from openswe.slack.blocks import block_payload
from openswe.slack.client import (
    GitHubPrRef,
    delete_slack_message,
    parse_github_pr_url,
    post_slack_thread_reply_with_ts,
    update_slack_message,
)
from openswe.slack.http import SlackRequestError
from openswe.slack.records import SlackThread
from openswe.slack.thread_notes import note_for_thread_owner
from openswe.slacklets.pull_request import COMMAND, Attach, Command, PullRequestView, apply
from openswe.ui_invalidations import Topic
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Entry:
    """One applied command and the state it left."""

    seq: int
    command: Command
    view: PullRequestView

    def log_text(self) -> str:
        return f"`#{self.seq} {self.command.op}` {self.command.summary()}"

    def transcript_line(self) -> str:
        return self.command.model_dump_json()


class PullRequestSlackletCommand(Base):
    __tablename__ = "pull_request_slacklet_command"

    slacklet_id: Mapped[UUID] = mapped_column(
        ForeignKey("pull_request_slacklet.id", ondelete="CASCADE")
    )
    seq: Mapped[int]
    command: Mapped[JsonObject] = mapped_column(JSONB)
    log_message_ts: Mapped[str | None] = mapped_column(default=None)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    def parsed(self) -> Command:
        return COMMAND.validate_python(self.command)


class PullRequestSlacklet(Base):
    __tablename__ = "pull_request_slacklet"

    slack_thread_id: Mapped[UUID] = mapped_column(ForeignKey("slack_thread.id", ondelete="CASCADE"))
    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("pull_request.id", ondelete="CASCADE"))
    message_ts: Mapped[str]
    pull_request_view: Mapped[JsonObject] = mapped_column(JSONB)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    slack_thread: Mapped[SlackThread] = relationship(init=False, lazy="joined", innerjoin=True)

    @property
    def view(self) -> PullRequestView:
        return PullRequestView.model_validate(self.pull_request_view)

    @property
    def location(self) -> tuple[str, str]:
        return self.slack_thread.channel_id, self.slack_thread.thread_ts

    @classmethod
    async def get(cls, slacklet_id: UUID) -> Self | None:
        async with postgres.session() as session:
            return await session.get(cls, slacklet_id)

    @classmethod
    async def attach(
        cls,
        channel_id: str,
        thread_ts: str,
        view: PullRequestView,
        *,
        created_by: UUID | None,
    ) -> tuple[Self, list[Entry]]:
        """Post the slacklet for ``view`` in the thread, or find the one already there.

        Returns the slacklet and every command applied to it so far.
        """
        owner, repo = view.repo.split("/", 1)
        pull_request = await (await PullRequest.load(owner, repo, view.number)).ensure()
        async with postgres.session() as session:
            slack_thread_id = await SlackThread.ensure(session, channel_id, thread_ts)
            existing = await cls._find(session, slack_thread_id, pull_request.id)
        if existing is not None:
            return existing, await existing.entries()

        entry = Entry(seq=1, command=Attach(view=view), view=view)
        message_ts = await post_slack_thread_reply_with_ts(
            channel_id,
            thread_ts,
            view.fallback_text(),
            blocks=block_payload(view.blocks(seq=entry.seq, last=entry.command)),
            unfurl_links=False,
            unfurl_media=False,
        )
        try:
            async with postgres.session() as session:
                slacklet = cls(
                    slack_thread_id=slack_thread_id,
                    pull_request_id=pull_request.id,
                    message_ts=message_ts,
                    pull_request_view=view.model_dump(mode="json"),
                    created_by=created_by,
                    updated_by=created_by,
                )
                session.add(slacklet)
                await session.flush()
                record = await slacklet._record(session, entry, created_by=created_by)
                await slacklet._log(session, [(entry, record)], (channel_id, thread_ts))
        except IntegrityError:
            if not await delete_slack_message(channel_id, message_ts):
                logger.warning("Left a duplicate slacklet message", extra={"slack_ts": message_ts})
            async with postgres.session() as session:
                raced = await cls._find(session, slack_thread_id, pull_request.id)
            if raced is None:
                raise
            return raced, await raced.entries()
        loaded = await cls.get(slacklet.id)
        if loaded is None:
            raise LookupError("The slacklet vanished right after it was attached")
        return loaded, [entry]

    @classmethod
    async def _find(
        cls, session: AsyncSession, slack_thread_id: UUID, pull_request_id: UUID
    ) -> Self | None:
        return await session.scalar(
            select(cls).where(
                cls.slack_thread_id == slack_thread_id, cls.pull_request_id == pull_request_id
            )
        )

    @classmethod
    async def watching(
        cls, owner: str, repo: str, *, number: int | None = None, sha: str | None = None
    ) -> list[Self]:
        """Slacklets on ``owner/repo`` for PR ``number``, or whose head commit is ``sha``."""
        query = (
            select(cls)
            .join(PullRequest, PullRequest.id == cls.pull_request_id)
            .join(Repository, Repository.id == PullRequest.repository_id)
            .where(Repository.key == f"{owner}/{repo}".lower())
        )
        if number is not None:
            query = query.where(PullRequest.number == number)
        elif sha:
            query = query.where(cls.pull_request_view["ci"]["sha"].astext == sha)
        else:
            return []
        async with postgres.session() as session:
            return list((await session.scalars(query)).unique())

    async def entries(self) -> list[Entry]:
        """Every command applied so far, replayed from the first."""
        return [entry for entry, _ in await self.commands()]

    async def commands(self) -> list[tuple[Entry, PullRequestSlackletCommand]]:
        """Each stored command with the state replaying it from the first left."""
        async with postgres.session() as session:
            records = list(
                await session.scalars(
                    select(PullRequestSlackletCommand)
                    .where(PullRequestSlackletCommand.slacklet_id == self.id)
                    .order_by(PullRequestSlackletCommand.seq)
                )
            )
        return list(zip(_replay(records), records, strict=True))

    async def advance(self, observed: PullRequestView) -> list[Entry]:
        """Append the commands that take this slacklet to ``observed``, and publish them.

        The row stays locked while the thread replies and the message update go
        out, so replies appear in command order even when events arrive together.
        """
        async with postgres.session() as session:
            locked = await session.scalar(
                select(PullRequestSlacklet)
                .where(PullRequestSlacklet.id == self.id)
                .with_for_update(of=PullRequestSlacklet)
            )
            if locked is None:
                return []
            view = locked.view
            seq = (
                await session.scalar(
                    select(func.max(PullRequestSlackletCommand.seq)).where(
                        PullRequestSlackletCommand.slacklet_id == self.id
                    )
                )
                or 0
            )
            records: list[tuple[Entry, PullRequestSlackletCommand]] = []
            for command in view.commands_to(observed):
                seq += 1
                view = apply(view, command)
                entry = Entry(seq=seq, command=command, view=view)
                records.append((entry, await self._record(session, entry, created_by=None)))
            if not records:
                return []
            applied = [entry for entry, _ in records]
            await session.execute(
                update(PullRequestSlacklet)
                .where(PullRequestSlacklet.id == self.id)
                .values(
                    pull_request_view=view.model_dump(mode="json"),
                    updated_by=None,
                    updated_at=func.clock_timestamp(),
                )
            )
            await self._log(session, records, self.location)
            await Topic.PULL_REQUESTS.invalidate(session, key=view.key)
        await note_for_thread_owner(*self.location, self.transcript(applied))
        return applied

    @staticmethod
    def transcript(entries: list[Entry]) -> str:
        """What the thread's agent reads about ``entries``: each command, then the state."""
        last = entries[-1]
        return prompt(
            "slacklets/pull-request-commands",
            url=last.view.url,
            entries=[{"seq": entry.seq, "command": entry.transcript_line()} for entry in entries],
            state=last.view.model_dump_json(),
        )

    async def _record(
        self, session: AsyncSession, entry: Entry, *, created_by: UUID | None
    ) -> PullRequestSlackletCommand:
        record = PullRequestSlackletCommand(
            slacklet_id=self.id,
            seq=entry.seq,
            command=entry.command.model_dump(mode="json"),
            created_by=created_by,
            updated_by=created_by,
        )
        session.add(record)
        await session.flush()
        return record

    async def _log(
        self,
        session: AsyncSession,
        records: list[tuple[Entry, PullRequestSlackletCommand]],
        location: tuple[str, str],
    ) -> None:
        """Post each entry as a reply at ``location``, then show the last one's state on the message."""
        channel_id, thread_ts = location
        for entry, record in records:
            try:
                record.log_message_ts = await post_slack_thread_reply_with_ts(
                    channel_id,
                    thread_ts,
                    entry.log_text(),
                    unfurl_links=False,
                    unfurl_media=False,
                )
            except SlackRequestError as exc:
                logger.warning(
                    "Posting a slacklet command reply failed",
                    extra={"slacklet_id": str(self.id), "seq": entry.seq, "slack_error": exc.code},
                )
        await session.flush()
        last = records[-1][0]
        if last.seq == 1:
            return
        try:
            await update_slack_message(
                channel_id,
                self.message_ts,
                last.view.fallback_text(),
                blocks=block_payload(last.view.blocks(seq=last.seq, last=last.command)),
                unfurl_links=False,
                unfurl_media=False,
            )
        except SlackRequestError as exc:
            logger.warning(
                "Updating a slacklet message failed",
                extra={"slacklet_id": str(self.id), "seq": last.seq, "slack_error": exc.code},
            )


def _replay(records: list[PullRequestSlackletCommand]) -> list[Entry]:
    entries: list[Entry] = []
    view: PullRequestView | None = None
    for record in records:
        command = record.parsed()
        view = apply(view, command)
        entries.append(Entry(seq=record.seq, command=command, view=view))
    return entries


async def observe(ref: GitHubPrRef) -> PullRequestView:
    """Read the pull request from GitHub as the Open SWE app's installation sees it."""
    async with PullRequestClient.as_app(ref.owner, ref.repo, ref.number) as pull_request:
        pull = await pull_request.pull()
        head = pull.get("head")
        sha = str(head.get("sha") or "") if isinstance(head, dict) else ""
        checks, reviewers = await asyncio.gather(
            CommitChecks.read(pull_request.repo, sha), pull_request.reviewers()
        )
    return PullRequestView.observe(ref, pull=pull, checks=checks, reviewers=reviewers)


SLACKLET_EVENTS = frozenset(
    {"pull_request", "pull_request_review", "check_run", "check_suite", "workflow_run", "status"}
)


def _event_target(event_type: str, payload: JsonObject) -> tuple[int | None, str]:
    """The pull request number an event names, else the commit it is about."""
    if event_type in {"pull_request", "pull_request_review"}:
        pull = payload.get("pull_request")
        number = pull.get("number") if isinstance(pull, dict) else None
        return (number if isinstance(number, int) else None), ""
    if event_type == "status":
        return None, str(payload.get("sha") or "")
    subject = payload.get(event_type)
    return None, str(subject.get("head_sha") or "") if isinstance(subject, dict) else ""


async def refresh_from_github(event_type: str, payload: JsonObject) -> None:
    """Bring every slacklet the event concerns up to date with a fresh read of its pull request.

    The event only says where to look: what changed comes from GitHub's current
    answer, so a late, repeated, or missed delivery cannot leave a slacklet wrong.
    """
    repository = payload.get("repository")
    owner = repository.get("owner") if isinstance(repository, dict) else None
    owner_login = str(owner.get("login") or "") if isinstance(owner, dict) else ""
    repo = str(repository.get("name") or "") if isinstance(repository, dict) else ""
    number, sha = _event_target(event_type, payload)
    if not (owner_login and repo) or not postgres.configured():
        return
    slacklets = await PullRequestSlacklet.watching(owner_login, repo, number=number, sha=sha)
    by_url: dict[str, list[PullRequestSlacklet]] = {}
    for slacklet in slacklets:
        by_url.setdefault(slacklet.view.url, []).append(slacklet)
    for url, watching in by_url.items():
        ref = parse_github_pr_url(url)
        if ref is None:
            continue
        try:
            observed = await observe(ref)
        except Exception:
            logger.warning(
                "Reading a slacklet's pull request failed",
                extra={"pr_url": url, "github_event": event_type},
                exc_info=True,
            )
            continue
        for slacklet in watching:
            await slacklet.advance(observed)
