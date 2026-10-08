"""Agent threads listening for event log rows in their workspace."""

import logging
from datetime import datetime
from typing import Self
from uuid import UUID, uuid7

from langgraph_sdk.errors import NotFoundError
from pydantic import BaseModel, Field, JsonValue, ValidationError
from sqlalchemy import (
    ColumnElement,
    ForeignKey,
    Text,
    delete,
    func,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from openswe.config import ENV
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.comments import fence_github_comment_body
from openswe.github.org_membership import INTERNAL_BOT_LOGINS, OPEN_SWE_GITHUB_LOGINS
from openswe.github.pull_requests import PullRequest
from openswe.github.repositories import Repository
from openswe.input_messages import Fields
from openswe.prompts import prompt
from openswe.webhooks.event_log import EventSource, LoggedEvent
from openswe.webhooks.event_matches import EventMatch, MultitaskStrategy

logger = logging.getLogger(__name__)

_TRUSTED_GITHUB_BOTS = frozenset(login.lower() for login in INTERNAL_BOT_LOGINS)
# CI results describe a commit, so they matter even when Open SWE pushed it.
_CI_EVENT_TYPES = frozenset({"check_run", "check_suite", "workflow_run"})
_MAX_BODY_CHARS = 8_000


class _GitHubAccount(BaseModel):
    login: str = ""


class _GitHubRepository(BaseModel):
    full_name: str = ""


class _GitHubApp(BaseModel):
    id: int | None = None


class _GitHubText(BaseModel):
    number: int | None = None
    body: str | None = None
    html_url: str = ""
    state: str = ""
    performed_via_github_app: _GitHubApp | None = None

    def authored_by_app(self, app_id: str) -> bool:
        app = self.performed_via_github_app
        return bool(app_id) and app is not None and str(app.id) == app_id


class _GitHubPullRequestRef(BaseModel):
    number: int | None = None


class _GitHubCheck(BaseModel):
    name: str = ""
    status: str = ""
    conclusion: str | None = None
    html_url: str | None = None
    pull_requests: list[_GitHubPullRequestRef] = []


class _GitHubDelivery(BaseModel):
    action: str = ""
    sender: _GitHubAccount | None = None
    repository: _GitHubRepository | None = None
    pull_request: _GitHubText | None = None
    issue: _GitHubText | None = None
    comment: _GitHubText | None = None
    review: _GitHubText | None = None
    check_run: _GitHubCheck | None = None
    check_suite: _GitHubCheck | None = None
    workflow_run: _GitHubCheck | None = None


class _SlackMessage(BaseModel):
    user: JsonValue = ""
    text: JsonValue = ""
    channel: JsonValue = ""
    channel_type: JsonValue = ""

    @property
    def user_id(self) -> str:
        return self.user if isinstance(self.user, str) else ""

    @property
    def channel_id(self) -> str:
        return self.channel if isinstance(self.channel, str) else ""

    @property
    def message_text(self) -> str:
        return self.text if isinstance(self.text, str) else ""


class _SlackDelivery(BaseModel):
    event: _SlackMessage | None = None
    user_id: str = ""
    channel_id: str = ""
    text: str = ""


class _LinearActor(BaseModel):
    name: str = ""


class _LinearData(BaseModel):
    title: str = ""
    body: str = ""
    bot_actor: JsonValue = Field(default=None, alias="botActor")


class _LinearDelivery(BaseModel):
    url: str = ""
    actor: _LinearActor | None = None
    data: _LinearData | None = None


class _OpenSweDecision(BaseModel):
    """The fields every event Open SWE emits about itself carries."""

    pr_url: str = ""
    summary: str = ""


class EventSummary(BaseModel):
    """What a wake message says about one delivery, whatever its source."""

    source: EventSource
    event_type: str
    target: str = ""
    sender: str = ""
    link: str = ""
    status: str = ""
    body: str = ""
    trusted: bool = False
    from_open_swe: bool = False
    slack_channel_id: str = ""
    pull_request_numbers: list[int] = []

    @property
    def details(self) -> str:
        """Every field the sender controls, for fencing as one block."""
        fields = Fields(
            {"On": self.target, "From": self.sender, "Status": self.status, "Link": self.link}
        ).text()
        lines = [fields] if fields else []
        if self.body:
            lines.extend(("", self.body[:_MAX_BODY_CHARS]))
        return "\n".join(lines)

    @classmethod
    def of(cls, event: LoggedEvent) -> Self:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.source == "github":
            return cls._github(event, _GitHubDelivery.model_validate(payload))
        if event.source == "slack":
            return cls._slack(event, _SlackDelivery.model_validate(payload))
        if event.source == "openswe":
            return cls._openswe(event, _OpenSweDecision.model_validate(payload))
        if event.source == "deployment":
            return cls._deployment(event, payload)
        return cls._linear(event, _LinearDelivery.model_validate(payload))

    @classmethod
    def _openswe(cls, event: LoggedEvent, decision: _OpenSweDecision) -> Self:
        # Emitted for listeners, so never filtered as Open SWE's own echo; untrusted because a
        # summary can carry a person's free-text reason, such as why they declined.
        return cls(
            source="openswe",
            event_type=event.event_type,
            target=decision.pr_url,
            sender="Open SWE",
            status=event.event_type,
            body=decision.summary,
        )

    @classmethod
    def _github(cls, event: LoggedEvent, delivery: _GitHubDelivery) -> Self:
        sender = delivery.sender.login.lower() if delivery.sender else ""
        commented = delivery.comment or delivery.review
        opened = delivery.pull_request or delivery.issue
        check = delivery.check_run or delivery.workflow_run or delivery.check_suite
        body = commented.body if commented else None
        if body is None and opened and delivery.action == "opened":
            body = opened.body
        if check:
            status = " ".join(p for p in (check.name, check.conclusion or check.status) if p)
            link = check.html_url or ""
        else:
            status = delivery.review.state if delivery.review else ""
            link = commented.html_url if commented else ""
        return cls(
            source="github",
            event_type=event.event_type,
            target=(opened.html_url if opened else "")
            or (delivery.repository.full_name if delivery.repository else ""),
            sender=f"@{sender}" if sender else "",
            link=link,
            status=status,
            body=body or "",
            trusted=event.user_id is not None or sender in _TRUSTED_GITHUB_BOTS,
            from_open_swe=(
                sender in OPEN_SWE_GITHUB_LOGINS
                or (commented is not None and commented.authored_by_app(ENV.GITHUB_APP_ID.get()))
            )
            and event.base_event_type not in _CI_EVENT_TYPES,
            pull_request_numbers=sorted(
                {
                    number
                    for number in (
                        delivery.pull_request.number if delivery.pull_request else None,
                        delivery.issue.number if delivery.issue else None,
                        *(ref.number for ref in (check.pull_requests if check else [])),
                    )
                    if number is not None
                }
            ),
        )

    @classmethod
    def _slack(cls, event: LoggedEvent, delivery: _SlackDelivery) -> Self:
        message = delivery.event or _SlackMessage(
            user=delivery.user_id, text=delivery.text, channel=delivery.channel_id
        )
        own_user = ENV.SLACK_BOT_USER_ID.get()
        return cls(
            source="slack",
            event_type=event.event_type,
            target=f"<#{message.channel_id}>" if message.channel_id else "",
            sender=f"<@{message.user_id}>" if message.user_id else "",
            body=message.message_text,
            trusted=event.user_id is not None,
            from_open_swe=bool(own_user) and message.user_id == own_user,
            slack_channel_id=message.channel_id,
        )

    @classmethod
    def _deployment(cls, event: LoggedEvent, payload: dict[str, JsonValue]) -> Self:
        """A verified deploy. ``from_open_swe`` stays false so a subscription can wake."""
        target = payload.get("target")
        commits = payload.get("commits")
        count = len(commits) if isinstance(commits, list) else 0
        return cls(
            source="deployment",
            event_type=event.event_type,
            target=target.strip() if isinstance(target, str) else "",
            status="deployed",
            body=(
                "This deploy includes the merge commit the thread subscribed for "
                f"({count} commits)."
            ),
            trusted=True,
        )

    @classmethod
    def _linear(cls, event: LoggedEvent, delivery: _LinearDelivery) -> Self:
        data = delivery.data or _LinearData()
        return cls(
            source="linear",
            event_type=event.event_type,
            target=delivery.url,
            sender=delivery.actor.name if delivery.actor else "",
            status=data.title,
            body=data.body,
            trusted=event.user_id is not None,
            from_open_swe=bool(data.bot_actor),
        )


class EventSubscription(Base):
    __tablename__ = "event_subscription"

    thread_id: Mapped[str]
    workspace_id: Mapped[UUID]
    multitask_strategy: Mapped[MultitaskStrategy] = mapped_column(Text)
    run_config: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    expires_at: Mapped[datetime]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    sources: Mapped[list[str]] = mapped_column(ARRAY(Text), default_factory=list)
    repository_id: Mapped[UUID | None] = mapped_column(ForeignKey("repository.id"), default=None)
    pull_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("pull_request.id"), default=None
    )
    event_types: Mapped[list[str]] = mapped_column(ARRAY(Text), default_factory=list)
    payload_match: Mapped[dict[str, JsonValue]] = mapped_column(JSONB, default_factory=dict)
    instructions: Mapped[str] = mapped_column(default="")
    one_shot: Mapped[bool] = mapped_column(default=False)
    trigger_count: Mapped[int] = mapped_column(server_default="0", init=False)
    last_triggered_at: Mapped[datetime | None] = mapped_column(default=None, init=False)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    repository: Mapped[Repository | None] = relationship(init=False, lazy="joined")
    pull_request: Mapped[PullRequest | None] = relationship(init=False, lazy="joined")

    @property
    def target(self) -> str:
        if self.pull_request:
            return self.pull_request.url
        return self.repository.full_name if self.repository else "workspace"

    async def create(self) -> Self:
        cls = type(self)
        async with postgres.session() as session:
            await session.execute(delete(cls).where(cls._expired()))
            session.add(self)
            await session.flush()
            return await session.get_one(cls, self.id, populate_existing=True)

    @classmethod
    async def for_thread(cls, thread_id: str) -> list[Self]:
        """The thread's subscriptions that have not expired, oldest first."""
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls)
                .where(cls.thread_id == thread_id, ~cls._expired())
                .order_by(cls.created_at)
            )
            return list(rows.unique())

    @classmethod
    async def cancel(cls, thread_id: str, subscription_id: UUID) -> bool:
        async with postgres.session() as session:
            deleted = await session.scalar(
                delete(cls)
                .where(cls.thread_id == thread_id, cls.id == subscription_id)
                .returning(cls.id)
            )
            return deleted is not None

    @classmethod
    def _expired(cls) -> ColumnElement[bool]:
        return cls.expires_at <= func.clock_timestamp()

    @classmethod
    async def deliver(cls, event: LoggedEvent) -> None:
        """Wake every thread listening for ``event``. Never raises."""
        if event.workspace_id is None:
            return
        try:
            summary = EventSummary.of(event)
        except ValidationError:
            logger.warning(
                "Event payload does not have its source's shape",
                extra={"webhook_source": event.source, "event_delivery_id": event.delivery_id},
                exc_info=True,
            )
            return
        if summary.from_open_swe:
            return
        try:
            subscriptions = await cls._matching(event, summary)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Loading event subscriptions failed",
                extra={"event_delivery_id": event.delivery_id},
                exc_info=True,
            )
            return
        strategies: dict[str, MultitaskStrategy] = {}
        for subscription in subscriptions:
            try:
                if not await subscription.sees(summary):
                    continue
                if not await subscription.match(event, summary):
                    continue
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Recording an event match failed",
                    extra={
                        "event_subscription_id": str(subscription.id),
                        "event_delivery_id": event.delivery_id,
                    },
                    exc_info=True,
                )
                continue
            if strategies.get(subscription.thread_id) != "interrupt":
                strategies[subscription.thread_id] = subscription.multitask_strategy
        for thread_id, strategy in strategies.items():
            await cls.deliver_to(thread_id, strategy)

    @classmethod
    async def deliver_to(cls, thread_id: str, strategy: MultitaskStrategy) -> None:
        """Start a run for what ``thread_id`` is owed, when one is needed. Never raises."""
        if not postgres.configured():
            return
        try:
            await EventMatch.deliver(thread_id, strategy)
        except NotFoundError:
            logger.info("Event subscription thread is gone", extra={"agent_thread_id": thread_id})
            await cls.forget(thread_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Delivering event matches failed",
                extra={"agent_thread_id": thread_id},
                exc_info=True,
            )

    @classmethod
    async def forget(cls, thread_id: str, *, session: AsyncSession | None = None) -> None:
        if session is not None:
            await session.execute(delete(cls).where(cls.thread_id == thread_id))
            await session.execute(delete(EventMatch).where(EventMatch.thread_id == thread_id))
            return
        try:
            async with postgres.session() as session:
                await cls.forget(thread_id, session=session)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Deleting a gone thread's event subscriptions failed",
                extra={"agent_thread_id": thread_id},
                exc_info=True,
            )

    @classmethod
    async def _matching(cls, event: LoggedEvent, summary: EventSummary) -> list[Self]:
        listed_pull_requests = select(PullRequest.id).where(
            PullRequest.repository_id == event.repository_id,
            PullRequest.number.in_(summary.pull_request_numbers),
        )
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls).where(
                    cls.workspace_id == event.workspace_id,
                    ~cls._expired(),
                    or_(func.cardinality(cls.sources) == 0, cls.sources.contains([event.source])),
                    or_(cls.repository_id.is_(None), cls.repository_id == event.repository_id),
                    or_(
                        cls.pull_request_id.is_(None),
                        cls.pull_request_id == event.pull_request_id,
                        cls.pull_request_id.in_(listed_pull_requests),
                    ),
                    or_(
                        func.cardinality(cls.event_types) == 0,
                        cls.event_types.overlap([event.event_type, event.base_event_type]),
                    ),
                    literal(event.payload, JSONB).contains(cls.payload_match),
                )
            )
            return list(rows.unique())

    async def sees(self, summary: EventSummary) -> bool:
        """Only joined non-DM Slack channels reach subscriptions."""
        if summary.source != "slack":
            return True
        from openswe.slack.channels import SlackChannel

        channel = await SlackChannel.load(summary.slack_channel_id)
        return channel is not None and channel.details.publishes_events

    async def match(self, event: LoggedEvent, summary: EventSummary) -> bool:
        """Record ``event`` as owed to this thread; ``False`` when nothing new is owed.

        The trigger is counted in the same transaction as the insert, so a
        redelivered webhook leaves the subscription exactly as it was.
        """
        details = summary.details
        content = prompt(
            "runs/event-subscription",
            summary=summary,
            details=fence_github_comment_body(details, registered=summary.trusted)
            if details
            else "",
            instructions=self.instructions,
            subscription_id=str(self.id),
            one_shot=self.one_shot,
        )
        match = EventMatch(
            thread_id=self.thread_id,
            subscription_id=self.id,
            source=event.source,
            delivery_id=event.delivery_id,
            content=content,
            run_config=self.run_config,
        )
        try:
            async with postgres.session() as session:
                if not await self._claim(session):
                    return False
                if not await match.record(session):
                    raise _AlreadyOwedError
        except _AlreadyOwedError:
            return False
        pull_request_closed = event.event_type == "pull_request.closed"
        if self.one_shot or (self.pull_request_id is not None and pull_request_closed):
            await self.delete()
        return True

    async def delete(self) -> None:
        async with postgres.session() as session:
            await session.execute(delete(type(self)).where(type(self).id == self.id))

    async def _claim(self, session: AsyncSession) -> bool:
        """Count a trigger; a one-shot that already fired, or a cancelled one, claims nothing."""
        cls = type(self)
        claimed = await session.scalar(
            update(cls)
            .where(cls.id == self.id, ~cls._expired(), ~cls.one_shot | (cls.trigger_count == 0))
            .values(trigger_count=cls.trigger_count + 1, last_triggered_at=func.clock_timestamp())
            .returning(cls.id)
        )
        return claimed is not None


class _AlreadyOwedError(Exception):
    """Rolls back a trigger whose delivery the thread already has."""
