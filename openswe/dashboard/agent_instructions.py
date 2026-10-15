"""Per-repository custom instructions for the main coding agent.

Each record holds a user-authored instruction prompt (edited in the dashboard)
that is appended to the main agent's system prompt for runs targeting that repo.
Records live in PostgreSQL, one per ``repository`` row, so a repository's
instructions apply whatever casing a run names it with.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import ForeignKey, Select, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP, filter_repo_models_for_user
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.repo_names import normalize_repo_full_name
from openswe.github.repositories import Repository
from openswe.users.records import github_login_of, user_id_for_login


class AgentInstructionsCreate(BaseModel):
    full_name: str = Field(..., description="GitHub repo in owner/name form")

    @field_validator("full_name", mode="before")
    @classmethod
    def _valid_full_name(cls, v: str) -> str:
        return normalize_repo_full_name(v)


class AgentInstructionsUpdate(BaseModel):
    instructions: str = Field(default="")


class AgentInstructions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    full_name: str
    owner: str = ""
    name: str = ""
    instructions: str = ""
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(
        cls,
        full_name: str,
        instructions: str,
        created_by: str | None,
        created_at: datetime | None,
        updated_at: datetime | None,
    ) -> AgentInstructions:
        owner, _, name = full_name.partition("/")
        return cls(
            full_name=full_name,
            owner=owner,
            name=name,
            instructions=instructions,
            created_by=created_by or "",
            created_at=created_at.isoformat() if created_at else "",
            updated_at=updated_at.isoformat() if updated_at else "",
        )


class AgentInstructionsRow(Base):
    __tablename__ = "agent_instructions"

    repository_id: Mapped[UUID] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), primary_key=True
    )
    instructions: Mapped[str] = mapped_column(server_default="", default="")
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @classmethod
    def records(cls) -> Select[str, str, str, datetime | None, datetime | None]:
        """Each row with its repository's name and its creator's GitHub login."""
        return select(
            Repository.full_name,
            cls.instructions,
            github_login_of(cls.created_by).correlate(cls),
            cls.created_at,
            cls.updated_at,
        ).join(Repository, Repository.id == cls.repository_id)


class AgentInstructionsStore:
    """Every repository's agent instructions, keyed case-insensitively by ``owner/name``."""

    async def get(self, full_name: str) -> AgentInstructions | None:
        async with postgres.session() as session:
            row = (
                await session.execute(
                    AgentInstructionsRow.records().where(Repository.key == full_name.lower())
                )
            ).first()
        return AgentInstructions.from_row(*row) if row else None

    async def list_all(self) -> list[AgentInstructions]:
        async with postgres.session() as session:
            rows = (
                await session.execute(AgentInstructionsRow.records().order_by(Repository.full_name))
            ).all()
        return [AgentInstructions.from_row(*row) for row in rows]

    async def create(self, full_name: str, created_by: str) -> AgentInstructions:
        """Start empty instructions for a repository, keeping any it already has."""
        async with postgres.session() as session:
            await session.execute(
                insert(AgentInstructionsRow)
                .values(
                    repository_id=await Repository.ensure_id(session, full_name),
                    created_by=user_id_for_login(created_by) if created_by else None,
                )
                .on_conflict_do_nothing()
            )
        return await self._stored(full_name)

    async def set_instructions(self, full_name: str, instructions: str) -> AgentInstructions:
        async with postgres.session() as session:
            upsert = insert(AgentInstructionsRow).values(
                repository_id=await Repository.ensure_id(session, full_name),
                instructions=instructions,
            )
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[AgentInstructionsRow.repository_id],
                    set_={"instructions": instructions, "updated_at": func.clock_timestamp()},
                )
            )
        return await self._stored(full_name)

    async def delete(self, full_name: str) -> None:
        """Delete a repository's instructions."""
        key = full_name.lower()
        async with postgres.session() as session:
            await session.execute(
                delete(AgentInstructionsRow).where(
                    AgentInstructionsRow.repository_id
                    == select(Repository.id).where(Repository.key == key).scalar_subquery()
                )
            )

    async def _stored(self, full_name: str) -> AgentInstructions:
        record = await self.get(full_name)
        if record is None:
            raise RuntimeError("agent instructions vanished after a write")
        return record


AGENT_INSTRUCTIONS = AgentInstructionsStore()


async def get_repo_agent_instructions(owner: str, repo: str) -> str | None:
    """Return the custom agent instructions for a repo, if configured."""
    record = await AGENT_INSTRUCTIONS.get(f"{owner}/{repo}")
    return record.instructions.strip() or None if record else None


router = APIRouter(tags=["agent-instructions"])


@router.get("/agent-instructions")
async def api_list_agent_instructions(
    session: dict[str, Any] = SESSION_DEP,
) -> list[AgentInstructions]:
    return await filter_repo_models_for_user(session["sub"], await AGENT_INSTRUCTIONS.list_all())


@router.post("/agent-instructions")
@audit_endpoint
async def api_create_agent_instructions(
    body: AgentInstructionsCreate,
    session: dict[str, Any] = SESSION_DEP,
) -> AgentInstructions:
    await require_repo_access_for_user(session["sub"], body.full_name)
    return await AGENT_INSTRUCTIONS.create(body.full_name, session["sub"])


@router.get("/agent-instructions/{full_name:path}")
async def api_get_agent_instructions(
    full_name: str,
    session: dict[str, Any] = SESSION_DEP,
) -> AgentInstructions:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    record = await AGENT_INSTRUCTIONS.get(full_name)
    if not record:
        raise HTTPException(404, "agent instructions not found")
    return record


@router.put("/agent-instructions/{full_name:path}")
@audit_endpoint
async def api_update_agent_instructions(
    full_name: str,
    body: AgentInstructionsUpdate,
    session: dict[str, Any] = SESSION_DEP,
) -> AgentInstructions:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    return await AGENT_INSTRUCTIONS.set_instructions(full_name, body.instructions)


@router.delete("/agent-instructions/{full_name:path}")
@audit_endpoint
async def api_delete_agent_instructions(
    full_name: str,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    record = await AGENT_INSTRUCTIONS.get(full_name)
    if not record:
        raise HTTPException(404, "agent instructions not found")
    await AGENT_INSTRUCTIONS.delete(full_name)
    return Response(status_code=204)
