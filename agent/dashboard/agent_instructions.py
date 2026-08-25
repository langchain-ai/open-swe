"""Per-repository custom instructions for the main coding agent.

Each record holds a user-authored instruction prompt (edited in the dashboard)
that is appended to the main agent's system prompt for runs targeting that repo.
"""

from typing import Any

from pydantic import BaseModel, Field, field_validator

from agent.store import KeyedRecordStore, now_iso

from .review_styles import normalize_repo_full_name

AGENT_INSTRUCTIONS_NAMESPACE: list[str] = ["agent_instructions"]


class AgentInstructionsCreate(BaseModel):
    full_name: str = Field(..., description="GitHub repo in owner/name form")

    @field_validator("full_name", mode="before")
    @classmethod
    def _valid_full_name(cls, v: str) -> str:
        return normalize_repo_full_name(v)


class AgentInstructionsUpdate(BaseModel):
    instructions: str = Field(default="")


def _default_record(full_name: str, created_by: str) -> dict[str, Any]:
    owner, name = full_name.split("/", 1)
    return {
        "full_name": full_name,
        "owner": owner,
        "name": name,
        "instructions": "",
        "created_by": created_by,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


_RECORDS = KeyedRecordStore(
    AGENT_INSTRUCTIONS_NAMESPACE,
    sort_key="full_name",
    default_factory=_default_record,
)


async def get_agent_instructions(full_name: str) -> dict[str, Any] | None:
    return await _RECORDS.get(full_name)


async def list_agent_instructions() -> list[dict[str, Any]]:
    return await _RECORDS.list()


async def create_agent_instructions(full_name: str, created_by: str) -> dict[str, Any]:
    return await _RECORDS.create(full_name, created_by)


async def set_agent_instructions(full_name: str, instructions: str) -> dict[str, Any]:
    return await _RECORDS.update(full_name, {"instructions": instructions})


async def delete_agent_instructions(full_name: str) -> None:
    await _RECORDS.delete(full_name)


async def get_repo_agent_instructions(owner: str, repo: str) -> str | None:
    """Return the custom agent instructions for a repo, if configured."""
    record = await get_agent_instructions(f"{owner}/{repo}")
    if not record:
        return None
    instructions = record.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        return instructions.strip()
    return None
