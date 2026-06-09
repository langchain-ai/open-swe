"""Per-repository custom instructions for the main coding agent.

Each record holds a user-authored instruction prompt (edited in the dashboard)
that is appended to the main agent's system prompt for runs targeting that repo.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from langgraph_sdk import get_client
from pydantic import BaseModel, Field, field_validator

from .review_styles import normalize_repo_full_name

logger = logging.getLogger(__name__)

AGENT_INSTRUCTIONS_NAMESPACE: list[str] = ["agent_instructions"]


class AgentInstructionsCreate(BaseModel):
    full_name: str = Field(..., description="GitHub repo in owner/name form")

    @field_validator("full_name", mode="before")
    @classmethod
    def _valid_full_name(cls, v: str) -> str:
        return normalize_repo_full_name(v)


class AgentInstructionsUpdate(BaseModel):
    instructions: str = Field(default="")


def _client():
    return get_client()


async def _get_value(key: str) -> dict[str, Any] | None:
    try:
        item = await _client().store.get_item(AGENT_INSTRUCTIONS_NAMESPACE, key)
    except Exception as e:  # noqa: BLE001
        logger.debug("store get_item failed for %s: %s", key, e)
        return None
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _default_record(full_name: str, created_by: str) -> dict[str, Any]:
    owner, name = full_name.split("/", 1)
    return {
        "full_name": full_name,
        "owner": owner,
        "name": name,
        "instructions": "",
        "created_by": created_by,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


async def get_agent_instructions(full_name: str) -> dict[str, Any] | None:
    return await _get_value(full_name)


async def list_agent_instructions() -> list[dict[str, Any]]:
    result = await _client().store.search_items(AGENT_INSTRUCTIONS_NAMESPACE, limit=1000)
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    out: list[dict[str, Any]] = []
    for item in items or []:
        value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
        if isinstance(value, dict):
            out.append(value)
    out.sort(key=lambda r: r.get("full_name", ""))
    return out


async def create_agent_instructions(full_name: str, created_by: str) -> dict[str, Any]:
    existing = await get_agent_instructions(full_name)
    if existing:
        return existing
    value = _default_record(full_name, created_by)
    await _client().store.put_item(AGENT_INSTRUCTIONS_NAMESPACE, full_name, value)
    return value


async def set_agent_instructions(full_name: str, instructions: str) -> dict[str, Any]:
    existing = await get_agent_instructions(full_name) or _default_record(full_name, "")
    value = {**existing, "instructions": instructions, "updated_at": _now_iso()}
    await _client().store.put_item(AGENT_INSTRUCTIONS_NAMESPACE, full_name, value)
    return value


async def delete_agent_instructions(full_name: str) -> None:
    await _client().store.delete_item(AGENT_INSTRUCTIONS_NAMESPACE, full_name)


async def get_repo_agent_instructions(owner: str, repo: str) -> str | None:
    """Return the custom agent instructions for a repo, if configured."""
    record = await get_agent_instructions(f"{owner}/{repo}")
    if not record:
        return None
    instructions = record.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        return instructions.strip()
    return None
