"""Durable app-first notifications with acknowledged delivery and Slack fallback."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Self
from uuid import UUID, uuid7

from pydantic import BaseModel, computed_field
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from openswe import event_claims
from openswe.database import postgres
from openswe.notifications.models import Notification
from openswe.slack.dm import DmOrigin, post_dm
from openswe.slack.http import SlackRequestError
from openswe.ui_invalidations.topics import Topic
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_base_url

logger = logging.getLogger(__name__)
JOB_KIND = "notification-fallback"


class AppNotification(BaseModel):
    id: UUID
    title: str
    body: str
    url: str

    @computed_field
    @property
    def path(self) -> str | None:
        """The in-app route for ``url``; ``None`` when it points outside the dashboard."""
        base = dashboard_base_url()
        if not base or not self.url.startswith(f"{base}/"):
            return None
        return self.url.removeprefix(base)

    @classmethod
    async def send(
        cls,
        recipient: User,
        *,
        key: str,
        title: str,
        body: str,
        url: str,
        fallback_text: str,
        origin: DmOrigin | None = None,
        by: UUID | None = None,
    ) -> None:
        async with postgres.session() as session:
            await session.execute(
                insert(Notification)
                .values(
                    id=uuid7(),
                    recipient_id=recipient.id,
                    dedupe_key=key,
                    title=title,
                    body=body,
                    url=url,
                    fallback_text=fallback_text,
                    origin=origin.model_dump(mode="json") if origin else None,
                    fallback_at=datetime.now(UTC) + timedelta(seconds=60),
                    created_by=by,
                    updated_by=by,
                )
                .on_conflict_do_nothing(
                    index_elements=[Notification.recipient_id, Notification.dedupe_key]
                )
            )
            await Topic.NOTIFICATIONS.invalidate(session, key=str(recipient.id))

    @classmethod
    async def send_to_slack(
        cls,
        slack_user_id: str,
        *,
        key: str,
        title: str,
        body: str,
        url: str,
        fallback_text: str,
        origin: DmOrigin | None = None,
        by: UUID | None = None,
    ) -> None:
        recipient = await User.for_identity("slack", slack_user_id)
        if recipient is None:
            claim_key = f"{slack_user_id}:{key}"
            if not await event_claims.claim(
                "notification-fallback", claim_key, ttl=timedelta(days=7)
            ):
                return
            try:
                await post_dm(slack_user_id, fallback_text, origin=origin)
            except Exception:
                await event_claims.release("notification-fallback", claim_key)
                raise
            return
        await cls.send(
            recipient,
            key=key,
            title=title,
            body=body,
            url=url,
            fallback_text=fallback_text,
            origin=origin,
            by=by,
        )

    @classmethod
    async def pending(cls, recipient: UUID) -> list[Self]:
        async with postgres.session() as session:
            rows = await session.scalars(
                select(Notification)
                .where(
                    Notification.recipient_id == recipient,
                    Notification.delivered_at.is_(None),
                    Notification.fallback_sent_at.is_(None),
                )
                .order_by(Notification.created_at)
                .limit(50)
            )
            return [cls.model_validate(row, from_attributes=True) for row in rows]

    @classmethod
    async def acknowledge(cls, recipient: UUID, notification_id: UUID) -> bool:
        async with postgres.session() as session:
            acknowledged_id = await session.scalar(
                update(Notification)
                .where(
                    Notification.id == notification_id,
                    Notification.recipient_id == recipient,
                    Notification.fallback_sent_at.is_(None),
                )
                .values(
                    delivered_at=func.clock_timestamp(),
                    updated_at=func.clock_timestamp(),
                    updated_by=recipient,
                )
                .returning(Notification.id)
            )
            if acknowledged_id is not None:
                await Topic.NOTIFICATIONS.invalidate(session, key=str(recipient))
            return acknowledged_id is not None

    @classmethod
    async def fallback(cls) -> None:
        async with postgres.session() as session:
            ids = list(
                await session.scalars(
                    select(Notification.id)
                    .where(
                        Notification.delivered_at.is_(None),
                        Notification.fallback_sent_at.is_(None),
                        Notification.fallback_failed_at.is_(None),
                        Notification.fallback_at <= func.clock_timestamp(),
                    )
                    .order_by(Notification.fallback_at)
                    .limit(100)
                )
            )
        for notification_id in ids:
            try:
                await cls.deliver_fallback(notification_id)
            except Exception:
                logger.exception(
                    "Notification fallback failed", extra={"notification_id": str(notification_id)}
                )

    @classmethod
    async def deliver_fallback(cls, notification_id: UUID) -> None:
        claim = uuid7()
        async with postgres.session() as session:
            row = await session.scalar(
                update(Notification)
                .where(
                    Notification.id == notification_id,
                    Notification.delivered_at.is_(None),
                    Notification.fallback_sent_at.is_(None),
                    Notification.fallback_failed_at.is_(None),
                    Notification.fallback_at <= func.clock_timestamp(),
                )
                .values(
                    fallback_claim=claim,
                    fallback_attempts=Notification.fallback_attempts + 1,
                    fallback_at=func.clock_timestamp() + timedelta(minutes=5),
                    updated_at=func.clock_timestamp(),
                    updated_by=None,
                )
                .returning(Notification)
            )
        if row is None:
            return
        failed = False
        sent = False
        try:
            recipient = await User.get(row.recipient_id)
            if recipient is None or not recipient.slack_user_id:
                failed = True
            else:
                origin = DmOrigin.model_validate(row.origin) if row.origin else None
                await post_dm(recipient.slack_user_id, row.fallback_text, origin=origin)
                sent = True
        except Exception as exc:
            failed = row.fallback_attempts >= 5 or (
                isinstance(exc, SlackRequestError)
                and exc.code
                in {
                    "user_not_found",
                    "account_inactive",
                    "cannot_dm_bot",
                    "user_disabled",
                    "channel_not_found",
                }
            )
            logger.warning(
                "Notification fallback delivery failed",
                extra={"notification_id": str(notification_id), "terminal": failed},
                exc_info=True,
            )
        async with postgres.session() as session:
            await session.execute(
                update(Notification)
                .where(Notification.id == notification_id, Notification.fallback_claim == claim)
                .values(
                    fallback_claim=None,
                    fallback_sent_at=func.clock_timestamp()
                    if sent
                    else Notification.fallback_sent_at,
                    fallback_failed_at=func.clock_timestamp()
                    if failed
                    else Notification.fallback_failed_at,
                    fallback_at=func.clock_timestamp()
                    + timedelta(seconds=min(3600, 60 * 2 ** min(row.fallback_attempts, 6))),
                    updated_at=func.clock_timestamp(),
                    updated_by=None,
                )
            )
