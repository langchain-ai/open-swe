"""What threads remember, and the prompt each workspace's curator distils from it.

Every memory waits for the curator of the workspace it was saved in, which may restate
what many people's threads learned in the workspace's prompt. A personal memory also
applies to its person's runs at once, until a line there supersedes it. Memories
saved in private threads before every memory reached the curator stay held from it.
"""

import hashlib
import hmac
from datetime import datetime
from typing import Final, Literal, Self
from uuid import UUID, uuid7

from pydantic import BaseModel
from sqlalchemy import ForeignKey, delete, func, or_, select, update
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

    @classmethod
    def of(cls, row: MemoryRow) -> Self:
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
            }
        )


class UnknownWorkspaceError(LookupError):
    """No workspace is stored under the slug a memory names."""


class Memories:
    """Every ``memory`` row and every ``workspace_prompt_version``, by workspace slug."""

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

    async def personal(
        self, person: UUID | None, *, curated_in: str | None = None
    ) -> list[PersonalMemory]:
        """``person``'s personal memories, newest first; none for no person.

        With ``curated_in``, the workspace whose curated prompt the run reads, leaving out
        the memories its curator superseded: that prompt carries them now. Without it, as for
        a person who does not read that prompt, they keep applying rather than disappear.
        """
        if person is None or not postgres.configured():
            return []
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
        async with postgres.session() as session:
            rows = await session.scalars(
                select(MemoryRow)
                .where(*conditions)
                .order_by(MemoryRow.created_at.desc())
                .limit(_PERSONAL_LIMIT)
            )
            return [PersonalMemory.model_validate({"id": row.id, "body": row.body}) for row in rows]

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
            return [CuratedMemory.of(row) for row in rows]

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
        if not postgres.configured():
            return ""
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
