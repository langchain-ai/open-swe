"""Durable app-first notifications with acknowledged delivery and Slack fallback."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Self
from uuid import UUID, uuid7

from pydantic import BaseModel
from sqlalchemy import text

from openswe import event_claims
from openswe.database import postgres
from openswe.slack.dm import DmOrigin, post_dm
from openswe.ui_invalidations.topics import Topic
from openswe.users import User

logger = logging.getLogger(__name__)
JOB_KIND = "notification-fallback"


class AppNotification(BaseModel):
    id: UUID
    title: str
    body: str
    url: str

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
        async with postgres.transaction() as conn:
            await conn.execute(
                text("""
                    INSERT INTO app_notification
                        (id, recipient_id, dedupe_key, title, body, url, fallback_text,
                         origin, fallback_at, created_by, updated_by)
                    VALUES (:id, :recipient, :key, :title, :body, :url, :fallback,
                            CAST(:origin AS jsonb), :due, :by, :by)
                    ON CONFLICT (recipient_id, dedupe_key) DO NOTHING
                """),
                {
                    "id": uuid7(),
                    "recipient": recipient.id,
                    "key": key,
                    "title": title,
                    "body": body,
                    "url": url,
                    "fallback": fallback_text,
                    "origin": origin.model_dump_json() if origin else None,
                    "due": datetime.now(UTC) + timedelta(seconds=60),
                    "by": by,
                },
            )
            await Topic.NOTIFICATIONS.invalidate(conn, key=str(recipient.id))

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
        async with postgres.connection() as conn:
            rows = await conn.execute(
                text(
                    "SELECT id, title, body, url FROM app_notification "
                    "WHERE recipient_id = :recipient AND delivered_at IS NULL "
                    "AND fallback_sent_at IS NULL ORDER BY created_at LIMIT 50"
                ),
                {"recipient": recipient},
            )
            return [cls.model_validate(dict(row)) for row in rows.mappings()]

    @classmethod
    async def acknowledge(cls, recipient: UUID, notification_id: UUID) -> bool:
        async with postgres.transaction() as conn:
            result = await conn.execute(
                text(
                    "UPDATE app_notification SET delivered_at = clock_timestamp(), "
                    "updated_at = clock_timestamp(), updated_by = :recipient "
                    "WHERE id = :id AND recipient_id = :recipient "
                    "AND fallback_sent_at IS NULL RETURNING id"
                ),
                {"id": notification_id, "recipient": recipient},
            )
            acknowledged = result.first() is not None
            if acknowledged:
                await Topic.NOTIFICATIONS.invalidate(conn, key=str(recipient))
            return acknowledged

    @classmethod
    async def fallback(cls) -> None:
        async with postgres.connection() as conn:
            ids = list(
                await conn.scalars(
                    text(
                        "SELECT id FROM app_notification WHERE delivered_at IS NULL "
                        "AND fallback_sent_at IS NULL AND fallback_at <= clock_timestamp() "
                        "ORDER BY fallback_at LIMIT 100"
                    )
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
        async with postgres.transaction() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT recipient_id, fallback_text, origin FROM app_notification "
                            "WHERE id = :id AND delivered_at IS NULL AND fallback_sent_at IS NULL "
                            "AND fallback_at <= clock_timestamp() FOR UPDATE SKIP LOCKED"
                        ),
                        {"id": notification_id},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return
            recipient = await User.get(row["recipient_id"])
            if recipient is None or not recipient.slack_user_id:
                return
            origin = DmOrigin.model_validate(row["origin"]) if row["origin"] else None
            await post_dm(recipient.slack_user_id, row["fallback_text"], origin=origin)
            await conn.execute(
                text(
                    "UPDATE app_notification SET fallback_sent_at = clock_timestamp(), "
                    "updated_at = clock_timestamp(), updated_by = NULL WHERE id = :id"
                ),
                {"id": notification_id},
            )
