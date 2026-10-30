"""What threads remember, and the prompt each workspace's curator distils from it.

A personal memory applies to the runs of the person who saved it at once. A memory
saved in a shared thread also waits for the curator of the workspace it was saved in,
which may fold what many people's threads learned into the workspace's prompt. One
saved in a private thread is held: the curator never reads it.
"""

from datetime import datetime
from typing import Final, Literal, Self
from uuid import UUID, uuid7

from pydantic import BaseModel
from sqlalchemy import ForeignKey, and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, selectinload

from openswe.dashboard.admin import is_admin
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.users.models import User
from openswe.workspaces.rows import WorkspaceRow

Scope = Literal["personal", "workspace"]
Status = Literal["pending", "held", "adopted", "declined"]
Decision = Literal["adopted", "declined"]
# The statuses the curator lists: it never sees a held memory.
Decided = Literal["pending", "adopted", "declined"]
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
    """A memory that reaches its person's runs: a personal one, or a workspace one they
    saved that the curator has not processed yet."""

    id: UUID
    scope: Scope
    body: str

    @property
    def line(self) -> str:
        """The memory as its person's block lists it, with the id and scope that forget it."""
        if self.scope == "workspace":
            return f"{self.body} (workspace memory {self.id}, awaiting the curator)"
        return f"{self.body} (memory {self.id})"


class Author(BaseModel):
    """Who saved a memory in a shared thread, which everyone in it could already see."""

    full_name: str
    github_login: str
    github_user_id: str
    slack_user_id: str
    # A configured admin, as a thread's ``person`` block calls them.
    admin: bool

    @classmethod
    def of(cls, user: User) -> Self:
        return cls(
            full_name=user.display_name,
            github_login=user.github_login,
            github_user_id=user.github_user_id,
            slack_user_id=user.slack_user_id,
            admin=is_admin(user.email, login=user.github_login or None),
        )


class CuratedMemory(BaseModel):
    """A memory as the curator sees it: only ever one saved in a shared thread."""

    id: UUID
    scope: Scope
    status: Status
    saved_at: datetime
    # None once its author's account is gone, or for a run no person started.
    author: Author | None
    body: str
    # Where it was saved, for the curator to read as evidence.
    thread_id: str | None
    # Its author asked to take it back, even after it was adopted.
    withdrawal_requested: bool

    @classmethod
    def of(cls, row: MemoryRow, author: User | None) -> Self:
        return cls.model_validate(
            {
                "id": row.id,
                "scope": row.scope,
                "status": row.status,
                "saved_at": row.created_at,
                "author": None if author is None else Author.of(author),
                "body": row.body,
                "thread_id": row.thread_id,
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
        """Keep a memory; one from a private thread is held, so the curator never reads it.

        A personal memory keeps the workspace it was saved in when there is one; a
        workspace memory needs it.
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
                status="held" if from_private_thread else "pending",
                created_by=person,
                updated_by=person,
            )
            session.add(row)
        return row

    async def personal(
        self, person: UUID | None, *, unprocessed_in: str | None = None
    ) -> list[PersonalMemory]:
        """``person``'s personal memories, newest first; none for no person.

        With ``unprocessed_in``, also the workspace memories they saved for that workspace
        that the curator has not adopted or declined yet: until it does, they apply to their
        person as if personal. Only those saved in the person's own private thread, which
        passed the bar a personal memory must: one from a shared thread may carry text
        someone else put there, and only the curator may pass that on.
        """
        if person is None or not postgres.configured():
            return []
        scopes = MemoryRow.scope == "personal"
        if unprocessed_in is not None:
            scopes = or_(
                scopes,
                and_(
                    MemoryRow.scope == "workspace",
                    MemoryRow.status.in_(("pending", "held")),
                    MemoryRow.from_private_thread.is_(True),
                    MemoryRow.workspace_id
                    == select(WorkspaceRow.id)
                    .where(WorkspaceRow.slug == unprocessed_in)
                    .scalar_subquery(),
                ),
            )
        async with postgres.session() as session:
            rows = await session.scalars(
                select(MemoryRow)
                .where(
                    scopes,
                    MemoryRow.created_by == person,
                    MemoryRow.withdrawal_requested.is_(False),
                )
                .order_by(MemoryRow.created_at.desc())
                .limit(_PERSONAL_LIMIT)
            )
            return [
                PersonalMemory.model_validate({"id": row.id, "scope": row.scope, "body": row.body})
                for row in rows
            ]

    async def withdraw(self, memory_id: UUID, person: UUID, scope: Scope) -> Withdrawal | None:
        """Take back one of ``person``'s memories in ``scope``; None when they saved no such one.

        One the curator never saw, held from a private thread, is simply deleted. Any other
        goes back to the curator, adopted or not, since only it may change the workspace's
        prompt; a personal one stops applying to its person at once.
        """
        mine = (
            MemoryRow.id == memory_id,
            MemoryRow.scope == scope,
            MemoryRow.created_by == person,
        )
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

    async def for_curator(self, workspace: str, status: Decided) -> list[CuratedMemory]:
        """The workspace's memories from shared threads with ``status``, oldest first, each
        with its author."""
        async with postgres.session() as session:
            rows = list(
                await session.scalars(
                    select(MemoryRow)
                    .where(
                        MemoryRow.workspace_id == await self._workspace_id(session, workspace),
                        MemoryRow.from_private_thread.is_(False),
                        MemoryRow.status == status,
                    )
                    .order_by(MemoryRow.created_at)
                    .limit(_CURATOR_PAGE)
                )
            )
            authors = {
                user.id: user
                for user in await session.scalars(
                    select(User)
                    .options(selectinload(User.identities))
                    .where(User.id.in_({row.created_by for row in rows if row.created_by}))
                )
            }
            return [
                CuratedMemory.of(row, authors.get(row.created_by) if row.created_by else None)
                for row in rows
            ]

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
                    MemoryRow.from_private_thread.is_(False),
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
