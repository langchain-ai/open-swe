"""Instance-wide integration bindings, independent of execution configuration."""

import logging
import re
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid7

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import CheckConstraint, ForeignKey, Text, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.repo_names import normalize_repo_full_name
from openswe.github.repositories import Repository
from openswe.slack.channels import SlackChannel
from openswe.ui_invalidations import Topic
from openswe.users import User

logger = logging.getLogger(__name__)

type ChannelMode = Literal["mentions", "kitchen", "ask"]


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
    may_start_threads: Mapped[bool] = mapped_column(default=False, server_default="false")
    merge_queue_config: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSONB(none_as_null=True), default=None
    )


class SlackChannelBinding(IntegrationRecord):
    __tablename__ = "integration_slack_channel"
    __table_args__ = (CheckConstraint("mode IN ('mentions', 'kitchen', 'ask')"),)

    channel_id: Mapped[str] = mapped_column(
        ForeignKey("slack_channel.id", ondelete="CASCADE"), unique=True
    )
    mode: Mapped[ChannelMode] = mapped_column(Text, default="mentions", server_default="mentions")


class RepositorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    may_start_threads: bool


class ChannelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_id: str
    mode: ChannelMode


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
            repos = (
                await session.execute(
                    select(Repository.full_name, RepositoryBinding)
                    .join(RepositoryBinding, RepositoryBinding.repository_id == Repository.id)
                    .order_by(Repository.key)
                )
            ).all()
            channels = list(
                await session.scalars(
                    select(SlackChannelBinding).order_by(SlackChannelBinding.channel_id)
                )
            )
        return IntegrationSettings(
            repos=[
                RepositorySettings(
                    repo=name,
                    may_start_threads=row.may_start_threads,
                )
                for name, row in repos
            ],
            slack_channels=[
                ChannelSettings(
                    channel_id=row.channel_id,
                    mode=row.mode,
                )
                for row in channels
            ],
        )

    async def contains_repo(self, full_name: str) -> bool:
        return await self.repository_permission(full_name) is not None

    async def repository_permission(self, full_name: str) -> bool | None:
        try:
            key = normalize_repo_full_name(full_name).lower()
        except ValueError:
            return None
        async with postgres.session() as session:
            return await session.scalar(
                select(RepositoryBinding.may_start_threads)
                .join(Repository, Repository.id == RepositoryBinding.repository_id)
                .where(Repository.key == key)
            )

    async def repository_may_start_threads(self, full_name: str) -> bool:
        return await self.repository_permission(full_name) is True

    async def configure_repository(
        self,
        full_name: str,
        *,
        may_start_threads: bool,
        actor: UUID | None,
    ) -> RepositorySettings:
        name = normalize_repo_full_name(full_name)
        async with postgres.session() as session:
            repository_id = await Repository.ensure_id(session, name)
            row = RepositoryBinding(
                repository_id=repository_id,
                may_start_threads=may_start_threads,
                created_by=actor,
                updated_by=actor,
            )
            upsert = insert(RepositoryBinding).values(
                id=row.id,
                repository_id=repository_id,
                may_start_threads=may_start_threads,
                created_by=actor,
                updated_by=actor,
            )
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[RepositoryBinding.repository_id],
                    set_={
                        "may_start_threads": may_start_threads,
                        "updated_by": actor,
                        "updated_at": func.clock_timestamp(),
                    },
                )
            )
        await Topic.ROUTING.invalidate()
        return RepositorySettings(repo=name, may_start_threads=may_start_threads)

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

    async def channel_mode(self, channel_id: str) -> ChannelMode | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(SlackChannelBinding.mode).where(
                    SlackChannelBinding.channel_id == channel_id.strip().upper()
                )
            )

    async def is_ask_channel(self, channel_id: str) -> bool:
        return await self.channel_mode(channel_id) == "ask"

    async def is_kitchen_channel(self, channel_id: str) -> bool:
        return await self.channel_mode(channel_id) in {"kitchen", "ask"}

    async def configure_channel(
        self,
        channel_id: str,
        *,
        mode: ChannelMode,
        actor: UUID | None,
    ) -> ChannelSettings:
        normalized = self.channel_id(channel_id)
        channel = await SlackChannel.load(normalized, use_cache=False, raise_unavailable=True)
        if channel is None:
            raise ValueError("Choose a Slack channel Robin can access")
        if mode in {"kitchen", "ask"} and not channel.can_be_kitchen:
            raise ValueError("Kitchen and Ask require an internal channel Robin has joined")
        async with postgres.session() as session:
            row = SlackChannelBinding(
                channel_id=normalized,
                mode=mode,
                created_by=actor,
                updated_by=actor,
            )
            upsert = insert(SlackChannelBinding).values(
                id=row.id,
                channel_id=normalized,
                mode=mode,
                created_by=actor,
                updated_by=actor,
            )
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[SlackChannelBinding.channel_id],
                    set_={
                        "mode": mode,
                        "updated_by": actor,
                        "updated_at": func.clock_timestamp(),
                    },
                )
            )
        await Topic.ROUTING.invalidate()
        return ChannelSettings(channel_id=normalized, mode=mode)

    async def remove_channel(self, channel_id: str) -> None:
        normalized = self.channel_id(channel_id)
        async with postgres.session() as session:
            await session.execute(
                delete(SlackChannelBinding).where(SlackChannelBinding.channel_id == normalized)
            )

        await Topic.ROUTING.invalidate()


INTEGRATIONS = IntegrationStore()


async def is_kitchen_channel(channel_id: str, *, slack_user_id: str = "") -> bool:
    try:
        if await INTEGRATIONS.is_kitchen_channel(channel_id):
            return True
        if not channel_id or not slack_user_id:
            return False
        user = await User.for_identity("slack", slack_user_id)
        return (
            user is not None
            and user.typed_preferences.personal_kitchen_channel_id == channel_id.strip().upper()
        )
    except Exception:
        logger.error(
            "Kitchen channel lookup failed", extra={"slack_channel_id": channel_id}, exc_info=True
        )
        return False
