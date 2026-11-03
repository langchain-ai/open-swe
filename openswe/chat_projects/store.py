"""PostgreSQL-backed, owner-scoped projects and personal thread memberships."""

import logging
from collections.abc import Mapping
from uuid import UUID, uuid7

from fastapi import HTTPException
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import bindparam, text

from openswe.chat_projects.models import (
    ChatProject,
    ProjectBody,
    ProjectCollection,
    ProjectThreadsPage,
)
from openswe.dashboard.agent_overrides import resolve_github_login
from openswe.dashboard.principal import DashboardPrincipal
from openswe.database.postgres import transaction
from openswe.run_config import RunConfig
from openswe.thread_ids import mda_successor_thread_id
from openswe.threads import index
from openswe.threads.identity import ThreadIdentity
from openswe.threads.index import IndexedThread
from openswe.threads.runtimes import RUNTIMES
from openswe.threads.status import WorkRunState
from openswe.threads.store import THREADS
from openswe.threads.summary import (
    assert_thread_readable,
    thread_is_readable,
    thread_is_resolved,
    thread_is_unlisted,
    thread_summary,
)
from openswe.ui_invalidations import Topic
from openswe.utils.json_types import as_json_object

logger = logging.getLogger(__name__)
_PROJECT_COLUMNS = "id, name, repo, local_path, device_id"
_PAGE = TypeAdapter(ProjectThreadsPage)
_SUMMARY = TypeAdapter(dict[str, JsonValue])


