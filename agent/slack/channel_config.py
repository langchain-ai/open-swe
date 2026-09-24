"""Per-channel behaviour admins turn on from the dashboard, one row per Slack channel."""

import logging
import re
from datetime import datetime
from typing import Self

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.slack.channels import SlackChannel

logger = logging.getLogger(__name__)

_CHANNEL_ID = re.compile(r"^[CG][A-Z0-9]{8,}$")


MAX_INSTRUCTIONS_CHARS = 4000


class SlackChannelConfigUpdate(BaseModel):
    watch_pull_requests: bool = False
    instructions: str = Field(default="", max_length=MAX_INSTRUCTIONS_CHARS)


class SlackChannelConfigView(SlackChannelConfigUpdate):
    model_config = ConfigDict(from_attributes=True)

    channel_id: str
    updated_by: str
    updated_at: datetime | None


class SlackChannelConfig(Base):
    __tablename__ = "slack_channel_config"

    channel_id: Mapped[str] = mapped_column(primary_key=True)
    watch_pull_requests: Mapped[bool] = mapped_column(default=False)
    instructions: Mapped[str] = mapped_column(server_default="", default="")
    updated_by: Mapped[str] = mapped_column(server_default="", default="")
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @classmethod
    async def all(cls) -> list[Self]:
        async with postgres.session() as session:
            return list(await session.scalars(select(cls).order_by(cls.channel_id)))

    @classmethod
    async def get(cls, channel_id: str) -> Self | None:
        if not postgres.configured():
            return None
        async with postgres.session() as session:
            return await session.get(cls, channel_id)

    @classmethod
    async def watches_pull_requests(cls, channel_id: str) -> bool:
        config = await cls.get(channel_id)
        return config is not None and config.watch_pull_requests

    @classmethod
    async def instructions_for(cls, channel_id: str) -> str:
        """The admin-written standing instructions for a channel, or ``""``."""
        try:
            config = await cls.get(channel_id)
        except Exception:
            logger.warning(
                "Slack channel instructions lookup failed",
                extra={"slack_channel": channel_id},
                exc_info=True,
            )
            return ""
        return config.instructions.strip() if config is not None else ""

    @classmethod
    async def save(cls, channel_id: str, update: SlackChannelConfigUpdate, login: str) -> Self:
        """Upsert a channel's config, joining the channel so Slack delivers its messages."""
        if not _CHANNEL_ID.fullmatch(channel_id):
            raise HTTPException(status_code=422, detail="Invalid Slack channel ID")
        values = {**update.model_dump(), "updated_by": login}
        upsert = insert(cls).values(channel_id=channel_id, **values)
        async with postgres.session() as session:
            saved = await session.scalar(
                upsert.on_conflict_do_update(
                    index_elements=[cls.channel_id],
                    set_={**values, "updated_at": func.clock_timestamp()},
                )
                .returning(cls)
                .execution_options(populate_existing=True)
            )
        if saved is None:
            raise HTTPException(status_code=500, detail="Slack channel config was not saved")
        if update.watch_pull_requests and (channel := await SlackChannel.load(channel_id)):
            await channel.join()
        return saved

    @classmethod
    async def remove(cls, channel_id: str) -> None:
        async with postgres.session() as session:
            await session.execute(delete(cls).where(cls.channel_id == channel_id))
