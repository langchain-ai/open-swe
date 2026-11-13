"""Ownership and resource limits shared by automation entrypoints."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid7

from fastapi import HTTPException
from langchain_core.messages import AIMessage, BaseMessage
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from openswe.dashboard.admin import is_admin
from openswe.database.postgres import transaction
from openswe.users import User


@dataclass(frozen=True)
class AutomationPolicy:
    owner: User | None

    MAX_AUTOMATIONS = 3
    DAILY_TOKENS = 30_000_000

    @classmethod
    async def for_login(cls, login: str) -> AutomationPolicy:
        return cls(await User.for_login("github", login))

    @classmethod
    async def for_record(cls, record: Mapping[str, object]) -> AutomationPolicy:
        return cls(await User.automation_creator(record))

    @property
    def admin(self) -> bool:
        return self.owner is not None and is_admin(self.owner.email, login=self.owner.github_login)

    def authorize(self, actor: User | None) -> None:
        if actor is None or (
            not AutomationPolicy(actor).admin and (self.owner is None or actor.id != self.owner.id)
        ):
            raise HTTPException(403, "Only the creator or an admin can manage this automation")

    async def check_creation(self, conn: AsyncConnection) -> None:
        if self.owner is None:
            raise HTTPException(403, "A linked Open SWE account is required")
        await conn.execute(
            text("SELECT id FROM users WHERE id = :id FOR UPDATE"), {"id": self.owner.id}
        )
        count = await conn.scalar(
            text("SELECT count(*) FROM automation WHERE created_by_user_id = :id"),
            {"id": self.owner.id},
        )
        if count is not None and count >= self.MAX_AUTOMATIONS:
            raise HTTPException(
                422, "Each user can create at most 3 automations (including paused automations)"
            )

    async def permits_call(self, automation_id: str, messages: Sequence[BaseMessage]) -> bool:
        if self.admin:
            return True
        async with transaction() as conn:
            exists = await conn.scalar(
                text("SELECT id FROM automation WHERE id = :id FOR UPDATE"),
                {"id": UUID(automation_id)},
            )
            if exists is None:
                return False
            for message in messages:
                if not isinstance(message, AIMessage):
                    continue
                if not message.usage_metadata:
                    raise ValueError(
                        "Automation model response has no token usage; refusing unmetered calls"
                    )
                if not message.id:
                    raise ValueError("Automation usage requires a model message id")
                await conn.execute(
                    text("""
                        INSERT INTO automation_token_usage (id, automation_id, message_id, tokens)
                        VALUES (:id, :automation_id, :message_id, :tokens)
                        ON CONFLICT (automation_id, message_id) DO NOTHING
                    """),
                    {
                        "id": uuid7(),
                        "automation_id": UUID(automation_id),
                        "message_id": message.id,
                        "tokens": message.usage_metadata["total_tokens"],
                    },
                )
            used = await conn.scalar(
                text("""
                    SELECT COALESCE(sum(tokens), 0) FROM automation_token_usage
                    WHERE automation_id = :id AND created_at >= :day
                """),
                {
                    "id": UUID(automation_id),
                    "day": datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0),
                },
            )
            if used is not None and used >= self.DAILY_TOKENS:
                await conn.execute(
                    text(
                        "UPDATE automation SET last_error = :error, last_error_at = clock_timestamp() WHERE id = :id"
                    ),
                    {
                        "id": UUID(automation_id),
                        "error": "Daily limit of 30M tokens reached; resumes at 00:00 UTC",
                    },
                )
                return False
        return True
