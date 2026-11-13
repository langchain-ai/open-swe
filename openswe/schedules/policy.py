"""Ownership and resource limits shared by automation entrypoints."""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar, Final, TypedDict
from uuid import UUID

from fastapi import HTTPException
from runtime_api.catalog import AUTOMATION_DAILY_TOKENS
from runtime_api.loop import AutomationUsage
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from openswe.dashboard.admin import is_admin
from openswe.threads.index import ThreadSearch
from openswe.threads.store import THREADS
from openswe.users import User

# Thread metadata holding a member's automation thread's budget counter, as its run last
# reported it.
AUTOMATION_USAGE_KEY: Final = "automation_usage"


class ThreadUsage(TypedDict):
    """The automation thread that spent the most of its budget today."""

    threadId: str
    tokens: int


@dataclass(frozen=True)
class AutomationPolicy:
    """Who an automation belongs to, and so what it may do.

    A system automation belongs to the environment: only admins manage it, and its runs
    are unbounded. A personal one belongs to its creator, admin or not: it counts toward
    their three, and runs on the workspace Fast model within a daily token budget per
    thread. A viewer's policy is their own, personal.
    """

    owner: User | None
    system: bool = False

    MAX_AUTOMATIONS: ClassVar = 3
    DAILY_TOKENS: ClassVar = AUTOMATION_DAILY_TOKENS
    # The most of today's automation threads read for usage; the counter is best-effort.
    USAGE_THREADS: ClassVar = 1000

    @classmethod
    async def for_login(cls, login: str) -> AutomationPolicy:
        return cls(await User.for_login("github", login))

    @classmethod
    async def for_record(cls, record: Mapping[str, object]) -> AutomationPolicy:
        return cls(await User.automation_creator(record), system=record.get("system") is True)

    @classmethod
    async def for_schedule(cls, schedule_id: str) -> AutomationPolicy:
        """The policy of the automation a run was started for; a deleted one is personal
        and has no owner, so it stays bounded."""
        from openswe.schedules.store import get_agent_schedule

        record = await get_agent_schedule(schedule_id)
        return cls(None) if record is None else await cls.for_record(record)

    @classmethod
    async def for_records(cls, records: Sequence[Mapping[str, object]]) -> list[AutomationPolicy]:
        """Each record's policy, reading creators by id in one query; rows saved before the
        creator's id are read by login."""
        ids = [record.get("created_by_user_id") for record in records]
        users = await User.for_ids({UUID(str(user_id)) for user_id in ids if user_id})
        legacy = iter(
            await asyncio.gather(
                *(
                    User.automation_creator(record)
                    for record, user_id in zip(records, ids, strict=True)
                    if not user_id
                )
            )
        )
        return [
            cls(
                users.get(UUID(str(user_id))) if user_id else next(legacy),
                system=record.get("system") is True,
            )
            for record, user_id in zip(records, ids, strict=True)
        ]

    def as_system(self, system: bool) -> AutomationPolicy:
        """This automation's policy once it is made a system one, or a personal one."""
        return AutomationPolicy(self.owner, system=system)

    @property
    def admin(self) -> bool:
        """Whether the person is an admin: for a viewer, whether they may manage everything."""
        return self.owner is not None and is_admin(self.owner.email, login=self.owner.github_login)

    @property
    def restricted(self) -> bool:
        """Whether the automation runs pinned to Fast, within the daily token budget."""
        return not self.system

    def owned_by(self, actor: AutomationPolicy) -> bool:
        return (
            not self.system
            and self.owner is not None
            and actor.owner is not None
            and actor.owner.id == self.owner.id
        )

    def authorize(self, actor: AutomationPolicy) -> None:
        """Refuse a change to this automation by anyone but an admin or, for a personal
        automation, its creator."""
        if not (actor.admin or self.owned_by(actor)):
            raise HTTPException(
                403,
                "Only admins can manage system automations"
                if self.system
                else "Only the creator or an admin can manage this automation",
            )

    def check_kind(self, actor: AutomationPolicy) -> None:
        """Refuse to make or keep a system automation for anyone but an admin."""
        if self.system and not actor.admin:
            raise HTTPException(403, "Only admins can create system automations")

    def check_listing(self, *, all_users: bool) -> None:
        """Refuse this viewer a list of every user's automations unless they are an admin."""
        if all_users and not self.admin:
            raise HTTPException(403, "Only admins can list every user's automations")

    def lists(self, automation: AutomationPolicy, *, all_users: bool) -> bool:
        """Whether this viewer's list shows ``automation``: their own and system ones, and
        every personal one too in an admin's ``all_users`` view."""
        return all_users or automation.system or automation.owned_by(self)

    async def check_creation(self, conn: AsyncConnection) -> None:
        """Refuse a personal automation past its creator's three, paused ones included;
        a system automation has no such limit."""
        if self.system:
            return
        if self.owner is None:
            raise HTTPException(403, "A linked Robin account is required")
        await conn.execute(
            text("SELECT id FROM users WHERE id = :id FOR UPDATE"), {"id": self.owner.id}
        )
        count = await conn.scalar(
            text("SELECT count(*) FROM automation WHERE created_by_user_id = :id AND NOT system"),
            {"id": self.owner.id},
        )
        if count is not None and count >= self.MAX_AUTOMATIONS:
            raise HTTPException(
                422,
                f"Each user can have at most {self.MAX_AUTOMATIONS} personal automations "
                "(including paused automations)",
            )

    @staticmethod
    async def record_usage(thread_id: str, usage: AutomationUsage) -> None:
        """Keep the budget counter a run of a member's automation reported on its thread."""
        await THREADS.update(thread_id, {AUTOMATION_USAGE_KEY: usage.model_dump()})

    @classmethod
    async def usage_today(cls) -> dict[str, ThreadUsage]:
        """Each automation's thread that spent the most of today's budget, by automation id."""
        today = datetime.now(UTC).date().isoformat()
        threads = await THREADS.search(
            ThreadSearch(
                metadata={AUTOMATION_USAGE_KEY: {"day": today}},
                limit=cls.USAGE_THREADS,
                sort_by="updated_at",
            )
        )
        busiest: dict[str, ThreadUsage] = {}
        for thread in threads:
            schedule_id = thread.metadata.get("schedule_id")
            usage = thread.metadata.get(AUTOMATION_USAGE_KEY)
            if not isinstance(schedule_id, str) or not isinstance(usage, dict):
                continue
            tokens = usage.get("tokens")
            if not isinstance(tokens, int):
                continue
            if schedule_id not in busiest or tokens > busiest[schedule_id]["tokens"]:
                busiest[schedule_id] = {"threadId": thread.thread_id, "tokens": tokens}
        return busiest
