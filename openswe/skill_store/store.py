"""Per-user Agent Skills stored as virtual ``SKILL.md`` files."""

import base64
import binascii
import json
import re
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import ColumnElement, ForeignKey, delete, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import Base
from openswe.users.models import User
from openswe.utils.json_types import JsonObject
from openswe.utils.time import now_iso

MAX_SKILL_NAME_CHARS = 64
MAX_SKILL_DESCRIPTION_CHARS = 1024
MAX_SKILL_INSTRUCTIONS_CHARS = 20_000
DEFAULT_SKILLS_PAGE_SIZE = 100
MAX_SKILLS_PAGE_SIZE = 100
MAX_ORGANIZATION_SKILLS = 1000
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_SKILL_NAME_CHARS)
    description: str = Field(min_length=1, max_length=MAX_SKILL_DESCRIPTION_CHARS)
    instructions: str = Field(default="", max_length=MAX_SKILL_INSTRUCTIONS_CHARS)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        value = value.strip()
        if not _SKILL_NAME_RE.fullmatch(value):
            raise ValueError("name must use lowercase letters, numbers, and single hyphens")
        return value

    @field_validator("description")
    @classmethod
    def _non_empty_description(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("description cannot be empty")
        return value


class SkillUpdate(BaseModel):
    description: str = Field(min_length=1, max_length=MAX_SKILL_DESCRIPTION_CHARS)
    instructions: str = Field(default="", max_length=MAX_SKILL_INSTRUCTIONS_CHARS)

    @field_validator("description")
    @classmethod
    def _non_empty_description(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("description cannot be empty")
        return value


class Skill(Base):
    """A person's skill, or the organization's when ``user_id`` is ``None``."""

    __tablename__ = "skill"

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str]
    value: Mapped[JsonObject] = mapped_column(JSONB)


type Owner = UUID | None


async def _person(login: str, *, session: AsyncSession | None = None) -> UUID:
    user = await User.for_login("github", login, session=session)
    if user is None:
        raise HTTPException(409, "No Robin user record for this login yet")
    return user.id


def skill_path(name: str) -> str:
    return f"/{name}/SKILL.md"


def _content(name: str, description: str, instructions: str) -> str:
    return (
        "---\n"
        f"name: {json.dumps(name)}\n"
        f"description: {json.dumps(description)}\n"
        "---\n\n"
        f"{instructions.strip()}\n"
    )


def _record(
    name: str, description: str, instructions: str, existing: dict[str, Any] | None = None
) -> dict[str, Any]:
    now = now_iso()
    return {
        "name": name,
        "description": description,
        "instructions": instructions,
        "content": _content(name, description, instructions),
        "encoding": "utf-8",
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
    }


def _owned_by(owner: Owner) -> ColumnElement[bool]:
    return Skill.user_id.is_(None) if owner is None else Skill.user_id == owner


async def _search(
    owner: Owner, *, limit: int, offset: int = 0, session: AsyncSession | None = None
) -> list[dict[str, Any]]:
    async with postgres.session(session) as session:
        values = await session.scalars(
            select(Skill.value)
            .where(_owned_by(owner))
            .order_by(Skill.name)
            .limit(limit)
            .offset(offset)
        )
        return list(values)


async def _get_skill(
    owner: Owner, name: str, *, session: AsyncSession | None = None
) -> dict[str, Any] | None:
    SkillCreate(name=name, description="valid")
    async with postgres.session(session) as session:
        return await session.scalar(select(Skill.value).where(_owned_by(owner), Skill.name == name))


async def _list_skills(
    owner: Owner, *, limit: int, offset: int, session: AsyncSession | None = None
) -> dict[str, Any]:
    found = await _search(owner, limit=limit + 1, offset=offset, session=session)
    return {
        "items": found[:limit],
        "next_offset": offset + limit if len(found) > limit else None,
    }


async def _create_skill(
    owner: Owner, body: SkillCreate, *, session: AsyncSession | None = None
) -> dict[str, Any]:
    value = _record(body.name, body.description, body.instructions)
    try:
        async with postgres.session(session) as db:
            if await _get_skill(owner, body.name, session=db):
                raise HTTPException(409, "skill already exists")
            db.add(Skill(user_id=owner, name=body.name, value=value))
            await db.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) != "23505":
            raise
        raise HTTPException(409, "skill already exists") from exc
    return value


