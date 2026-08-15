"""Per-user Agent Skills stored as virtual ``SKILL.md`` files."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from langgraph_sdk import get_client
from pydantic import BaseModel, Field, field_validator

SKILLS_NAMESPACE = "user_skills"
ORGANIZATION_SKILLS_NAMESPACE = "organization_skills"
MAX_SKILL_NAME_CHARS = 64
MAX_SKILL_DESCRIPTION_CHARS = 1024
MAX_SKILL_INSTRUCTIONS_CHARS = 20_000
DEFAULT_SKILLS_PAGE_SIZE = 100
MAX_SKILLS_PAGE_SIZE = 100
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


def _client():
    return get_client()


def _namespace(login: str) -> list[str]:
    return [SKILLS_NAMESPACE, login]


def _organization_namespace() -> list[str]:
    return [ORGANIZATION_SKILLS_NAMESPACE]


def _key(name: str) -> str:
    return f"/{name}/SKILL.md"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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
    now = _now_iso()
    return {
        "name": name,
        "description": description,
        "instructions": instructions,
        "content": _content(name, description, instructions),
        "encoding": "utf-8",
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
    }


def _value(item: Any) -> dict[str, Any] | None:
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def _get_skill(namespace: list[str], name: str) -> dict[str, Any] | None:
    SkillCreate(name=name, description="valid")
    item = await _client().store.get_item(namespace, _key(name))
    return _value(item) if item else None


async def _list_skills(namespace: list[str], *, limit: int, offset: int) -> dict[str, Any]:
    result = await _client().store.search_items(namespace, limit=limit + 1, offset=offset)
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    skills = [value for item in (items or [])[:limit] if (value := _value(item)) is not None]
    skills.sort(key=lambda skill: skill.get("name", ""))
    return {
        "items": skills,
        "next_offset": offset + limit if len(items or []) > limit else None,
    }


async def _create_skill(namespace: list[str], body: SkillCreate) -> dict[str, Any]:
    if await _get_skill(namespace, body.name):
        raise HTTPException(409, "skill already exists")
    value = _record(body.name, body.description, body.instructions)
    await _client().store.put_item(namespace, _key(body.name), value)
    return value


async def _update_skill(namespace: list[str], name: str, body: SkillUpdate) -> dict[str, Any]:
    SkillCreate(name=name, description=body.description, instructions=body.instructions)
    existing = await _get_skill(namespace, name)
    if not existing:
        raise HTTPException(404, "skill not found")
    value = _record(name, body.description, body.instructions, existing)
    await _client().store.put_item(namespace, _key(name), value)
    return value


async def _delete_skill(namespace: list[str], name: str) -> None:
    SkillCreate(name=name, description="valid")
    if not await _get_skill(namespace, name):
        raise HTTPException(404, "skill not found")
    await _client().store.delete_item(namespace, _key(name))


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


async def list_organization_skills(*, limit: int, offset: int) -> dict[str, Any]:
    return await _list_skills(_organization_namespace(), limit=limit, offset=offset)


async def create_organization_skill(body: SkillCreate) -> dict[str, Any]:
    return await _create_skill(_organization_namespace(), body)


async def update_organization_skill(name: str, body: SkillUpdate) -> dict[str, Any]:
    return await _update_skill(_organization_namespace(), name, body)


async def delete_organization_skill(name: str) -> None:
    await _delete_skill(_organization_namespace(), name)
