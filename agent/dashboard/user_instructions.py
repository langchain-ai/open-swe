"""Per-user custom instructions for the main coding agent.

Each record holds a user-authored instruction prompt (edited in the dashboard
Profile tab, or by the agent itself via ``save_user_instructions``) that is
appended to the main agent's system prompt for runs that user triggers.

Stored in its own namespace rather than on the ``["profiles"]`` record so
agent-written updates and dashboard profile saves can't clobber each other.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from langgraph_sdk import get_client
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

USER_INSTRUCTIONS_NAMESPACE: list[str] = ["user_instructions"]

MAX_USER_INSTRUCTIONS_CHARS = 20_000


class UserInstructionsUpdate(BaseModel):
    instructions: str = Field(default="", max_length=MAX_USER_INSTRUCTIONS_CHARS)


def _client():
    return get_client()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def get_user_instructions(login: str) -> dict[str, Any] | None:
    try:
        item = await _client().store.get_item(USER_INSTRUCTIONS_NAMESPACE, login)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def set_user_instructions(
    login: str,
    instructions: str,
    updated_by: str = "",
) -> dict[str, Any]:
    existing = await get_user_instructions(login) or {}
    value: dict[str, Any] = {
        **existing,
        "login": login,
        "instructions": instructions[:MAX_USER_INSTRUCTIONS_CHARS],
        "created_at": existing.get("created_at") or _now_iso(),
        "updated_at": _now_iso(),
        "updated_by": updated_by or login,
    }
    await _client().store.put_item(USER_INSTRUCTIONS_NAMESPACE, login, value)
    return value


async def delete_user_instructions(login: str) -> None:
    await _client().store.delete_item(USER_INSTRUCTIONS_NAMESPACE, login)


async def get_user_custom_instructions(login: str | None) -> str | None:
    """Return the user's custom instructions for prompt injection, if any."""
    if not login:
        return None
    try:
        record = await get_user_instructions(login)
    except Exception:
        logger.debug("Failed to load user custom instructions for %s", login, exc_info=True)
        return None
    if not record:
        return None
    instructions = record.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        return instructions.strip()
    return None
