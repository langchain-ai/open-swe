"""Human review requests: a Slack card asking people to review one pull request.

A request has a kind. An ``expedited`` request is a vote on what its card showed of
a tiny pull request: it pins the head SHA the card was posted for and a fingerprint
of the diff it drew, and one approval from someone other than the author completes
it. A ``standard`` request is a card in the repository's review channel that people
sign up to review on GitHub; it merges once they approve. A ``posted`` request is
someone's own message linking the pull request in its review channel: Open SWE never
edits it, only reacts to it when the pull request is approved or merged, and never
merges.

One request per pull request may be ``open`` at a time, whatever its kind; a partial
unique index enforces that. A participant is a ``users.id``, never a GitHub or Slack
handle.
"""

from collections import Counter
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal, Self, TypedDict
from uuid import UUID, uuid7

from pydantic import JsonValue
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Text,
    cast,
    desc,
    func,
    inspect,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.expedited_review.eligibility import ExcludedHunk
from openswe.github.pull_requests import PullRequest
from openswe.github.repositories import Repository
from openswe.human_review.pick_message import PickMessage
from openswe.run_config import RunConfig
from openswe.slack.client import SlackConversation
from openswe.slack.dm import DmOrigin
from openswe.slack.records import lookup_slack_thread_id
from openswe.users import User
from openswe.utils.json_types import JsonObject
from openswe.webhooks.event_log import EventLog, EventRefs

RequestKind = Literal["expedited", "standard", "posted"]
RequestState = Literal["open", "merged", "rejected", "superseded", "cancelled"]
# ``approve`` and ``reject`` are Slack votes on an expedited card; ``review`` is a
# person signed up to review a standard request on GitHub; ``picked`` is someone
# Open SWE asked who has not accepted yet, so the request is still open to anyone;
# ``expired`` is a pick that was not accepted in time, kept so it is not picked again.
ParticipantDecision = Literal["approve", "reject", "review", "picked", "expired"]
SUPPLEMENTARY_REVIEWS_KEY = "supplementary_reviews"


class ChannelChoice(TypedDict):
    id: str
    name: str


def slack_mention(user: User | None, fallback_login: str) -> str:
    """A Slack mention for ``user``, or their GitHub handle when Slack is not linked."""
    if user is not None and user.slack_user_id:
        return f"<@{user.slack_user_id}>"
    return f"@{fallback_login}"


class HumanReviewParticipant(Base):
    __tablename__ = "human_review_participant"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("human_review_request.id", ondelete="CASCADE"), primary_key=True, init=False
    )
    decision: Mapped[ParticipantDecision] = mapped_column(Text, default="approve")
    github_review_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    github_review_sha: Mapped[str] = mapped_column(server_default="", default="")
    assigned_by_agent: Mapped[bool] = mapped_column(server_default="false", default=False)
    joined_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    dm_channel_id: Mapped[str] = mapped_column(server_default="", default="")
    dm_ts: Mapped[str] = mapped_column(server_default="", default="")
    dm_text: Mapped[str] = mapped_column(server_default="", default="")
    user: Mapped[User] = relationship(init=False)

    @property
    def pick_message(self) -> PickMessage | None:
        """The DM that asked them to take the pick; ``None`` when it was never recorded."""
        if not self.dm_channel_id or not self.dm_ts:
            return None
        return PickMessage(channel_id=self.dm_channel_id, ts=self.dm_ts, text=self.dm_text)

    @property
    def github_login(self) -> str:
        return self.user.login_for("github")

    @property
    def slack_mention(self) -> str:
        return slack_mention(self.user, self.github_login)


