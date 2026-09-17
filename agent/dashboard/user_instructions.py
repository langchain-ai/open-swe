"""Per-user custom instructions for the main coding agent.

Each record holds a user-authored instruction prompt (edited in the dashboard
Profile tab, or by the agent itself via ``save_user_instructions``) that is
appended to the main agent's system prompt for runs that user triggers.

Stored in its own namespace rather than on the ``["profiles"]`` record so
agent-written updates and dashboard profile saves can't clobber each other.
"""

from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from agent.dashboard.deps import SESSION_DEP
from agent.store import delete_value, get_value, now_iso, put_value

USER_INSTRUCTIONS_NAMESPACE: list[str] = ["user_instructions"]

MAX_USER_INSTRUCTIONS_CHARS = 20_000


class UserInstructionsUpdate(BaseModel):
    instructions: str = Field(default="", max_length=MAX_USER_INSTRUCTIONS_CHARS)


async def get_user_instructions(login: str) -> dict[str, Any] | None:
    return await get_value(USER_INSTRUCTIONS_NAMESPACE, login)


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
        "created_at": existing.get("created_at") or now_iso(),
        "updated_at": now_iso(),
        "updated_by": updated_by or login,
    }
    await put_value(USER_INSTRUCTIONS_NAMESPACE, login, value)
    return value


async def delete_user_instructions(login: str) -> None:
    await delete_value(USER_INSTRUCTIONS_NAMESPACE, login)


async def get_user_custom_instructions(login: str | None) -> str | None:
    """Return the user's custom instructions for prompt injection, if any."""
    if not login:
        return None
    record = await get_user_instructions(login)
    if not record:
        return None
    instructions = record.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        return instructions.strip()
    return None


router = APIRouter(tags=["user-instructions"])


@router.get("/me/instructions")
async def api_get_my_instructions(
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    login = session["sub"]
    record = await get_user_instructions(login)
    return record or {"login": login, "instructions": ""}


@router.put("/me/instructions")
async def api_put_my_instructions(
    body: UserInstructionsUpdate,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    login = session["sub"]
    return await set_user_instructions(login, body.instructions, updated_by=login)


@router.delete("/me/instructions")
async def api_delete_my_instructions(
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    await delete_user_instructions(session["sub"])
    return Response(status_code=204)
