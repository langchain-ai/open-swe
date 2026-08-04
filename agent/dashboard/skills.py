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
MAX_SKILL_NAME_CHARS = 64
MAX_SKILL_DESCRIPTION_CHARS = 1024
MAX_SKILL_INSTRUCTIONS_CHARS = 20_000
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


async def get_skill(login: str, name: str) -> dict[str, Any] | None:
    item = await _client().store.get_item(_namespace(login), _key(name))
    return _value(item) if item else None


async def list_skills(login: str) -> list[dict[str, Any]]:
    result = await _client().store.search_items(_namespace(login), limit=1000)
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    skills = [value for item in items or [] if (value := _value(item)) is not None]
    skills.sort(key=lambda skill: skill.get("name", ""))
    return skills


async def create_skill(login: str, body: SkillCreate) -> dict[str, Any]:
    if await get_skill(login, body.name):
        raise HTTPException(409, "skill already exists")
    value = _record(body.name, body.description, body.instructions)
    await _client().store.put_item(_namespace(login), _key(body.name), value)
    return value


async def update_skill(login: str, name: str, body: SkillUpdate) -> dict[str, Any]:
    SkillCreate(name=name, description=body.description, instructions=body.instructions)
    existing = await get_skill(login, name)
    if not existing:
        raise HTTPException(404, "skill not found")
    value = _record(name, body.description, body.instructions, existing)
    await _client().store.put_item(_namespace(login), _key(name), value)
    return value


async def delete_skill(login: str, name: str) -> None:
    SkillCreate(name=name, description="valid")
    if not await get_skill(login, name):
        raise HTTPException(404, "skill not found")
    await _client().store.delete_item(_namespace(login), _key(name))
