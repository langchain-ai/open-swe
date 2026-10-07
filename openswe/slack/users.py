"""Postgres-backed Slack profile cache shared by ingress and the dashboard."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Self

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.config import ENV
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.slack.http import SLACK_REQUEST_ERRORS, SlackClient, slack_error
from openswe.utils.json_types import JsonObject, as_json_object

logger = logging.getLogger(__name__)
PROFILE_TTL = timedelta(hours=1)


class SlackUser(Base):
    __tablename__ = "slack_user"

    id: Mapped[str] = mapped_column(primary_key=True)
    payload: Mapped[JsonObject] = mapped_column(JSONB, default_factory=dict)
    fetched_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @property
    def display_name(self) -> str:
        profile = as_json_object(self.payload.get("profile"))
        for value in (
            profile.get("display_name"),
            profile.get("real_name"),
            self.payload.get("name"),
        ):
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @property
    def avatar_url(self) -> str:
        profile = as_json_object(self.payload.get("profile"))
        for key in ("image_72", "image_48", "image_192"):
            value = profile.get(key)
            if isinstance(value, str) and value.startswith("https://"):
                return value
        return ""

    @classmethod
    async def load(cls, user_id: str) -> Self | None:
        known = None
        if postgres.configured():
            try:
                async with postgres.session() as session:
                    known = await session.get(cls, user_id)
            except Exception:
                logger.warning(
                    "Slack profile cache read failed",
                    extra={"slack_user_id": user_id},
                    exc_info=True,
                )
        if (
            known is not None
            and known.fetched_at is not None
            and datetime.now(UTC) - known.fetched_at < PROFILE_TTL
        ):
            return known
        if not ENV.SLACK_BOT_TOKEN.optional():
            return known
        try:
            async with SlackClient.bot() as client:
                data = await client.users_info(user=user_id)
        except SLACK_REQUEST_ERRORS as exc:
            logger.warning("Slack user lookup failed", extra={"slack_error": slack_error(exc)})
            return known
        payload = data.get("user")
        if not isinstance(payload, dict):
            return known
        row = cls(id=user_id, payload=as_json_object(payload))
        if postgres.configured():
            try:
                upsert = insert(cls).values(id=user_id, payload=row.payload)
                async with postgres.session() as session:
                    await session.execute(
                        upsert.on_conflict_do_update(
                            index_elements=[cls.id],
                            set_={
                                "payload": upsert.excluded.payload,
                                "fetched_at": func.clock_timestamp(),
                            },
                        )
                    )
            except Exception:
                logger.warning(
                    "Slack profile cache write failed",
                    extra={"slack_user_id": user_id},
                    exc_info=True,
                )
        return row
