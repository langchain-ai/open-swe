"""Projects: named folders of threads that people create and share.

A project belongs to a workspace, whose sandbox and settings its runs use, and
may be bound to one GitHub repository; a repository belongs to at most one
project. A thread is in a project through ``project_id`` in its metadata, or
through the project bound to its repository.

A project only groups threads: who may read a thread is decided by the thread
alone, so a member sees the project's threads they could already read.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, Literal
from uuid import UUID, uuid7

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import ForeignKey, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, selectinload

from openswe.dashboard.agent_overrides import resolve_github_login
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.repo_names import normalize_repo_full_name
from openswe.github.repositories import Repository
from openswe.threads.store import THREADS
from openswe.threads.summary import thread_is_owner
from openswe.users import User
from openswe.workspaces.rows import WorkspaceRow
from openswe.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES

logger = logging.getLogger(__name__)

ProjectRole = Literal["owner", "member"]

PROJECT_ID_KEY: Final = "project_id"
NAME_MAX_CHARS: Final = 120


class ProjectError(Exception):
    """A change the store refuses; ``status_code`` is how the dashboard API reports it."""

    status_code = 400


class ProjectNotFound(ProjectError):
    """No such project, or the person is not one of its members."""

    status_code = 404


class ProjectForbidden(ProjectError):
    """The person is a member, but the change needs an owner or access they lack."""

    status_code = 403


class ProjectConflict(ProjectError):
    """The change would break an invariant another row holds, such as a bound repository."""

    status_code = 409


class ProjectRow(Base):
    __tablename__ = "project"

    name: Mapped[str]
    workspace_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspace.id", ondelete="SET NULL"), default=None
    )
    repository_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("repository.id", ondelete="SET NULL"), default=None
    )
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)


class ProjectMemberRow(Base):
    __tablename__ = "project_member"

    project_id: Mapped[UUID] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str]
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)


class ProjectMember(BaseModel):
    user_id: UUID
    name: str
    login: str
    avatar_url: str
    role: ProjectRole


class Project(BaseModel):
    """A project as one of its members sees it."""

    id: UUID
    name: str
    workspace: str
    repo_full_name: str | None
    role: ProjectRole
    members: list[ProjectMember]
    created_at: datetime
    updated_at: datetime


class ProjectChanges(BaseModel):
    """What an owner may change; an omitted field stays as it is."""

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX_CHARS)
    workspace: str | None = None
    # ``""`` unbinds the repository.
    repo_full_name: str | None = None

    @field_validator("name")
    @classmethod
    def _stripped(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("name must not be blank")
        return value.strip()


@dataclass(frozen=True)
class ProjectFiling:
    """Which of one person's projects each thread is filed under, for their sidebar."""

    projects: Mapping[str, Project] = field(default_factory=dict)
    by_repo: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, projects: list[Project]) -> ProjectFiling:
        return cls(
            projects={str(project.id): project for project in projects},
            by_repo={
                project.repo_full_name.lower(): str(project.id)
                for project in projects
                if project.repo_full_name
            },
        )

    def project_for(self, metadata: Mapping[str, object], repo_full_name: str) -> str | None:
        """The project id the thread is filed under, or ``None`` outside the person's projects.

        A thread started in a project stays there; any other falls into the project
        bound to its repository.
        """
        project_id = metadata.get(PROJECT_ID_KEY)
        if isinstance(project_id, str) and project_id in self.projects:
            return project_id
        return self.by_repo.get(repo_full_name.lower()) if repo_full_name else None


NO_PROJECTS: Final = ProjectFiling()


