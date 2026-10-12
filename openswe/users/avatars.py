"""Slack display profiles cached in existing per-person records."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Self

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from openswe.config import ENV
from openswe.database import postgres
from openswe.slack.http import SLACK_REQUEST_ERRORS, SlackClient, slack_error
from openswe.users.records import UserRecord
from openswe.utils.json_types import as_json_object

if TYPE_CHECKING:
    from openswe.users.models import User

logger = logging.getLogger(__name__)
PROFILE_TTL = timedelta(hours=1)


@dataclass
class SlackProfile:
    user: User
    display_name: str = ""
    avatar_url: str = ""

    async def load(self) -> Self:
        identity = next((i for i in self.user.identities if i.provider == "slack"), None)
        if identity is None:
            return self
        try:
            async with postgres.session() as session:
                cached = await session.scalar(
                    select(UserRecord).where(
                        UserRecord.user_id == self.user.id,
                        UserRecord.kind == "slack_profile",
                        UserRecord.key == "",
                    )
                )
            if (
                cached is not None
                and cached.updated_at is not None
                and (identity.last_seen_at is None or cached.updated_at >= identity.last_seen_at)
            ):
                name, avatar = cached.value.get("display_name"), cached.value.get("avatar_url")
                self.display_name = name if isinstance(name, str) else ""
                self.avatar_url = avatar if isinstance(avatar, str) else ""
                if datetime.now(UTC) - cached.updated_at < PROFILE_TTL:
                    return self
        except Exception:
            logger.warning(
                "Slack profile cache read failed",
                extra={"user_id": str(self.user.id)},
                exc_info=True,
            )
        if not ENV.SLACK_BOT_TOKEN.optional():
            return self
        try:
            async with SlackClient.bot() as client:
                data = await client.users_info(user=identity.external_id)
        except SLACK_REQUEST_ERRORS as exc:
            logger.warning("Slack user lookup failed", extra={"slack_error": slack_error(exc)})
            return self
        payload = data.get("user")
        if not isinstance(payload, dict):
            return self
        profile = as_json_object(payload.get("profile"))
        self.display_name = next(
            (
                value.strip()
                for value in (
                    profile.get("display_name"),
                    profile.get("real_name"),
                    payload.get("name"),
                )
                if isinstance(value, str) and value.strip()
            ),
            "",
        )
        self.avatar_url = next(
            (
                value
                for key in ("image_72", "image_48", "image_192")
                if isinstance(value := profile.get(key), str) and value.startswith("https://")
            ),
            "",
        )
        try:
            value = {"display_name": self.display_name, "avatar_url": self.avatar_url}
            upsert = insert(UserRecord).values(
                user_id=self.user.id, kind="slack_profile", key="", value=value
            )
            async with postgres.session() as session:
                await session.execute(
                    upsert.on_conflict_do_update(
                        index_elements=[UserRecord.user_id, UserRecord.kind, UserRecord.key],
                        set_={"value": value, "updated_at": func.clock_timestamp()},
                    )
                )
        except Exception:
            logger.warning(
                "Slack profile cache write failed",
                extra={"user_id": str(self.user.id)},
                exc_info=True,
            )
        return self
