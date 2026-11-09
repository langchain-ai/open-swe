"""What threads remember, and the prompt each workspace's curator distils from it.

Every memory waits for the curator of the workspace it was saved in, which may restate
what many people's threads learned in the workspace's prompt. A personal memory also
applies to its person's runs at once, until a line there supersedes it. Memories
saved in private threads before every memory reached the curator stay held from it.
The curator may also link memories to triggers it names, each a path such as
``repo/org/mono/api``: a linked memory leaves its person's context, which reads only the
trigger's summary of when to ``recall`` it. A trigger may also carry the curator's
guidance for everyone in the workspace, recalled the same way.
"""

import hashlib
import hmac
import re
from datetime import datetime
from typing import Final, Literal, Self
from uuid import UUID, uuid7

from pydantic import BaseModel
from sqlalchemy import ColumnElement, ForeignKey, delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.config import ENV
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.users.models import User
from openswe.workspaces.rows import WorkspaceRow

# A personal memory applies to its person; a workspace one only reaches the curator.
Scope = Literal["personal", "workspace"]
Status = Literal["pending", "held", "adopted", "superseded", "declined"]
Decision = Literal["adopted", "superseded", "declined"]
Withdrawal = Literal["deleted", "requested"]

# The thread metadata key naming the workspace a curator thread curates; nobody posts there.
CURATOR_THREAD_KEY: Final = "curator"
# The source of the runs the curator's wake starts, the only runs its tools serve.
CURATOR_SOURCE: Final = "curator"
MAX_MEMORY_CHARS: Final = 2_000
MAX_WORKSPACE_PROMPT_CHARS: Final = 40_000
MAX_TRIGGER_SUMMARY_CHARS: Final = 300
MAX_TRIGGER_GUIDANCE_CHARS: Final = 4_000
_TRIGGER_PATH: Final = re.compile(r"[a-z0-9][a-z0-9/._-]{0,99}")
# The personal memories a person's runs see, newest first.
_PERSONAL_LIMIT: Final = 50
_CURATOR_PAGE: Final = 200


class MemoryRow(Base):
    __tablename__ = "memory"

    scope: Mapped[str]
    from_private_thread: Mapped[bool]
    body: Mapped[str]
    workspace_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(WorkspaceRow.id, ondelete="CASCADE"), default=None
    )
    thread_id: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(server_default="pending", default="pending")
    decision_note: Mapped[str] = mapped_column(server_default="", default="")
    withdrawal_requested: Mapped[bool] = mapped_column(server_default="false", default=False)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )

    @property
    def person_key(self) -> str | None:
        """The same for every memory one person saved in one workspace, and nothing else.

        Keyed by a server secret: the curator's thread is public, and a plain hash of ids
        anyone can guess would name who saved each memory.
        """
        if self.created_by is None:
            return None
        secret = ENV.REMOTE_RUNTIME_TOKEN_SECRET.optional()
        if not secret:
            raise RuntimeError("REMOTE_RUNTIME_TOKEN_SECRET keys the curator's person keys")
        message = f"memory-person:{self.workspace_id}:{self.created_by}".encode()
        return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()[:16]


class WorkspacePromptVersion(Base):
    __tablename__ = "workspace_prompt_version"

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey(WorkspaceRow.id, ondelete="CASCADE"))
    prompt: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )


class MemoryTriggerRow(Base):
    __tablename__ = "memory_trigger"

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey(WorkspaceRow.id, ondelete="CASCADE"))
    # Stored as ``slug``, the column's name from before triggers were paths.
    path: Mapped[str] = mapped_column("slug")
    summary: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    # What the curator wrote at this path for every thread of the workspace; empty for none.
    guidance: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )


