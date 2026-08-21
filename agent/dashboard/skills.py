"""Per-user Agent Skills stored as virtual ``SKILL.md`` files."""

import base64
import binascii
import json
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..store import delete_value, get_value, now_iso, put_value, search_values


class SkillError(Exception):
    """A skill request the store cannot satisfy.

    ``status_code``/``detail`` are the HTTP answer the web layer should give;
    it maps them itself so this module stays free of FastAPI.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


SKILLS_NAMESPACE = "user_skills"
ORGANIZATION_SKILLS_NAMESPACE = "organization_skills"
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


def _namespace(login: str) -> list[str]:
    return [SKILLS_NAMESPACE, login]


def _organization_namespace() -> list[str]:
    return [ORGANIZATION_SKILLS_NAMESPACE]


def _key(name: str) -> str:
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


async def _get_skill(namespace: list[str], name: str) -> dict[str, Any] | None:
    SkillCreate(name=name, description="valid")
    return await get_value(namespace, _key(name))


async def _list_skills(namespace: list[str], *, limit: int, offset: int) -> dict[str, Any]:
    found = await search_values(namespace, limit=limit + 1, offset=offset)
    skills = sorted(found[:limit], key=lambda skill: skill.get("name", ""))
    return {
        "items": skills,
        "next_offset": offset + limit if len(found) > limit else None,
    }


async def _create_skill(namespace: list[str], body: SkillCreate) -> dict[str, Any]:
    if await _get_skill(namespace, body.name):
        raise SkillError(409, "skill already exists")
    value = _record(body.name, body.description, body.instructions)
    await put_value(namespace, _key(body.name), value)
    return value


async def _update_skill(namespace: list[str], name: str, body: SkillUpdate) -> dict[str, Any]:
    SkillCreate(name=name, description=body.description, instructions=body.instructions)
    existing = await _get_skill(namespace, name)
    if not existing:
        raise SkillError(404, "skill not found")
    value = _record(name, body.description, body.instructions, existing)
    await put_value(namespace, _key(name), value)
    return value


async def _delete_skill(namespace: list[str], name: str) -> None:
    SkillCreate(name=name, description="valid")
    if not await _get_skill(namespace, name):
        raise SkillError(404, "skill not found")
    await delete_value(namespace, _key(name))


async def get_skill(login: str, name: str) -> dict[str, Any] | None:
    return await _get_skill(_namespace(login), name)


async def list_skills(login: str, *, limit: int, offset: int) -> dict[str, Any]:
    return await _list_skills(_namespace(login), limit=limit, offset=offset)


async def create_skill(login: str, body: SkillCreate) -> dict[str, Any]:
    return await _create_skill(_namespace(login), body)


async def update_skill(login: str, name: str, body: SkillUpdate) -> dict[str, Any]:
    return await _update_skill(_namespace(login), name, body)


async def delete_skill(login: str, name: str) -> None:
    await _delete_skill(_namespace(login), name)


def _encode_cursor(name: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({"name": name}).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> str:
    if cursor is None:
        return ""
    if not cursor:
        raise SkillError(400, "invalid cursor")
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
        raise SkillError(400, "invalid cursor") from None
    if not isinstance(payload, dict) or set(payload) != {"name"}:
        raise SkillError(400, "invalid cursor")
    name = payload["name"]
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= MAX_SKILL_NAME_CHARS
        or not _SKILL_NAME_RE.fullmatch(name)
    ):
        raise SkillError(400, "invalid cursor")
    return name


async def list_organization_skills(*, limit: int, cursor: str | None) -> dict[str, Any]:
    after = _decode_cursor(cursor)
    found = await search_values(_organization_namespace(), limit=MAX_ORGANIZATION_SKILLS + 1)
    if len(found) > MAX_ORGANIZATION_SKILLS:
        raise SkillError(409, "organization skill limit exceeded; delete a skill to continue")
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
    existing = await search_values(_organization_namespace(), limit=MAX_ORGANIZATION_SKILLS)
    if len(existing) >= MAX_ORGANIZATION_SKILLS:
        raise SkillError(409, "organization skill limit reached")
    return await _create_skill(_organization_namespace(), body)


async def update_organization_skill(name: str, body: SkillUpdate) -> dict[str, Any]:
    return await _update_skill(_organization_namespace(), name, body)


async def delete_organization_skill(name: str) -> None:
    await _delete_skill(_organization_namespace(), name)
