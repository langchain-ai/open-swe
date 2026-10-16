"""Persistence for the plan-review feature.

The plan lives in two places:
  - the agent's sandbox, as a self-contained HTML file the agent creates and edits, and
  - PostgreSQL, as the published snapshot the dashboard renders (``thread_plan``).

Reviewers leave whole-document comments, one ``thread_plan_comment`` row each.
"""

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from langgraph_sdk import get_client
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)


class ThreadPlan(Base):
    __tablename__ = "thread_plan"

    thread_id: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[JsonObject] = mapped_column(JSONB)
    dismissed: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)


class PlanComment(Base):
    __tablename__ = "thread_plan_comment"

    thread_id: Mapped[str] = mapped_column(primary_key=True)
    id: Mapped[str] = mapped_column(primary_key=True)
    author: Mapped[str]
    author_login: Mapped[str]
    body: Mapped[str]
    anchor: Mapped[JsonObject | None] = mapped_column(JSONB)
    created_at: Mapped[datetime]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "author": self.author,
            "author_login": self.author_login,
            "body": self.body,
            "anchor": self.anchor,
            "created_at": self.created_at.astimezone(UTC).isoformat(),
        }


# Plans are mirrored into the sandbox outside cloned repositories.
PLAN_FILE_DIRECTORY = "/workspace/plans"

PLAN_STATUS_SHARED = "shared"


def make_plan_approver(*, actor_id: str, name: str, source: str) -> dict[str, str]:
    actor_id = actor_id.strip()
    name = name.strip()
    source = source.strip()
    return {
        "id": actor_id or name or "unknown",
        "name": name or actor_id or "Unknown user",
        "source": source or "unknown",
    }


def plan_file_path_for_thread(thread_id: str) -> str:
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", thread_id).strip("-").lower()[:48]
    return f"{PLAN_FILE_DIRECTORY}/{date}-{slug or 'plan'}.html"


async def _stored_plan_file_path(thread_id: str) -> str | None:
    value = await get_plan_content(thread_id) or {}
    path = value.get("plan_file_path")
    return path if isinstance(path, str) and path else None


async def save_plan_content(
    thread_id: str,
    *,
    html: str | None = None,
    markdown: str | None = None,
    status: str = PLAN_STATUS_SHARED,
    clear_comments: bool = True,
    plan_file_path: str | None = None,
) -> None:
    """Publish an artifact, clearing old comments unless this is a manual edit."""
    if plan_file_path is None:
        plan_file_path = await _stored_plan_file_path(thread_id)
    record: dict[str, Any] = {"status": status, "revision": str(uuid.uuid4())}
    if html is not None:
        record["html"] = html
    if markdown is not None:
        record["markdown"] = markdown
    if plan_file_path:
        record["plan_file_path"] = plan_file_path
    async with postgres.session() as session:
        await session.execute(
            insert(ThreadPlan)
            .values(thread_id=thread_id, value=record)
            .on_conflict_do_update(
                index_elements=[ThreadPlan.thread_id],
                set_={"value": record, "dismissed": False, "updated_at": NOW},
            )
        )
        if clear_comments:
            await session.execute(delete(PlanComment).where(PlanComment.thread_id == thread_id))
    metadata = {"plan_status": status, "plan_approved_by": None, "plan_approved_at": None}
    await _merge_thread_metadata(thread_id, metadata)


async def dismiss_plan(thread_id: str, revision: str | None) -> None:
    """Hide this revision's inline preview until a new revision is published."""
    async with postgres.session() as session:
        await session.execute(
            update(ThreadPlan)
            .where(
                ThreadPlan.thread_id == thread_id,
                ThreadPlan.value["revision"].astext.is_not_distinct_from(revision),
            )
            .values(dismissed=True)
        )


async def plan_is_dismissed(thread_id: str) -> bool:
    try:
        async with postgres.session() as session:
            dismissed = await session.scalar(
                select(ThreadPlan.dismissed).where(ThreadPlan.thread_id == thread_id)
            )
    except Exception:
        logger.warning(
            "Could not read artifact dismissal", extra={"agent_thread_id": thread_id}, exc_info=True
        )
        return False
    return bool(dismissed)


async def write_plan_to_sandbox(
    thread_id: str, content: str, *, plan_file_path: str | None = None
) -> str:
    """Mirror the dashboard plan edit into the thread's sandbox.

    Best-effort: a missing sandbox must not block publishing the plan to the
    review page.
    """
    path = plan_file_path or plan_file_path_for_thread(thread_id)
    try:
        from openswe.sandboxes.state import get_sandbox_backend

        backend = await get_sandbox_backend(thread_id)
        await backend.awrite(path, content)
        return path
    except Exception:
        logger.warning("Could not write plan file to sandbox for %s", thread_id, exc_info=True)
        return path


async def get_plan_content(
    thread_id: str, *, raise_on_error: bool = False
) -> dict[str, Any] | None:
    """Read the published artifact, optionally propagating database failures."""
    try:
        async with postgres.session() as session:
            return await session.scalar(
                select(ThreadPlan.value).where(ThreadPlan.thread_id == thread_id)
            )
    except Exception:
        if raise_on_error:
            raise
        logger.warning("plan content lookup failed for %s", thread_id, exc_info=True)
        return None


async def list_plan_comments(
    thread_id: str, *, raise_on_error: bool = False
) -> list[dict[str, Any]]:
    """Read comments oldest first, optionally propagating database failures."""
    try:
        async with postgres.session() as session:
            comments = await session.scalars(
                select(PlanComment)
                .where(PlanComment.thread_id == thread_id)
                .order_by(PlanComment.created_at, PlanComment.id)
            )
            return [comment.to_dict() for comment in comments]
    except Exception:
        if raise_on_error:
            raise
        logger.warning("plan comment lookup failed for %s", thread_id, exc_info=True)
        return []


async def add_plan_comment(
    thread_id: str,
    *,
    author: str,
    author_login: str,
    body: str,
    anchor: dict[str, Any] | None,
) -> dict[str, Any]:
    """Append an anchored comment; returns the stored comment."""
    comment = PlanComment(
        thread_id=thread_id,
        id=uuid.uuid4().hex,
        author=author,
        author_login=author_login,
        body=body,
        anchor=anchor,
        created_at=datetime.now(UTC),
    )
    async with postgres.session() as session:
        session.add(comment)
    return comment.to_dict()


async def delete_plan_comment(thread_id: str, comment_id: str) -> None:
    async with postgres.session() as session:
        await session.execute(
            delete(PlanComment).where(
                PlanComment.thread_id == thread_id, PlanComment.id == comment_id
            )
        )


async def _merge_thread_metadata(thread_id: str, metadata: dict[str, Any]) -> None:
    try:
        await get_client().threads.update(thread_id=thread_id, metadata=metadata)
    except Exception:
        logger.warning(
            "Could not update artifact metadata",
            extra={"agent_thread_id": thread_id},
            exc_info=True,
        )
