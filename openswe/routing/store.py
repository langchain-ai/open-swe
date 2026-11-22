"""Instance-wide integration bindings, independent of execution configuration."""

import logging
import re
from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid7

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import ForeignKey, Text, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.repo_names import normalize_repo_full_name
from openswe.github.repositories import Repository
from openswe.slack.channels import SlackChannel
from openswe.ui_invalidations import Topic

logger = logging.getLogger(__name__)

type ChannelPrompt = Annotated[str, Field(max_length=32_000)]


class IntegrationRecord(Base, kw_only=True):
    __abstract__ = True

    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


class RepositoryBinding(IntegrationRecord):
    __tablename__ = "integration_repository"

    repository_id: Mapped[UUID] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), unique=True
    )
    merge_queue_config: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSONB(none_as_null=True), default=None
    )


class SlackChannelBinding(IntegrationRecord):
    __tablename__ = "integration_slack_channel"

    channel_id: Mapped[str] = mapped_column(
        ForeignKey("slack_channel.id", ondelete="CASCADE"), unique=True
    )
    prompt: Mapped[str] = mapped_column(Text, default="", server_default="")


class RepositorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str


class ChannelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_id: str
    prompt: ChannelPrompt


class IntegrationSettings(BaseModel):
    repos: list[RepositorySettings]
    slack_channels: list[ChannelSettings]


class IntegrationStore:
    @staticmethod
    def channel_id(raw: str) -> str:
        channel_id = raw.strip().upper()
        if not re.fullmatch(r"[CG][A-Z0-9]+", channel_id):
            raise ValueError("Choose a Slack channel ID starting with C or G")
        return channel_id

    async def settings(self, *, session: AsyncSession | None = None) -> IntegrationSettings:
        async with postgres.session(session) as session:
            repos = list(
                await session.scalars(
                    select(Repository.full_name)
                    .join(RepositoryBinding, RepositoryBinding.repository_id == Repository.id)
                    .order_by(Repository.key)
                )
            )
            channels = list(
                await session.scalars(
                    select(SlackChannelBinding).order_by(SlackChannelBinding.channel_id)
                )
            )
        return IntegrationSettings(
            repos=[RepositorySettings(repo=name) for name in repos],
            slack_channels=[
                ChannelSettings(
                    channel_id=row.channel_id,
                    prompt=row.prompt,
                )
                for row in channels
            ],
        )

    async def configure_repository(
        self,
        full_name: str,
        *,
        actor: UUID | None,
    ) -> RepositorySettings:
        name = normalize_repo_full_name(full_name)
        async with postgres.session() as session:
            repository_id = await Repository.ensure_id(session, name)
            row = RepositoryBinding(
                repository_id=repository_id,
                created_by=actor,
                updated_by=actor,
            )
            upsert = insert(RepositoryBinding).values(
                id=row.id,
                repository_id=repository_id,
                created_by=actor,
                updated_by=actor,
            )
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[RepositoryBinding.repository_id],
                    set_={
                        "updated_by": actor,
                        "updated_at": func.clock_timestamp(),
                    },
                )
            )
        await Topic.ROUTING.invalidate()
        return RepositorySettings(repo=name)

    async def remove_repository(self, full_name: str) -> None:
        from openswe.merge_queue.store import MergeQueue

        key = normalize_repo_full_name(full_name).lower()
        async with postgres.session() as session:
            binding = await session.scalar(
                select(RepositoryBinding)
                .where(
                    RepositoryBinding.repository_id.in_(
                        select(Repository.id).where(Repository.key == key)
                    )
                )
                .with_for_update(of=RepositoryBinding)
            )
            if binding is not None:
                if binding.merge_queue_config is not None or await session.scalar(
                    select(MergeQueue.id).where(MergeQueue.routing_id == binding.id).limit(1)
                ):
                    raise ValueError(
                        "Cannot unbind a repository with merge queue configuration or retained "
                        "queue history. Disable queue processing without removing its binding; "
                        "queue ownership changes require an explicit operator handoff."
                    )
                await session.delete(binding)

        await Topic.ROUTING.invalidate()

    async def channel_prompt(self, channel_id: str) -> str | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(SlackChannelBinding.prompt).where(
                    SlackChannelBinding.channel_id == channel_id.strip().upper()
                )
            )

    async def listens_to_channel(self, channel_id: str) -> bool:
        return await self.channel_prompt(channel_id) is not None

    async def configure_channel(
        self,
        channel_id: str,
        *,
        prompt: str,
        actor: UUID | None,
    ) -> ChannelSettings:
        normalized = self.channel_id(channel_id)
        settings = ChannelSettings(channel_id=normalized, prompt=prompt)
        channel = await SlackChannel.load(normalized, use_cache=False, raise_unavailable=True)
        if channel is None:
            raise ValueError("Choose a Slack channel Robin can access")
        if not channel.can_listen:
            raise ValueError("Listening requires an internal channel Robin has joined")
        async with postgres.session() as session:
            row = SlackChannelBinding(
                channel_id=normalized,
                prompt=prompt,
                created_by=actor,
                updated_by=actor,
            )
            upsert = insert(SlackChannelBinding).values(
                id=row.id,
                channel_id=normalized,
                prompt=prompt,
                created_by=actor,
                updated_by=actor,
            )
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[SlackChannelBinding.channel_id],
                    set_={
                        "prompt": prompt,
                        "updated_by": actor,
                        "updated_at": func.clock_timestamp(),
                    },
                )
            )
        await Topic.ROUTING.invalidate()
        return settings

    async def remove_channel(self, channel_id: str) -> None:
        normalized = self.channel_id(channel_id)
        async with postgres.session() as session:
            await session.execute(
                delete(SlackChannelBinding).where(SlackChannelBinding.channel_id == normalized)
            )

        await Topic.ROUTING.invalidate()


INTEGRATIONS = IntegrationStore()


async def is_listening_channel(channel_id: str) -> bool:
    try:
        return await INTEGRATIONS.listens_to_channel(channel_id)
    except Exception:
        logger.error(
            "Listening channel lookup failed", extra={"slack_channel_id": channel_id}, exc_info=True
        )
        return False