class ProjectStore:
    """Every read and change of a project, each checked against the person it is for."""

    async def for_member(self, user: User) -> list[Project]:
        """Every project ``user`` belongs to, most recently changed first."""
        async with postgres.session() as session:
            project_ids = list(
                await session.scalars(
                    select(ProjectMemberRow.project_id).where(ProjectMemberRow.user_id == user.id)
                )
            )
            return await self._load(session, project_ids, user)

    async def filing_for(self, login: str) -> ProjectFiling:
        """How the person signed in as ``login`` files threads into their projects."""
        user = await User.for_login("github", login)
        return ProjectFiling.of(await self.for_member(user)) if user is not None else NO_PROJECTS

    async def member_for_run(self, config: Mapping[str, object]) -> User:
        """The person an agent tool acts for: whoever started the run."""
        login = await resolve_github_login(dict(config))
        user = await User.for_login("github", login) if login else None
        if user is None:
            raise ProjectNotFound("could not tell who started this run")
        return user

    async def get(self, project_id: UUID, user: User) -> Project:
        async with postgres.session() as session:
            loaded = await self._load(session, [project_id], user)
        if not loaded or not any(member.user_id == user.id for member in loaded[0].members):
            raise ProjectNotFound("project not found")
        return loaded[0]

    async def create(
        self,
        user: User,
        name: str,
        *,
        workspace: str | None = None,
        repo_full_name: str | None = None,
    ) -> Project:
        """A new project with ``user`` as its owner."""
        changes = ProjectChanges(name=name, workspace=workspace, repo_full_name=repo_full_name)
        if changes.name is None:
            raise ProjectError("name is required")
        project = ProjectRow(name=changes.name, created_by=user.id, updated_by=user.id)
        async with postgres.session() as session:
            await self._apply(session, project, changes, user)
            session.add(project)
            await self._flush(session)
            session.add(
                ProjectMemberRow(
                    project_id=project.id,
                    user_id=user.id,
                    role="owner",
                    created_by=user.id,
                    updated_by=user.id,
                )
            )
            await self._flush(session)
        return await self.get(project.id, user)

    async def update(self, project_id: UUID, user: User, changes: ProjectChanges) -> Project:
        async with postgres.session() as session:
            await self._require_owner(session, project_id, user)
            project = await session.get_one(ProjectRow, project_id)
            await self._apply(session, project, changes, user)
            project.updated_by = user.id
            project.updated_at = datetime.now(UTC)
            await self._flush(session)
        return await self.get(project_id, user)

    async def delete(self, project_id: UUID, user: User) -> None:
        """Delete the project; its threads keep an id that no longer files them anywhere."""
        async with postgres.session() as session:
            await self._require_owner(session, project_id, user)
            await session.execute(delete(ProjectRow).where(ProjectRow.id == project_id))

    async def set_member(
        self, project_id: UUID, user: User, member: User, role: ProjectRole
    ) -> Project:
        """Add ``member`` to the project, or change their role."""
        async with postgres.session() as session:
            await self._require_owner(session, project_id, user)
            existing = await session.scalar(
                select(ProjectMemberRow).where(
                    ProjectMemberRow.project_id == project_id,
                    ProjectMemberRow.user_id == member.id,
                )
            )
            if existing is None:
                session.add(
                    ProjectMemberRow(
                        project_id=project_id,
                        user_id=member.id,
                        role=role,
                        created_by=user.id,
                        updated_by=user.id,
                    )
                )
            elif existing.role != role:
                if existing.role == "owner":
                    await self._keep_an_owner(session, project_id, member)
                existing.role = role
                existing.updated_by = user.id
                existing.updated_at = datetime.now(UTC)
            await self._flush(session)
        return await self.get(project_id, user)

    async def remove_member(self, project_id: UUID, user: User, member: User) -> None:
        """Take ``member`` out of the project: an owner may remove anyone, anyone may leave."""
        async with postgres.session() as session:
            if member.id == user.id:
                await self._require_role(session, project_id, user)
            else:
                await self._require_owner(session, project_id, user)
            await self._keep_an_owner(session, project_id, member)
            await session.execute(
                delete(ProjectMemberRow).where(
                    ProjectMemberRow.project_id == project_id,
                    ProjectMemberRow.user_id == member.id,
                )
            )

    async def file_thread(self, thread_id: str, user: User, project_id: UUID | None) -> None:
        """Move the person's own thread into one of their projects, or out with ``None``."""
        thread = await THREADS.get(thread_id)
        if thread is None or not thread_is_owner(thread.metadata, user.session_login):
            raise ProjectNotFound("thread not found")
        if project_id is not None:
            await self.get(project_id, user)
        await THREADS.update(
            thread_id, {PROJECT_ID_KEY: str(project_id) if project_id is not None else None}
        )

    async def for_run(self, project_id: str, user: User | None) -> Project:
        """The project a run is started in, which its starter must belong to."""
        try:
            parsed = UUID(project_id)
        except ValueError as exc:
            raise ProjectNotFound("project not found") from exc
        if user is None:
            raise ProjectNotFound("project not found")
        return await self.get(parsed, user)

    async def _apply(
        self, session: AsyncSession, project: ProjectRow, changes: ProjectChanges, user: User
    ) -> None:
        if changes.name is not None:
            project.name = changes.name
        if changes.workspace is not None:
            workspace_id = await WORKSPACES.id_for_slug(changes.workspace)
            if workspace_id is None:
                raise ProjectError(f"no workspace named {changes.workspace!r}")
            project.workspace_id = workspace_id
        if changes.repo_full_name == "":
            project.repository_id = None
        elif changes.repo_full_name is not None:
            full_name = normalize_repo_full_name(changes.repo_full_name)
            if not user.github_login:
                raise ProjectForbidden("Link GitHub to bind a repository to a project")
            try:
                await require_repo_access_for_user(user.github_login, full_name)
            except HTTPException as exc:
                raise ProjectForbidden(f"no access to {full_name}") from exc
            project.repository_id = await Repository.ensure_id(session, full_name)

    async def _flush(self, session: AsyncSession) -> None:
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ProjectConflict("that repository already belongs to another project") from exc

    async def _require_role(
        self, session: AsyncSession, project_id: UUID, user: User
    ) -> ProjectRole:
        role = await session.scalar(
            select(ProjectMemberRow.role).where(
                ProjectMemberRow.project_id == project_id, ProjectMemberRow.user_id == user.id
            )
        )
        if role not in ("owner", "member"):
            raise ProjectNotFound("project not found")
        return role

    async def _require_owner(self, session: AsyncSession, project_id: UUID, user: User) -> None:
        if await self._require_role(session, project_id, user) != "owner":
            raise ProjectForbidden("only a project owner can do that")

    async def _keep_an_owner(self, session: AsyncSession, project_id: UUID, leaving: User) -> None:
        # Locking the project serializes owner changes, so two owners can't both step down.
        await session.execute(
            select(ProjectRow.id).where(ProjectRow.id == project_id).with_for_update()
        )
        owners = set(
            await session.scalars(
                select(ProjectMemberRow.user_id).where(
                    ProjectMemberRow.project_id == project_id, ProjectMemberRow.role == "owner"
                )
            )
        )
        if owners <= {leaving.id}:
            raise ProjectConflict("a project needs an owner: add another owner or delete it")

    async def _load(
        self, session: AsyncSession, project_ids: list[UUID], viewer: User
    ) -> list[Project]:
        if not project_ids:
            return []
        rows = (
            await session.execute(
                select(ProjectRow, WorkspaceRow.slug, Repository.full_name)
                .outerjoin(WorkspaceRow, WorkspaceRow.id == ProjectRow.workspace_id)
                .outerjoin(Repository, Repository.id == ProjectRow.repository_id)
                .where(ProjectRow.id.in_(project_ids))
                .order_by(ProjectRow.updated_at.desc())
            )
        ).tuples()
        memberships = (
            await session.execute(
                select(ProjectMemberRow, User)
                .join(User, User.id == ProjectMemberRow.user_id)
                .options(selectinload(User.identities))
                .where(ProjectMemberRow.project_id.in_(project_ids))
                .order_by(ProjectMemberRow.created_at)
            )
        ).tuples()
        members: dict[UUID, list[ProjectMember]] = {}
        for membership, person in memberships:
            members.setdefault(membership.project_id, []).append(
                ProjectMember(
                    user_id=person.id,
                    name=person.display_name or person.login_for("github"),
                    login=person.github_login,
                    avatar_url=person.avatar_url,
                    role="owner" if membership.role == "owner" else "member",
                )
            )
        projects: list[Project] = []
        for row, workspace, repo_full_name in rows:
            project_members = members.get(row.id, [])
            role = next(
                (member.role for member in project_members if member.user_id == viewer.id),
                "member",
            )
            projects.append(
                Project(
                    id=row.id,
                    name=row.name,
                    workspace=workspace or DEFAULT_WORKSPACE_SLUG,
                    repo_full_name=repo_full_name,
                    role=role,
                    members=project_members,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
            )
        return projects


PROJECTS: Final = ProjectStore()