class MemoryTriggerLink(Base):
    __tablename__ = "memory_trigger_link"

    trigger_id: Mapped[UUID] = mapped_column(ForeignKey(MemoryTriggerRow.id, ondelete="CASCADE"))
    memory_id: Mapped[UUID] = mapped_column(ForeignKey(MemoryRow.id, ondelete="CASCADE"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )


class MemoryTrigger(BaseModel):
    """When what the trigger at ``path`` holds applies; a run reads this in place of it."""

    path: str
    summary: str

    @classmethod
    def path_of(cls, text: str) -> str:
        """``text`` as a trigger's ``/``-separated path, such as ``repo/org/name``;
        ValueError when it is none. ``:`` separates like ``/``, as older triggers did."""
        path = text.strip().lower().replace(":", "/").rstrip("/")
        if not _TRIGGER_PATH.fullmatch(path):
            raise ValueError(
                "A trigger path is 1-100 lowercase letters, digits and '/._-', "
                "starting with a letter or digit."
            )
        return path

    @staticmethod
    def lineage(path: str) -> list[str]:
        """``path`` and every path above it: ``a/b/c`` gives ``a/b/c``, ``a/b`` and ``a``."""
        parts = path.split("/")
        return ["/".join(parts[:end]) for end in range(len(parts), 0, -1)]

    @property
    def line(self) -> str:
        """The trigger as a run lists it, with the path ``recall`` takes."""
        return f"{self.path}: {self.summary}"


class CuratedTrigger(MemoryTrigger):
    """A trigger as its curator sees it, with the memories it recalls and its guidance."""

    memory_ids: list[UUID]
    guidance: str


class TriggerGuidance(BaseModel):
    """What the curator wrote at ``path`` for every thread of its workspace."""

    path: str
    guidance: str


class PersonalMemory(BaseModel):
    """A memory that reaches its person's runs."""

    id: UUID
    body: str

    @property
    def line(self) -> str:
        """The memory as its person's block lists it, with the id that forgets it."""
        return f"{self.body} (memory {self.id})"


class CuratedMemory(BaseModel):
    """A memory as the curator sees it: never who saved it, nor what a held one says."""

    id: UUID
    # Personal memories already apply to their person, until a line in the prompt supersedes one.
    scope: Scope
    status: Status
    saved_at: datetime
    # The same for every memory one person saved in this workspace, so the curator
    # can count how many people say a thing without learning who they are.
    person: str | None
    from_private_thread: bool
    # None for a held memory.
    body: str | None
    # Where the curator can look for evidence; None for a private thread.
    thread_id: str | None
    # Its person asked to take it back, even after it was adopted.
    withdrawal_requested: bool
    # The paths of the triggers that recall it in place of its body.
    triggers: list[str]

    @classmethod
    def of(cls, row: MemoryRow, triggers: list[str]) -> Self:
        return cls.model_validate(
            {
                "id": row.id,
                "scope": row.scope,
                "status": row.status,
                "saved_at": row.created_at,
                "person": row.person_key,
                "from_private_thread": row.from_private_thread,
                "body": None if row.status == "held" else row.body,
                "thread_id": None if row.from_private_thread else row.thread_id,
                "withdrawal_requested": row.withdrawal_requested,
                "triggers": triggers,
            }
        )


class UnknownWorkspaceError(LookupError):
    """No workspace is stored under the slug a memory names."""


class Memories:
    """Every ``memory`` row, its triggers, and every ``workspace_prompt_version``, by
    workspace slug."""

    @staticmethod
    async def _workspace_id(session: AsyncSession, slug: str) -> UUID:
        workspace_id = await session.scalar(
            select(WorkspaceRow.id).where(WorkspaceRow.slug == slug)
        )
        if workspace_id is None:
            raise UnknownWorkspaceError(slug)
        return workspace_id

    async def save(
        self,
        *,
        scope: Scope,
        body: str,
        person: UUID | None,
        workspace: str | None,
        thread_id: str | None,
        from_private_thread: bool,
    ) -> MemoryRow:
        """Keep a memory for the curator; a workspace one never takes effect without it.

        A personal memory keeps the workspace it was saved in when there is one, for that
        workspace's curator; a workspace memory needs it.
        """
        async with postgres.session() as session:
            workspace_id = (
                await session.scalar(select(WorkspaceRow.id).where(WorkspaceRow.slug == workspace))
                if workspace
                else None
            )
            if scope == "workspace" and workspace_id is None:
                raise UnknownWorkspaceError(workspace or "")
            row = MemoryRow(
                scope=scope,
                body=body,
                workspace_id=workspace_id,
                thread_id=thread_id,
                from_private_thread=from_private_thread,
                created_by=person,
                updated_by=person,
            )
            session.add(row)
        return row

    @staticmethod
    def _applying(person: UUID, curated_in: str | None) -> list[ColumnElement[bool]]:
        """What makes a memory one of ``person``'s that applies to their runs.

        With ``curated_in``, the workspace whose curated prompt the run reads, it leaves out
        the memories its curator superseded: that prompt carries them now. Without it, as for
        a person who does not read that prompt, they keep applying rather than disappear.
        """
        conditions = [
            MemoryRow.scope == "personal",
            MemoryRow.created_by == person,
            MemoryRow.withdrawal_requested.is_(False),
        ]
        if curated_in is not None:
            conditions.append(
                or_(
                    MemoryRow.status != "superseded",
                    MemoryRow.workspace_id.is_distinct_from(
                        select(WorkspaceRow.id)
                        .where(WorkspaceRow.slug == curated_in)
                        .scalar_subquery()
                    ),
                )
            )
        return conditions

    @staticmethod
    def _triggered(path: str | None = None) -> ColumnElement[bool]:
        """Whether a trigger links the memory, or one stored at ``path`` or above it."""
        linked = select(MemoryTriggerLink.id).where(MemoryTriggerLink.memory_id == MemoryRow.id)
        if path is not None:
            linked = linked.join(
                MemoryTriggerRow, MemoryTriggerRow.id == MemoryTriggerLink.trigger_id
            ).where(MemoryTriggerRow.path.in_(MemoryTrigger.lineage(path)))
        return exists(linked)

    async def _personal_where(
        self, *conditions: ColumnElement[bool], limit: int | None = None
    ) -> list[PersonalMemory]:
        async with postgres.session() as session:
            rows = await session.scalars(
                select(MemoryRow)
                .where(*conditions)
                .order_by(MemoryRow.created_at.desc())
                .limit(limit)
            )
            return [PersonalMemory.model_validate({"id": row.id, "body": row.body}) for row in rows]

    async def personal(
        self, person: UUID | None, *, curated_in: str | None = None
    ) -> list[PersonalMemory]:
        """``person``'s personal memories no trigger holds back, newest first; none for no
        person."""
        if person is None:
            return []
        return await self._personal_where(
            *self._applying(person, curated_in), ~self._triggered(), limit=_PERSONAL_LIMIT
        )

    async def triggers(
        self, person: UUID | None, *, curated_in: str | None = None
    ) -> list[MemoryTrigger]:
        """The triggers holding back ``person``'s personal memories, in place of them."""
        if person is None:
            return []
        async with postgres.session() as session:
            rows = await session.execute(
                select(MemoryTriggerRow.path, MemoryTriggerRow.summary)
                .join(MemoryTriggerLink, MemoryTriggerLink.trigger_id == MemoryTriggerRow.id)
                .join(MemoryRow, MemoryRow.id == MemoryTriggerLink.memory_id)
                .where(*self._applying(person, curated_in))
                .distinct()
                .order_by(MemoryTriggerRow.path, MemoryTriggerRow.summary)
            )
            return [MemoryTrigger(path=path, summary=summary) for path, summary in rows]

    async def recall(
        self,
        person: UUID,
        path: str,
        *,
        curated_in: str | None = None,
        include_private: bool = False,
    ) -> list[PersonalMemory]:
        """``person``'s personal memories held back by the trigger at ``path`` or any above
        it, newest first."""
        conditions = self._applying(person, curated_in)
        if not include_private:
            conditions.append(MemoryRow.from_private_thread.is_(False))
        return await self._personal_where(*conditions, self._triggered(path))

    async def withdraw(self, memory_id: UUID, person: UUID) -> Withdrawal | None:
        """Take back one of ``person``'s memories; None when they saved no such one.

        One the curator never saw, held from it, is simply deleted. Any other
        goes back to the curator, decided or not, since only it may change the workspace's
        prompt; a personal one stops applying to its person at once.
        """
        mine = (MemoryRow.id == memory_id, MemoryRow.created_by == person)
        async with postgres.session() as session:
            if await session.scalar(
                delete(MemoryRow).where(*mine, MemoryRow.status == "held").returning(MemoryRow.id)
            ):
                return "deleted"
            await session.execute(
                update(MemoryRow)
                .where(*mine, MemoryRow.withdrawal_requested.is_(False))
                .values(
                    status="pending",
                    withdrawal_requested=True,
                    updated_by=person,
                    updated_at=func.clock_timestamp(),
                )
            )
            withdrawn = await session.scalar(select(MemoryRow.id).where(*mine))
        return "requested" if withdrawn else None

    async def for_curator(self, workspace: str, status: Status) -> list[CuratedMemory]:
        async with postgres.session() as session:
            rows = await session.scalars(
                select(MemoryRow)
                .where(
                    MemoryRow.workspace_id == await self._workspace_id(session, workspace),
                    MemoryRow.status == status,
                )
                .order_by(MemoryRow.created_at)
                .limit(_CURATOR_PAGE)
            )
            memories = list(rows)
            links = await session.execute(
                select(MemoryTriggerLink.memory_id, MemoryTriggerRow.path)
                .join(MemoryTriggerRow, MemoryTriggerRow.id == MemoryTriggerLink.trigger_id)
                .where(MemoryTriggerLink.memory_id.in_([row.id for row in memories]))
                .order_by(MemoryTriggerRow.path)
            )
        triggers: dict[UUID, list[str]] = {}
        for memory_id, path in links:
            triggers.setdefault(memory_id, []).append(path)
        return [CuratedMemory.of(row, triggers.get(row.id, [])) for row in memories]

    async def triggers_for_curator(self, workspace: str) -> list[CuratedTrigger]:
        async with postgres.session() as session:
            rows = await session.execute(
                select(MemoryTriggerRow, MemoryTriggerLink.memory_id)
                .outerjoin(MemoryTriggerLink, MemoryTriggerLink.trigger_id == MemoryTriggerRow.id)
                .where(
                    MemoryTriggerRow.workspace_id == await self._workspace_id(session, workspace)
                )
                .order_by(MemoryTriggerRow.path, MemoryTriggerLink.created_at)
            )
            triggers: dict[str, CuratedTrigger] = {}
            for trigger, memory_id in rows:
                curated = triggers.setdefault(
                    trigger.path,
                    CuratedTrigger(
                        path=trigger.path,
                        summary=trigger.summary,
                        memory_ids=[],
                        guidance=trigger.guidance,
                    ),
                )
                if memory_id is not None:
                    curated.memory_ids.append(memory_id)
        return list(triggers.values())

    async def set_trigger(self, workspace: str, path: str, summary: str) -> None:
        """Name a trigger, or change when an existing one says its memories apply."""
        async with postgres.session() as session:
            upsert = insert(MemoryTriggerRow).values(
                id=uuid7(),
                workspace_id=await self._workspace_id(session, workspace),
                path=path,
                summary=summary,
            )
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[MemoryTriggerRow.workspace_id, MemoryTriggerRow.path],
                    set_={"summary": summary, "updated_at": func.clock_timestamp()},
                )
            )

    async def set_guidance(self, workspace: str, path: str, guidance: str) -> bool:
        """Write what every thread of the workspace recalls at the trigger at ``path``, or
        clear it with an empty ``guidance``; False when the workspace has no such trigger."""
        async with postgres.session() as session:
            written = await session.scalar(
                update(MemoryTriggerRow)
                .where(
                    MemoryTriggerRow.workspace_id == await self._workspace_id(session, workspace),
                    MemoryTriggerRow.path == path,
                )
                .values(guidance=guidance, updated_at=func.clock_timestamp())
                .returning(MemoryTriggerRow.id)
            )
        return written is not None

    async def shared_triggers(self, workspace: str) -> list[MemoryTrigger]:
        """The workspace's triggers that carry guidance, for every thread of it to list."""
        async with postgres.session() as session:
            rows = await session.execute(
                select(MemoryTriggerRow.path, MemoryTriggerRow.summary)
                .join(WorkspaceRow, WorkspaceRow.id == MemoryTriggerRow.workspace_id)
                .where(WorkspaceRow.slug == workspace, MemoryTriggerRow.guidance != "")
                .order_by(MemoryTriggerRow.path)
            )
            return [MemoryTrigger(path=path, summary=summary) for path, summary in rows]

    async def recall_guidance(self, workspace: str, path: str) -> list[TriggerGuidance]:
        """The workspace's guidance at ``path`` and every path above it, most general
        first."""
        async with postgres.session() as session:
            rows = await session.execute(
                select(MemoryTriggerRow.path, MemoryTriggerRow.guidance)
                .join(WorkspaceRow, WorkspaceRow.id == MemoryTriggerRow.workspace_id)
                .where(
                    WorkspaceRow.slug == workspace,
                    MemoryTriggerRow.path.in_(MemoryTrigger.lineage(path)),
                    MemoryTriggerRow.guidance != "",
                )
                .order_by(func.length(MemoryTriggerRow.path))
            )
            return [TriggerGuidance(path=path, guidance=text) for path, text in rows]

    async def delete_trigger(self, workspace: str, path: str) -> bool:
        """Drop a trigger; the memories it held back return to their people's context."""
        async with postgres.session() as session:
            deleted = await session.scalar(
                delete(MemoryTriggerRow)
                .where(
                    MemoryTriggerRow.workspace_id == await self._workspace_id(session, workspace),
                    MemoryTriggerRow.path == path,
                )
                .returning(MemoryTriggerRow.id)
            )
        return deleted is not None

    async def link_trigger(
        self, workspace: str, path: str, memory_id: UUID, *, linked: bool
    ) -> bool:
        """Link a personal memory the curator may decide to a trigger, or unlink any memory
        from it; False when either is not this workspace's."""
        async with postgres.session() as session:
            workspace_id = await self._workspace_id(session, workspace)
            trigger_id = await session.scalar(
                select(MemoryTriggerRow.id).where(
                    MemoryTriggerRow.workspace_id == workspace_id, MemoryTriggerRow.path == path
                )
            )
            conditions = [
                MemoryRow.id == memory_id,
                MemoryRow.workspace_id == workspace_id,
                MemoryRow.status != "held",
            ]
            if linked:
                # Only personal memories are recalled; workspace-scoped links made before
                # that rule may still be removed.
                conditions.append(MemoryRow.scope == "personal")
            memory = await session.scalar(select(MemoryRow.id).where(*conditions))
            if trigger_id is None or memory is None:
                return False
            if linked:
                await session.execute(
                    insert(MemoryTriggerLink)
                    .values(id=uuid7(), trigger_id=trigger_id, memory_id=memory_id)
                    .on_conflict_do_nothing(
                        index_elements=[MemoryTriggerLink.trigger_id, MemoryTriggerLink.memory_id]
                    )
                )
            else:
                await session.execute(
                    delete(MemoryTriggerLink).where(
                        MemoryTriggerLink.trigger_id == trigger_id,
                        MemoryTriggerLink.memory_id == memory_id,
                    )
                )
        return True

    async def pending_workspaces(self) -> list[str]:
        """The slugs of the workspaces with a memory their curator has not decided."""
        async with postgres.session() as session:
            rows = await session.scalars(
                select(WorkspaceRow.slug)
                .join(MemoryRow, MemoryRow.workspace_id == WorkspaceRow.id)
                .where(MemoryRow.status == "pending")
                .distinct()
            )
            return list(rows)

    async def decide(self, workspace: str, memory_id: UUID, decision: Decision, note: str) -> bool:
        """Record the curator's decision; False for a memory it may not decide."""
        async with postgres.session() as session:
            decided = await session.scalar(
                update(MemoryRow)
                .where(
                    MemoryRow.id == memory_id,
                    MemoryRow.workspace_id == await self._workspace_id(session, workspace),
                    MemoryRow.status != "held",
                )
                .values(status=decision, decision_note=note, updated_at=func.clock_timestamp())
                .returning(MemoryRow.id)
            )
        return decided is not None

    async def workspace_prompt(self, workspace: str) -> str:
        """The workspace's latest curated prompt; empty before its curator publishes one."""
        async with postgres.session() as session:
            prompt = await session.scalar(
                select(WorkspacePromptVersion.prompt)
                .join(WorkspaceRow, WorkspaceRow.id == WorkspacePromptVersion.workspace_id)
                .where(WorkspaceRow.slug == workspace)
                .order_by(WorkspacePromptVersion.created_at.desc())
                .limit(1)
            )
        return prompt or ""

    async def publish(self, workspace: str, prompt: str) -> WorkspacePromptVersion:
        async with postgres.session() as session:
            version = WorkspacePromptVersion(
                workspace_id=await self._workspace_id(session, workspace), prompt=prompt
            )
            session.add(version)
        return version


MEMORIES = Memories()
