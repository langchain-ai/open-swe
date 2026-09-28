"""Agent threads listening for event log rows; each match starts a run on the thread."""

import logging
from datetime import datetime
from typing import Literal, Self
from uuid import UUID, uuid7

from langgraph_sdk.errors import NotFoundError
from pydantic import BaseModel, JsonValue, ValidationError
from sqlalchemy import ColumnElement, ForeignKey, Text, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.dispatch import create_durable_run
from agent.github.comments import fence_github_comment_body
from agent.github.org_membership import INTERNAL_BOT_LOGINS
from agent.github.pull_requests import PullRequest
from agent.input_messages import build_run_input
from agent.prompts import prompt
from agent.webhooks.event_log import LoggedEvent

logger = logging.getLogger(__name__)

type MultitaskStrategy = Literal["enqueue", "interrupt"]

_OWN_BOT_LOGINS = frozenset({"open-swe[bot]", "openswe-dev[bot]"})
_TRUSTED_BOT_LOGINS = frozenset(login.lower() for login in INTERNAL_BOT_LOGINS)
# CI results describe a commit, so they matter even when Open SWE pushed it.
_CI_EVENT_TYPES = frozenset({"check_run", "check_suite", "workflow_run"})
_MAX_BODY_CHARS = 8_000
_SENDER_ID = "system:event-subscription"


class _Account(BaseModel):
    login: str = ""


class _Commented(BaseModel):
    body: str | None = None
    html_url: str = ""
    state: str = ""


class _Check(BaseModel):
    name: str = ""
    status: str = ""
    conclusion: str | None = None
    html_url: str | None = None


class _GitHubEvent(BaseModel):
    action: str = ""
    sender: _Account | None = None
    comment: _Commented | None = None
    review: _Commented | None = None
    check_run: _Check | None = None
    check_suite: _Check | None = None
    workflow_run: _Check | None = None

    @property
    def sender_login(self) -> str:
        return self.sender.login.lower() if self.sender else ""

    @property
    def body(self) -> str:
        commented = self.comment or self.review
        return (commented.body or "") if commented else ""

    @property
    def link(self) -> str:
        if commented := self.comment or self.review:
            return commented.html_url
        return (check.html_url or "") if (check := self.check) else ""

    @property
    def status(self) -> str:
        if check := self.check:
            return " ".join(part for part in (check.name, check.conclusion or check.status) if part)
        return self.review.state if self.review else ""

    @property
    def check(self) -> _Check | None:
        return self.check_run or self.workflow_run or self.check_suite


class EventSubscription(Base):
    __tablename__ = "event_subscription"

    thread_id: Mapped[str]
    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("pull_request.id"))
    multitask_strategy: Mapped[MultitaskStrategy] = mapped_column(Text)
    run_config: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    expires_at: Mapped[datetime]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    source: Mapped[Literal["github"]] = mapped_column(Text, default="github")
    event_types: Mapped[list[str]] = mapped_column(ARRAY(Text), default_factory=list)
    actions: Mapped[list[str]] = mapped_column(ARRAY(Text), default_factory=list)
    instructions: Mapped[str] = mapped_column(default="")
    one_shot: Mapped[bool] = mapped_column(default=False)
    trigger_count: Mapped[int] = mapped_column(server_default="0", init=False)
    last_triggered_at: Mapped[datetime | None] = mapped_column(default=None, init=False)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    pull_request: Mapped[PullRequest] = relationship(init=False, lazy="joined")

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
        if event.pull_request_id is None:
            return
        try:
            summary = _GitHubEvent.model_validate(event.payload)
        except ValidationError:
            logger.warning(
                "Event payload is not a GitHub delivery",
                extra={"event_delivery_id": event.delivery_id},
                exc_info=True,
            )
            return
        if summary.sender_login in _OWN_BOT_LOGINS and event.event_type not in _CI_EVENT_TYPES:
            return
        try:
            subscriptions = await cls._matching(event, summary.action)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Loading event subscriptions failed",
                extra={"event_delivery_id": event.delivery_id},
                exc_info=True,
            )
            return
        for subscription in subscriptions:
            extra = {
                "event_subscription_id": str(subscription.id),
                "agent_thread_id": subscription.thread_id,
                "event_delivery_id": event.delivery_id,
            }
            try:
                await subscription.wake(event, summary)
            except NotFoundError:
                logger.info("Event subscription thread is gone", extra=extra)
                await subscription.delete()
            except Exception:  # noqa: BLE001
                logger.warning("Waking an event subscription failed", extra=extra, exc_info=True)

    @classmethod
    async def _matching(cls, event: LoggedEvent, action: str) -> list[Self]:
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls).where(
                    cls.source == event.source,
                    cls.pull_request_id == event.pull_request_id,
                    ~cls._expired(),
                    or_(
                        func.cardinality(cls.event_types) == 0,
                        cls.event_types.contains([event.event_type]),
                    ),
                    or_(func.cardinality(cls.actions) == 0, cls.actions.contains([action])),
                )
            )
            return list(rows.unique())

    async def wake(self, event: LoggedEvent, summary: _GitHubEvent) -> None:
        if not await self._claim():
            return
        registered = event.user_id is not None or summary.sender_login in _TRUSTED_BOT_LOGINS
        content = prompt(
            "runs/event-subscription",
            event_type=event.event_type,
            action=summary.action,
            sender=summary.sender_login,
            pr_url=self.pull_request.url,
            link=summary.link,
            status=summary.status,
            body=fence_github_comment_body(summary.body[:_MAX_BODY_CHARS], registered=registered)
            if summary.body
            else "",
            instructions=self.instructions,
            subscription_id=str(self.id),
            one_shot=self.one_shot,
        )
        try:
            await create_durable_run(
                self.thread_id,
                "agent",
                input=build_run_input(
                    content,
                    {"sender_id": _SENDER_ID, "surface": "github", "kind": "system"},
                    systems=[
                        {"id": _SENDER_ID, "display_name": "Event listener", "platform": "github"}
                    ],
                ),
                config={"configurable": self.run_config},
                metadata={"kind": "event_subscription", "event_subscription_id": str(self.id)},
                source="github",
                thread_title=None,
                multitask_strategy=self.multitask_strategy,
                if_not_exists="reject",
            )
        except Exception:
            await self._unclaim()
            raise
        if self.one_shot or (event.event_type == "pull_request" and summary.action == "closed"):
            await self.delete()

    async def delete(self) -> None:
        async with postgres.session() as session:
            await session.execute(delete(type(self)).where(type(self).id == self.id))

    async def _claim(self) -> bool:
        """Count a trigger; a one-shot that already fired, or a cancelled one, claims nothing."""
        cls = type(self)
        async with postgres.session() as session:
            claimed = await session.scalar(
                update(cls)
                .where(cls.id == self.id, ~cls._expired(), ~cls.one_shot | (cls.trigger_count == 0))
                .values(
                    trigger_count=cls.trigger_count + 1, last_triggered_at=func.clock_timestamp()
                )
                .returning(cls.id)
            )
            return claimed is not None

    async def _unclaim(self) -> None:
        cls = type(self)
        async with postgres.session() as session:
            await session.execute(
                update(cls)
                .where(cls.id == self.id)
                .values(trigger_count=func.greatest(cls.trigger_count - 1, 0))
            )