async def _update_skill(
    owner: Owner, name: str, body: SkillUpdate, *, session: AsyncSession | None = None
) -> dict[str, Any]:
    SkillCreate(name=name, description=body.description, instructions=body.instructions)
    async with postgres.session(session) as db:
        existing = await _get_skill(owner, name, session=db)
        if not existing:
            raise HTTPException(404, "skill not found")
        value = _record(name, body.description, body.instructions, existing)
        await db.execute(
            update(Skill).where(_owned_by(owner), Skill.name == name).values(value=value)
        )
    return value


async def _delete_skill(owner: Owner, name: str, *, session: AsyncSession | None = None) -> None:
    SkillCreate(name=name, description="valid")
    async with postgres.session(session) as session:
        deleted = await session.execute(
            delete(Skill).where(_owned_by(owner), Skill.name == name).returning(Skill.id)
        )
        if deleted.first() is None:
            raise HTTPException(404, "skill not found")


async def get_skill(login: str, name: str) -> dict[str, Any] | None:
    return await _get_skill(await _person(login), name)


async def list_skills(
    login: str,
    *,
    limit: int,
    offset: int,
    user: User | None = None,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    async with postgres.session(session) as db:
        owner = user.id if user is not None else await _person(login, session=db)
        return await _list_skills(owner, limit=limit, offset=offset, session=db)


async def create_skill(
    login: str, body: SkillCreate, *, user: User | None = None, session: AsyncSession | None = None
) -> dict[str, Any]:
    async with postgres.session(session) as db:
        owner = user.id if user is not None else await _person(login, session=db)
        return await _create_skill(owner, body, session=db)


async def update_skill(
    login: str,
    name: str,
    body: SkillUpdate,
    *,
    user: User | None = None,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    async with postgres.session(session) as db:
        owner = user.id if user is not None else await _person(login, session=db)
        return await _update_skill(owner, name, body, session=db)


async def delete_skill(
    login: str, name: str, *, user: User | None = None, session: AsyncSession | None = None
) -> None:
    async with postgres.session(session) as db:
        owner = user.id if user is not None else await _person(login, session=db)
        await _delete_skill(owner, name, session=db)


async def agent_skills(login: str | None) -> list[dict[str, Any]]:
    """The organization's skills, or ``login``'s, as the agent reads them."""
    if login is None:
        return await _search(None, limit=MAX_ORGANIZATION_SKILLS)
    user = await User.for_login("github", login)
    return [] if user is None else await _search(user.id, limit=MAX_ORGANIZATION_SKILLS)


def _encode_cursor(name: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({"name": name}).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> str:
    if cursor is None:
        return ""
    if not cursor:
        raise HTTPException(400, "invalid cursor")
    try:
        encoded = cursor.encode("ascii")
        payload = json.loads(
            base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        )
    except (
        binascii.Error,
        UnicodeEncodeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        raise HTTPException(400, "invalid cursor") from None
    if not isinstance(payload, dict) or set(payload) != {"name"}:
        raise HTTPException(400, "invalid cursor")
    name = payload["name"]
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= MAX_SKILL_NAME_CHARS
        or not _SKILL_NAME_RE.fullmatch(name)
    ):
        raise HTTPException(400, "invalid cursor")
    return name


async def list_organization_skills(*, limit: int, cursor: str | None) -> dict[str, Any]:
    after = _decode_cursor(cursor)
    found = await _search(None, limit=MAX_ORGANIZATION_SKILLS + 1)
    if len(found) > MAX_ORGANIZATION_SKILLS:
        raise HTTPException(409, "organization skill limit exceeded; delete a skill to continue")
    skills = sorted(
        (value for value in found if value.get("name", "") > after),
        key=lambda skill: skill.get("name", ""),
    )
    page = skills[:limit]
    return {
        "items": page,
        "next_cursor": _encode_cursor(page[-1]["name"]) if len(skills) > limit else None,
    }


async def create_organization_skill(body: SkillCreate) -> dict[str, Any]:
    async with postgres.session() as session:
        # Serialize organization creates so the limit check and insert are atomic.
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('organization-skills'))"))
        existing = await _search(None, limit=MAX_ORGANIZATION_SKILLS, session=session)
        if len(existing) >= MAX_ORGANIZATION_SKILLS:
            raise HTTPException(409, "organization skill limit reached")
        return await _create_skill(None, body, session=session)


async def update_organization_skill(name: str, body: SkillUpdate) -> dict[str, Any]:
    return await _update_skill(None, name, body)


async def delete_organization_skill(name: str) -> None:
    await _delete_skill(None, name)
