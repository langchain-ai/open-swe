"""Per-user Agent Skills stored as virtual ``SKILL.md`` files."""

import base64
import binascii
import json
import re
from typing import Any
from uuid import UUID

import yaml
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import ColumnElement, ForeignKey, delete, func, literal_column, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
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


class SkillImport(BaseModel):
    files: dict[str, str] = Field(min_length=1, max_length=100)

    @field_validator("files")
    @classmethod
    def valid_files(cls, files: dict[str, str]) -> dict[str, str]:
        if "SKILL.md" not in files or sum(len(text) for text in files.values()) > 1_000_000:
            raise ValueError("A skill needs SKILL.md and at most 1 MB of UTF-8 resources")
        for path, text in files.items():
            if (
                not path
                or len(path) > 256
                or "\\" in path
                or any(part in {"", ".", ".."} or part.startswith(".") for part in path.split("/"))
                or len(text) > 200_000
                or "\x00" in text
            ):
                raise ValueError("Invalid skill resource path or content")
        return files

    def skill(self, name: str) -> SkillCreate:
        content = self.files["SKILL.md"].replace("\r\n", "\n")
        frontmatter = re.match(r"\A---\n(.*?)\n---[ \t]*(?:\n|$)(.*)\Z", content, re.DOTALL)
        if frontmatter is None:
            raise ValueError("SKILL.md needs YAML frontmatter")
        try:
            metadata = yaml.safe_load(frontmatter[1])
        except yaml.YAMLError as error:
            raise ValueError("Invalid SKILL.md YAML frontmatter") from error
        if not isinstance(metadata, dict) or metadata.get("name") != name:
            raise ValueError("Skill name must match its directory")
        return SkillCreate(
            name=name,
            description=metadata.get("description"),
            instructions=frontmatter[2].strip(),
        )

    async def save(self, login: str, name: str) -> dict[str, object]:
        body = self.skill(name)
        owner = await _person(login)
        value = _record(body.name, body.description, body.instructions)
        value.update(content=self.files["SKILL.md"], files=self.files, source="desktop")
        statement = insert(Skill).values(user_id=owner, name=name, value=value)
        statement = statement.on_conflict_do_update(
            index_elements=[
                func.coalesce(
                    Skill.user_id, literal_column("'00000000-0000-0000-0000-000000000000'::uuid")
                ),
                Skill.name,
            ],
            set_={
                "value": statement.excluded.value.op("||")(
                    func.jsonb_build_object("created_at", Skill.value["created_at"])
                )
            },
            where=Skill.value["source"].astext == "desktop",
        ).returning(Skill.id)
        async with postgres.session() as session:
            if await session.scalar(statement) is None:
                raise HTTPException(409, "A cloud-managed skill already has this name")
        return {"name": name}


class SkillUpdate(BaseModel):
    description: str = Field(min_length=1, max_length=MAX_SKILL_DESCRIPTION_CHARS)
    instructions: str = Field(default="", max_length=MAX_SKILL_INSTRUCTIONS_CHARS)

    def content(self, original: str, name: str) -> str:
        frontmatter = re.match(
            r"\A---\n(.*?)\n---[ \t]*(?:\n|$)", original.replace("\r\n", "\n"), re.DOTALL
        )
        metadata = yaml.safe_load(frontmatter[1]) if frontmatter else {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata.update(name=name, description=self.description)
        return (
            f"---\n{yaml.safe_dump(metadata, sort_keys=False)}---\n\n{self.instructions.strip()}\n"
        )

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


async def _person(login: str) -> UUID:
    user = await User.for_login("github", login)
    if user is None:
        raise HTTPException(409, "No Open SWE user record for this login yet")
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


async def _search(owner: Owner, *, limit: int, offset: int = 0) -> list[dict[str, Any]]:
    async with postgres.session() as session:
        values = await session.scalars(
            select(Skill.value)
            .where(_owned_by(owner))
            .order_by(Skill.name)
            .limit(limit)
            .offset(offset)
        )
        return list(values)


async def _get_skill(owner: Owner, name: str) -> dict[str, Any] | None:
    SkillCreate(name=name, description="valid")
    async with postgres.session() as session:
        return await session.scalar(select(Skill.value).where(_owned_by(owner), Skill.name == name))


async def _list_skills(owner: Owner, *, limit: int, offset: int) -> dict[str, Any]:
    found = await _search(owner, limit=limit + 1, offset=offset)
    return {
        "items": [
            {key: value for key, value in skill.items() if key != "files"}
            for skill in found[:limit]
        ],
        "next_offset": offset + limit if len(found) > limit else None,
    }


async def _create_skill(owner: Owner, body: SkillCreate) -> dict[str, Any]:
    if await _get_skill(owner, body.name):
        raise HTTPException(409, "skill already exists")
    value = _record(body.name, body.description, body.instructions)
    async with postgres.session() as session:
        session.add(Skill(user_id=owner, name=body.name, value=value))
    return value


async def _update_skill(owner: Owner, name: str, body: SkillUpdate) -> dict[str, Any]:
    SkillCreate(name=name, description=body.description, instructions=body.instructions)
    existing = await _get_skill(owner, name)
    if not existing:
        raise HTTPException(404, "skill not found")
    value = _record(name, body.description, body.instructions, existing)
    if "files" in existing:
        content = body.content(existing["content"], name)
        value.update(content=content, files={**existing["files"], "SKILL.md": content})
    async with postgres.session() as session:
        await session.execute(
            update(Skill).where(_owned_by(owner), Skill.name == name).values(value=value)
        )
    return value


async def _delete_skill(owner: Owner, name: str) -> None:
    SkillCreate(name=name, description="valid")
    async with postgres.session() as session:
        deleted = await session.execute(
            delete(Skill).where(_owned_by(owner), Skill.name == name).returning(Skill.id)
        )
        if deleted.first() is None:
            raise HTTPException(404, "skill not found")


async def get_skill(login: str, name: str) -> dict[str, Any] | None:
    return await _get_skill(await _person(login), name)


async def list_skills(login: str, *, limit: int, offset: int) -> dict[str, Any]:
    return await _list_skills(await _person(login), limit=limit, offset=offset)


async def create_skill(login: str, body: SkillCreate) -> dict[str, Any]:
    return await _create_skill(await _person(login), body)


async def update_skill(login: str, name: str, body: SkillUpdate) -> dict[str, Any]:
    return await _update_skill(await _person(login), name, body)


async def delete_skill(login: str, name: str) -> None:
    await _delete_skill(await _person(login), name)


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
    existing = await _search(None, limit=MAX_ORGANIZATION_SKILLS)
    if len(existing) >= MAX_ORGANIZATION_SKILLS:
        raise HTTPException(409, "organization skill limit reached")
    return await _create_skill(None, body)


async def update_organization_skill(name: str, body: SkillUpdate) -> dict[str, Any]:
    return await _update_skill(None, name, body)


async def delete_organization_skill(name: str) -> None:
    await _delete_skill(None, name)