class HumanReviewRequest(Base):
    __tablename__ = "human_review_request"

    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("pull_request.id", ondelete="CASCADE"))
    head_sha: Mapped[str]
    kind: Mapped[RequestKind] = mapped_column(Text)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    thread_id: Mapped[str] = mapped_column(server_default="", default="")
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    diff_fingerprint: Mapped[str] = mapped_column(server_default="", default="")
    # Hunks the agent left off an expedited card as qualifying under the target repo's APPROVALS.md.
    excluded_hunks: Mapped[list[ExcludedHunk]] = mapped_column(JSONB, default_factory=list)
    tldr: Mapped[str] = mapped_column(server_default="", default="")
    state: Mapped[RequestState] = mapped_column(Text, default="open")
    detail: Mapped[str] = mapped_column(server_default="", default="")
    slack_channel_id: Mapped[str] = mapped_column(server_default="", default="")
    # Empty for a standard card posted at the top of the review channel.
    slack_thread_ts: Mapped[str] = mapped_column(server_default="", default="")
    slack_dm_channel_id: Mapped[str] = mapped_column(server_default="", default="")
    slack_dm_message_ts: Mapped[str] = mapped_column(server_default="", default="")
    slack_message_ts: Mapped[str] = mapped_column(server_default="", default="")
    # Slack only renders a file cited when the message is first posted, so updates reuse it.
    slack_diff_file_id: Mapped[str] = mapped_column(server_default="", default="")
    auto_merge: Mapped[bool] = mapped_column(server_default="true", default=True)
    # A draft PR's expedited card offers only "Mark ready", to its author, until they click it.
    awaiting_ready: Mapped[bool] = mapped_column(server_default="false", default=False)
    # The posted card is a thread reply also sent to the channel.
    slack_broadcast: Mapped[bool] = mapped_column(server_default="false", default=False)
    # Channels the card offers to be sent to, its own first; fixed when it is posted.
    slack_channel_choices: Mapped[list[ChannelChoice]] = mapped_column(JSONB, default_factory=list)
    # A top-level copy in another channel. The channel stays after the copy is deleted,
    # so the author's later cards offer it again.
    slack_copy_channel_id: Mapped[str] = mapped_column(server_default="", default="")
    slack_copy_ts: Mapped[str] = mapped_column(server_default="", default="")
    run_config: Mapped[JsonObject] = mapped_column(JSONB, default_factory=dict)
    # A posted request's approved reaction went on at this time.
    approved_at: Mapped[datetime | None] = mapped_column(default=None)
    # When a posted request's pull request last turned green; ``None`` while it is not.
    ready_since: Mapped[datetime | None] = mapped_column(default=None)
    participants: Mapped[list[HumanReviewParticipant]] = relationship(
        default_factory=list,
        cascade="all, delete-orphan",
        order_by=lambda: HumanReviewParticipant.joined_at,
    )
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    pull_request: Mapped[PullRequest] = relationship(init=False)
    requested_by: Mapped[User | None] = relationship(
        init=False, foreign_keys=[requested_by_user_id]
    )

    @property
    def active(self) -> bool:
        return self.state == "open"

    @property
    def has_card(self) -> bool:
        """Whether the Slack message is Open SWE's card rather than someone's own post."""
        return self.kind != "posted"

    @property
    def approvals(self) -> list[HumanReviewParticipant]:
        """Slack approvals from anyone but the author; older cards may still hold an author's vote."""
        return [
            participant
            for participant in self.participants
            if participant.decision == "approve"
            and not self.is_author(participant.user_id, participant.github_login)
        ]

    @property
    def approved(self) -> bool:
        """One approval from someone other than the author is enough."""
        return bool(self.approvals)

    @property
    def reviewers(self) -> list[HumanReviewParticipant]:
        """People signed up to review a standard request, in the order they joined."""
        return [p for p in self.participants if p.decision == "review"]

    @property
    def picks(self) -> list[HumanReviewParticipant]:
        """People Open SWE asked to review who have not accepted yet."""
        return [p for p in self.participants if p.decision == "picked"]

    @property
    def requester_login(self) -> str:
        if self.requested_by is not None:
            return self.requested_by.github_login
        return RunConfig.parse(self.run_config).github_login or ""

    async def author_mention(self) -> str:
        pr = self.pull_request
        author = await User.get(pr.author_user_id) if pr.author_user_id else None
        return slack_mention(author, pr.author or "the author")

    def is_author(self, user_id: UUID, login: str) -> bool:
        pr = self.pull_request
        if pr.author_user_id is not None:
            return pr.author_user_id == user_id
        return bool(pr.author) and bool(login) and pr.author.lower() == login.lower()

    @property
    def approvers(self) -> list[str]:
        """GitHub handles of the approvers, for display."""
        return [participant.github_login for participant in self.approvals]

    def participant(self, user_id: UUID) -> HumanReviewParticipant | None:
        return next((p for p in self.participants if p.user_id == user_id), None)

    @property
    def dm_origin(self) -> DmOrigin | None:
        """The review's Slack thread, which DMs about it are sent on behalf of."""
        root = self.slack_thread_ts or self.slack_message_ts
        if not self.slack_channel_id or not root:
            return None
        return DmOrigin(
            channel_id=self.slack_channel_id, thread_ts=root, subject=self.pull_request.url
        )

    async def picked_by(self, thread_id: str) -> bool:
        """Whether ``thread_id`` may pick this request's reviewer: its own thread or its Slack thread's."""
        if not thread_id:
            return False
        if self.thread_id == thread_id:
            return True
        root = self.slack_thread_ts or self.slack_message_ts
        if not self.slack_channel_id or not root:
            return False
        owner = await lookup_slack_thread_id(self.slack_channel_id, root)
        return owner == thread_id

    @property
    def slack_conversation(self) -> SlackConversation:
        return SlackConversation(self.slack_channel_id)

    @property
    def slack_location(self) -> tuple[str, str] | None:
        if self.slack_channel_id and self.slack_thread_ts:
            return self.slack_channel_id, self.slack_thread_ts
        return None

    @classmethod
    def _loaded(cls, statement):  # noqa: ANN001, ANN206
        return statement.options(
            selectinload(cls.participants)
            .selectinload(HumanReviewParticipant.user)
            .selectinload(User.identities),
            selectinload(cls.requested_by).selectinload(User.identities),
            selectinload(cls.pull_request).selectinload(PullRequest.threads),
        )

    @classmethod
    async def get(cls, request_id: UUID) -> Self | None:
        async with postgres.session() as session:
            return await session.scalar(cls._loaded(select(cls)).where(cls.id == request_id))

    @classmethod
    async def active_for(cls, owner: str, repo: str, number: int) -> Self | None:
        async with postgres.session() as session:
            return await session.scalar(
                cls._loaded(select(cls))
                .join(cls.pull_request)
                .join(PullRequest.repository)
                .where(
                    Repository.key == f"{owner}/{repo}".lower(),
                    PullRequest.number == number,
                    cls.state == "open",
                )
            )

    @classmethod
    async def assigned_to(cls, user_id: UUID) -> list[Self]:
        """Open review requests for which Open SWE explicitly picked this person."""
        async with postgres.session() as session:
            rows = await session.scalars(
                cls._loaded(select(cls))
                .join(cls.pull_request)
                .join(cls.participants)
                .where(
                    cls.state == "open",
                    cls.kind.in_(("standard", "posted")),
                    PullRequest.state == "open",
                    HumanReviewParticipant.user_id == user_id,
                    HumanReviewParticipant.decision == "review",
                    HumanReviewParticipant.assigned_by_agent.is_(True),
                )
            )
            return list(rows)

    @classmethod
    async def is_expedited_approver(cls, owner: str, repo: str, number: int, login: str) -> bool:
        """Whether ``login`` approved the PR through its open expedited card."""
        request = await cls.active_for(owner, repo, number)
        if request is None or request.kind != "expedited":
            return False
        return login.lower() in {approver.lower() for approver in request.approvers}

    @classmethod
    async def open_in_repository(
        cls, owner: str, repo: str, *, kinds: tuple[RequestKind, ...]
    ) -> list[Self]:
        async with postgres.session() as session:
            rows = await session.scalars(
                cls._loaded(select(cls))
                .join(cls.pull_request)
                .join(PullRequest.repository)
                .where(
                    Repository.key == f"{owner}/{repo}".lower(),
                    cls.kind.in_(kinds),
                    cls.state == "open",
                )
            )
            return list(rows)

    @classmethod
    async def superseded_on_slack(cls, pull_request_id: UUID) -> list[Self]:
        """Superseded cards for a pull request whose Slack message is still up."""
        async with postgres.session() as session:
            rows = await session.scalars(
                cls._loaded(select(cls)).where(
                    cls.pull_request_id == pull_request_id,
                    cls.state == "superseded",
                    cls.slack_message_ts != "",
                )
            )
            return list(rows)

    @classmethod
    async def with_standing_reviews(cls, pull_request_id: UUID) -> list[Self]:
        """Closed, unmerged cards for a pull request with a GitHub review not yet dismissed."""
        async with postgres.session() as session:
            rows = await session.scalars(
                cls._loaded(select(cls)).where(
                    cls.pull_request_id == pull_request_id,
                    cls.state.not_in(("open", "merged")),
                    cls.participants.any(HumanReviewParticipant.github_review_id.is_not(None)),
                )
            )
            return list(rows)

    @classmethod
    async def all_for_repo(cls, owner: str, repo: str) -> list[Self]:
        """Every request a repository has ever had, oldest first."""
        async with postgres.session() as session:
            rows = await session.scalars(
                cls._loaded(select(cls))
                .join(cls.pull_request)
                .join(PullRequest.repository)
                .where(Repository.key == f"{owner}/{repo}".lower())
                .order_by(cls.created_at, cls.id)
            )
            return list(rows)

    @asynccontextmanager
    async def supplementary_rotation(
        self, policy_id: str
    ) -> AsyncIterator[tuple[Self | None, dict[str, datetime]]]:
        """Read one supplementary review's rotation history and reserve a reviewer atomically."""
        cls = type(self)
        pr = self.pull_request
        repository = f"{pr.owner}/{pr.repo}".lower()
        review = cls.run_config[SUPPLEMENTARY_REVIEWS_KEY][policy_id]
        login = review["github_login"].astext
        assigned_at = cast(review["assigned_at"].astext, DateTime(timezone=True))
        async with postgres.session() as session:
            await session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended(f"supplementary-review:{repository}:{policy_id}", 0)
                    )
                )
            )
            row = await session.scalar(
                cls._loaded(select(cls))
                .where(cls.id == self.id)
                .with_for_update(of=cls)
                .execution_options(populate_existing=True)
            )
            rows = await session.execute(
                select(login, func.max(assigned_at))
                .join(cls.pull_request)
                .join(PullRequest.repository)
                .where(
                    Repository.key == repository,
                    login.is_not(None),
                    login != "",
                )
                .group_by(login)
            )
            before = row.event_state() if row is not None else None
            yield row, {name.lower(): at for name, at in rows if name and at is not None}
        if row is not None and before is not None:
            await row.log_changes(before)

    @classmethod
    async def open_review_counts(
        cls, user_ids: Collection[UUID], *, excluding: UUID
    ) -> Counter[UUID]:
        """How many other open requests each person is reviewing or has been picked for."""
        if not user_ids:
            return Counter()
        async with postgres.session() as session:
            rows = await session.execute(
                select(HumanReviewParticipant.user_id, func.count())
                .join(cls, cls.id == HumanReviewParticipant.request_id)
                .where(
                    HumanReviewParticipant.user_id.in_(user_ids),
                    HumanReviewParticipant.decision.in_(("review", "picked")),
                    cls.state == "open",
                    cls.id != excluding,
                )
                .group_by(HumanReviewParticipant.user_id)
            )
            return Counter(dict(rows.tuples().all()))

    @classmethod
    async def card_in_thread(cls, channel_id: str, thread_ts: str) -> Self | None:
        """The Open SWE review-request card this Slack thread holds, with its pull request."""
        if not postgres.configured():
            return None
        async with postgres.session() as session:
            return await session.scalar(
                select(cls)
                .options(selectinload(cls.pull_request))
                .where(
                    cls.kind.in_(("standard", "expedited")),
                    or_(
                        (cls.slack_channel_id == channel_id)
                        & (
                            func.coalesce(
                                func.nullif(cls.slack_thread_ts, ""), cls.slack_message_ts
                            )
                            == thread_ts
                        ),
                        (cls.slack_copy_channel_id == channel_id)
                        & (cls.slack_copy_ts == thread_ts),
                    ),
                )
                .order_by(cls.created_at.desc())
                .limit(1)
            )

    @classmethod
    async def copy_channels_for_author(cls, login: str, *, since: datetime) -> list[str]:
        """Channels other than their thread's that ``login``'s cards were sent to, newest first."""
        async with postgres.session() as session:
            rows = await session.execute(
                select(cls.slack_copy_channel_id, func.max(cls.created_at).label("used_at"))
                .join(cls.pull_request)
                .where(
                    func.lower(PullRequest.author) == login.lower(),
                    cls.slack_copy_channel_id != "",
                    cls.created_at >= since,
                )
                .group_by(cls.slack_copy_channel_id)
                .order_by(desc("used_at"))
            )
            return [channel_id for channel_id, _ in rows]

    @property
    def slack_copy(self) -> tuple[str, str] | None:
        if self.slack_copy_channel_id and self.slack_copy_ts:
            return self.slack_copy_channel_id, self.slack_copy_ts
        return None

    @property
    def sent_elsewhere(self) -> bool:
        """Also in a channel, not only its thread; a card is sent at most once."""
        return self.slack_broadcast or self.slack_copy is not None

    def event_state(self) -> dict[str, JsonValue]:
        return {
            "state": self.state,
            "head_sha": self.head_sha,
            "detail": self.detail,
            "auto_merge": self.auto_merge,
            "awaiting_ready": self.awaiting_ready,
            "slack_channel_id": self.slack_channel_id,
            "slack_message_ts": self.slack_message_ts,
            "slack_dm_channel_id": self.slack_dm_channel_id,
            "slack_dm_message_ts": self.slack_dm_message_ts,
            "supplementary_reviews": {
                policy: {
                    key: value
                    for key, value in state.items()
                    if key
                    in {
                        "github_login",
                        "assigned_at",
                        "github_requested",
                        "github_notified",
                        "slack_notified",
                        "dm_notified",
                        "reason",
                    }
                }
                for policy, state in self.run_config.get(SUPPLEMENTARY_REVIEWS_KEY, {}).items()
                if isinstance(state, dict)
            },
            "slack_copy_channel_id": self.slack_copy_channel_id,
            "slack_copy_ts": self.slack_copy_ts,
            "slack_broadcast": self.slack_broadcast,
            "participants": [
                {
                    "user_id": str(p.user_id),
                    "decision": p.decision,
                    "assigned_by_agent": p.assigned_by_agent,
                    "joined_at": p.joined_at.isoformat() if p.joined_at else None,
                    "github_review_id": p.github_review_id,
                    "dm_channel_id": p.dm_channel_id,
                    "dm_ts": p.dm_ts,
                }
                for p in self.participants
            ],
        }

    async def log_event(self, event_type: str, **details: JsonValue) -> None:
        """Append review activity without exposing run configuration or DM contents."""
        pr = self.pull_request
        await EventLog.append(
            "human_review",
            {
                "request_id": str(self.id),
                "kind": self.kind,
                "pull_request_url": pr.url,
                "thread_id": self.thread_id,
                "requested_by_user_id": str(self.requested_by_user_id)
                if self.requested_by_user_id
                else None,
                **details,
            },
            event_type=event_type,
            delivery_id=str(uuid7()),
            refs=EventRefs(
                github_repository=f"{pr.owner}/{pr.repo}",
                pull_request_number=pr.number,
                slack_channel_id=self.slack_channel_id,
            ),
        )

    async def log_changes(self, before: dict[str, JsonValue]) -> None:
        changes: dict[str, JsonValue] = {
            key: {"before": before.get(key), "after": value}
            for key, value in self.event_state().items()
            if before.get(key) != value
        }
        if changes:
            await self.log_event("request.updated", changes=changes)

    async def save(self) -> Self:
        cls = type(self)
        previous = await cls.get(self.id)
        before = previous.event_state() if previous is not None else None
        async with postgres.session() as session:
            session.add(self)
            await session.flush()
            stored = await session.scalar(
                cls._loaded(select(cls))
                .where(cls.id == self.id)
                .execution_options(populate_existing=True)
            )
        if stored is None:
            raise RuntimeError(f"human review request {self.id} vanished during save")
        if before is None:
            await stored.log_event("request.created", snapshot=stored.event_state())
        else:
            await stored.log_changes(before)
        return stored

    @classmethod
    @asynccontextmanager
    async def locked(cls, request_id: UUID) -> AsyncIterator[tuple[AsyncSession, Self | None]]:
        """The row locked for update; changes made to it commit when the block exits."""
        async with postgres.session() as session:
            row = await session.scalar(
                cls._loaded(select(cls))
                .where(cls.id == request_id)
                .with_for_update(of=cls)
                .execution_options(populate_existing=True)
            )
            before = row.event_state() if row is not None else None
            yield session, row
            deleted = row is not None and (row in session.deleted or inspect(row).deleted)
        if row is not None and before is not None:
            if deleted:
                await row.log_event("request.discarded", snapshot=before)
            else:
                await row.log_changes(before)

    @classmethod
    @asynccontextmanager
    async def locked_post(cls, channel_id: str, message_ts: str) -> AsyncIterator[list[Self]]:
        """Lock every PR watched on one Slack message in a stable order."""
        async with postgres.session() as session:
            rows = await session.scalars(
                cls._loaded(select(cls))
                .where(
                    cls.kind == "posted",
                    cls.slack_channel_id == channel_id,
                    cls.slack_message_ts == message_ts,
                )
                .order_by(cls.id)
                .with_for_update(of=cls)
            )
            requests = list(rows)
            before = {row.id: row.event_state() for row in requests}
            yield requests
        for row in requests:
            await row.log_changes(before[row.id])

    async def record_pick_message(self, user_id: UUID, message: PickMessage) -> None:
        """Remember the DM that asked ``user_id`` to take their pick, so it can be edited later."""
        async with self.locked(self.id) as (_, row):
            participant = row.participant(user_id) if row is not None else None
            if participant is None:
                return
            participant.dm_channel_id = message.channel_id
            participant.dm_ts = message.ts
            participant.dm_text = message.text

    @classmethod
    async def transition(
        cls, request_id: UUID, *, expected: tuple[RequestState, ...], **changes: object
    ) -> Self | None:
        """Apply ``changes`` if the row is still in one of ``expected``; else ``None``."""
        async with cls.locked(request_id) as (_, row):
            if row is None or row.state not in expected:
                return None
            for name, value in changes.items():
                setattr(row, name, value)
            return row
