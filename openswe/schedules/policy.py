"""Ownership rules shared by automation entrypoints."""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException

from openswe.dashboard.admin import is_admin
from openswe.users import User


@dataclass(frozen=True)
class AutomationPolicy:
    """Who an automation belongs to, and so what it may do.

    A system automation belongs to the environment: only admins manage it, and its runs
    run on the workspace's default model. A personal one belongs to its creator, admin or
    not: it runs as them and is billed to them, on Auto model routing, with no cap on how
    many they keep or how much each spends. A viewer's policy is their own, personal.
    """

    owner: User | None
    system: bool = False

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

    @classmethod
    async def for_thread(cls, metadata: Mapping[str, object]) -> AutomationPolicy:
        """Resolve a run's saved owner without assigning an orphan to another person."""
        if metadata.get("automation_system") is True:
            return cls(None, system=True)
        if creator_id := metadata.get("automation_creator_user_id"):
            user_id = User.key_id(f"user:{creator_id}")
            return cls(await User.get(user_id) if user_id is not None else None)
        from openswe.schedules.store import get_agent_schedule

        schedule_id = metadata.get("schedule_id")
        if not isinstance(schedule_id, str) or not schedule_id:
            return cls(None)
        record = await get_agent_schedule(schedule_id)
        if record is None or record.get("workspace") != metadata.get("workspace"):
            return cls(None)
        policy = await cls.for_record(record)
        creator = metadata.get("created_by")
        if not isinstance(creator, str) or not creator:
            return cls(None, system=policy.system)
        saved_creator = await User.for_login("github", creator)
        if policy.owner is None or saved_creator is None or saved_creator.id != policy.owner.id:
            return cls(None, system=policy.system)
        return policy

    def as_system(self, system: bool) -> AutomationPolicy:
        """This automation's policy once it is made a system one, or a personal one."""
        return AutomationPolicy(self.owner, system=system)

    @property
    def admin(self) -> bool:
        """Whether the person is an admin: for a viewer, whether they may manage everything."""
        return self.owner is not None and is_admin(self.owner.email, login=self.owner.github_login)

    @property
    def personal(self) -> bool:
        """Whether the automation is its creator's own: run as them, on Auto, and never
        as an admin thread."""
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
        """Whether this viewer's list shows ``automation``: their own, system ones for an
        admin, and every personal one too in an admin's ``all_users`` view."""
        return all_users or automation.owned_by(self) or (automation.system and self.admin)

    def check_creation(self) -> None:
        """Refuse a personal automation nobody owns; a system automation needs no owner."""
        if not self.system and self.owner is None:
            raise HTTPException(403, "A linked Robin account is required")