class ChatProjects:
    def __init__(self, user_id: UUID, subject: str, email: str | None = None) -> None:
        self.user_id = user_id
        self.subject = subject
        self.email = email

    @classmethod
    async def for_session(cls, session: Mapping[str, object]) -> ChatProjects:
        principal = DashboardPrincipal.from_session(session)
        user = await principal.user()
        if user is None:
            raise HTTPException(401, "Sign in again to use chat projects")
        email = session.get("email")
        return cls(user.id, principal.subject, email if isinstance(email, str) else None)

    @classmethod
    async def for_config(cls, config: Mapping[str, object]) -> ChatProjects:
        cfg = RunConfig.from_config(config)
        subject = cfg.mcp_caller or await resolve_github_login(as_json_object(config))
        if not subject:
            raise HTTPException(401, "Could not resolve the project owner's identity")
        user = await ThreadIdentity(subject).user()
        if user is None:
            raise HTTPException(401, "Project owner account not found")
        return cls(user.id, subject, cfg.user_email)

    async def get(self, project_id: UUID) -> ChatProject:
        async with transaction() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            f"SELECT {_PROJECT_COLUMNS} FROM chat_project "
                            "WHERE id = :id AND user_id = :user_id"
                        ),
                        {"id": project_id, "user_id": self.user_id},
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise HTTPException(404, "project not found")
        return ChatProject.from_row(row)

    async def collection(self) -> ProjectCollection:
        async with transaction() as conn:
            rows = (
                (
                    await conn.execute(
                        text(
                            f"SELECT {_PROJECT_COLUMNS} FROM chat_project "
                            "WHERE user_id = :user_id ORDER BY created_at, id"
                        ),
                        {"user_id": self.user_id},
                    )
                )
                .mappings()
                .all()
            )
        memberships = await self.memberships()
        return ProjectCollection(
            projects=[ChatProject.from_row(row) for row in rows],
            memberships={thread.thread_id: str(project_id) for thread, project_id in memberships},
        )

    async def create(self, body: ProjectBody) -> ChatProject:
        async with transaction() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "INSERT INTO chat_project "
                            "(id, user_id, name, repo, local_path, device_id, created_by, updated_by) "
                            "VALUES (:id, :user_id, :name, :repo, :local_path, :device_id, "
                            f":user_id, :user_id) RETURNING {_PROJECT_COLUMNS}"
                        ),
                        {"id": uuid7(), "user_id": self.user_id, **body.context_columns()},
                    )
                )
                .mappings()
                .one()
            )
            await Topic.CHAT_PROJECTS.invalidate(conn, key=str(self.user_id))
        return ChatProject.from_row(row)

    async def update(self, project_id: UUID, body: ProjectBody) -> ChatProject:
        async with transaction() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "UPDATE chat_project SET name = :name, repo = :repo, "
                            "local_path = :local_path, device_id = :device_id, "
                            "updated_at = clock_timestamp(), updated_by = :user_id "
                            "WHERE id = :id AND user_id = :user_id "
                            f"RETURNING {_PROJECT_COLUMNS}"
                        ),
                        {"id": project_id, "user_id": self.user_id, **body.context_columns()},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise HTTPException(404, "project not found")
            await Topic.CHAT_PROJECTS.invalidate(conn, key=str(self.user_id))
        return ChatProject.from_row(row)

    async def delete(self, project_id: UUID) -> None:
        async with transaction() as conn:
            deleted = await conn.execute(
                text("DELETE FROM chat_project WHERE id = :id AND user_id = :user_id RETURNING id"),
                {"id": project_id, "user_id": self.user_id},
            )
            if deleted.first() is None:
                raise HTTPException(404, "project not found")
            await Topic.CHAT_PROJECTS.invalidate(conn, key=str(self.user_id))

    async def assign(self, thread_id: str, project_id: UUID | None) -> None:
        current_id = await index.current_thread_id(thread_id)
        thread = await THREADS.get(current_id)
        if thread is None:
            raise HTTPException(404, "thread not found")
        assert_thread_readable(thread.metadata, self.subject, self.email)
        async with transaction() as conn:
            if project_id is not None:
                project = await conn.execute(
                    text(
                        "SELECT id FROM chat_project WHERE id = :project_id "
                        "AND user_id = :user_id FOR KEY SHARE"
                    ),
                    {"project_id": project_id, "user_id": self.user_id},
                )
                if project.first() is None:
                    raise HTTPException(404, "project not found")
            predecessors = (
                (
                    await conn.execute(
                        text(
                            "SELECT m.thread_id FROM chat_project_membership m "
                            "JOIN thread_index t ON t.thread_id = m.thread_id "
                            "WHERE m.user_id = :user_id AND t.runtime = 'local'"
                        ),
                        {"user_id": self.user_id},
                    )
                )
                .scalars()
                .all()
            )
            removed = {
                str(predecessor)
                for predecessor in predecessors
                if mda_successor_thread_id(str(predecessor)) == current_id
            }
            if thread_id != current_id:
                removed.add(thread_id)
            if project_id is None:
                removed.add(current_id)
            if removed:
                await conn.execute(
                    text(
                        "DELETE FROM chat_project_membership WHERE user_id = :user_id "
                        "AND thread_id IN :removed"
                    ).bindparams(bindparam("removed", expanding=True)),
                    {"user_id": self.user_id, "removed": list(removed)},
                )
            if project_id is not None:
                await conn.execute(
                    text(
                        "INSERT INTO chat_project_membership "
                        "(id, user_id, thread_id, project_id, created_by, updated_by) "
                        "VALUES (:id, :user_id, :thread_id, :project_id, :user_id, :user_id) "
                        "ON CONFLICT (user_id, thread_id) DO UPDATE SET "
                        "project_id = EXCLUDED.project_id, updated_by = EXCLUDED.updated_by, "
                        "updated_at = clock_timestamp()"
                    ),
                    {
                        "id": uuid7(),
                        "user_id": self.user_id,
                        "thread_id": current_id,
                        "project_id": project_id,
                    },
                )
            await Topic.CHAT_PROJECTS.invalidate(conn, key=str(self.user_id))

    async def memberships(self, project_id: UUID | None = None) -> list[tuple[IndexedThread, UUID]]:
        async with transaction() as conn:
            rows = (
                (
                    await conn.execute(
                        text(
                            "SELECT t.thread_id, t.runtime, t.metadata, t.status, "
                            "t.created_at, t.updated_at, m.project_id "
                            "FROM chat_project_membership m "
                            "JOIN thread_index t ON t.thread_id = m.thread_id "
                            "WHERE m.user_id = :user_id "
                            "ORDER BY m.updated_at DESC, m.id DESC"
                        ),
                        {"user_id": self.user_id, "project_id": project_id},
                    )
                )
                .mappings()
                .all()
            )
        seen: set[str] = set()
        result: list[tuple[IndexedThread, UUID]] = []
        for row in rows:
            thread = IndexedThread.from_row(row)
            if thread.runtime == "local":
                current_id = await index.current_thread_id(thread.thread_id)
                if current_id != thread.thread_id:
                    current = await THREADS.get(current_id)
                    if current is not None:
                        thread = current
            if thread.thread_id in seen:
                continue
            seen.add(thread.thread_id)
            member_project_id = UUID(str(row["project_id"]))
            if (project_id is None or member_project_id == project_id) and thread_is_readable(
                thread.metadata, self.subject, self.email
            ):
                result.append((thread, member_project_id))
        return result

    async def summarize(self, thread: IndexedThread) -> dict[str, JsonValue]:
        latest_run_status = latest_run_id = None
        try:
            if (
                thread.metadata.get("transcript") == "v2"
                or thread.metadata.get("experience") == "agent_workspace"
            ):
                thread = await WorkRunState(thread).refresh()
            elif thread.status == "busy" or thread.metadata.get("latest_run_status") in (
                None,
                "pending",
                "running",
            ):
                run = await RUNTIMES.runs_on(thread.runtime).latest_run(thread.thread_id)
                if run is not None:
                    latest_run_status, latest_run_id = run.status, run.run_id
        except Exception:
            logger.warning(
                "Could not refresh project thread run",
                extra={"thread_id": thread.thread_id},
                exc_info=True,
            )
        return _SUMMARY.validate_python(
            await thread_summary(
                thread.as_langgraph(),
                latest_run_status=latest_run_status,
                latest_run_id=latest_run_id,
                bridge_status=False,
            )
        )

    def activity_ms(self, thread: IndexedThread) -> float:
        value = thread.metadata.get("updated_at_ms")
        return (
            float(value)
            if isinstance(value, (int, float))
            else thread.updated_at.timestamp() * 1000
        )

    async def threads(
        self, project_id: UUID, *, limit: int = 25, offset: int = 0, resolved: bool | None = False
    ) -> ProjectThreadsPage:
        await self.get(project_id)
        candidates = [
            thread
            for thread, _ in await self.memberships(project_id)
            if not thread_is_unlisted(thread.metadata)
            and (resolved is None or thread_is_resolved(thread.metadata) is resolved)
            and not thread.metadata.get("succeeded_by")
        ]
        candidates.sort(
            key=lambda thread: (self.activity_ms(thread), thread.thread_id), reverse=True
        )
        limit, offset = min(max(limit, 1), 100), max(offset, 0)
        items = [await self.summarize(thread) for thread in candidates[offset : offset + limit]]
        return _PAGE.validate_python(
            {
                "items": items,
                "limit": limit,
                "offset": offset,
                "hasMore": len(candidates) > offset + limit,
            }
        )
