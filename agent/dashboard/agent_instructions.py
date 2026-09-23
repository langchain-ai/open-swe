"""Per-repository custom instructions for the main coding agent.

Each record holds a user-authored instruction prompt (edited in the dashboard)
that is appended to the main agent's system prompt for runs targeting that repo.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.dashboard.deps import SESSION_DEP, filter_repo_models_for_user
from agent.dashboard.repo_access import require_repo_access_for_user
from agent.review.styles import normalize_repo_full_name
from agent.store import TypedStore, now_iso

AGENT_INSTRUCTIONS_NAMESPACE: list[str] = ["agent_instructions"]


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
    def seed(cls, full_name: str, created_by: str) -> AgentInstructions:
        owner, _, name = full_name.partition("/")
        now = now_iso()
        return cls(
            full_name=full_name,
            owner=owner,
            name=name,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )


class AgentInstructionsStore(TypedStore[AgentInstructions]):
    def __init__(self) -> None:
        super().__init__(AGENT_INSTRUCTIONS_NAMESPACE, AgentInstructions)

    async def list_all(self) -> list[AgentInstructions]:
        records = await self.search_all()
        records.sort(key=lambda record: record.full_name)
        return records

    async def create(self, full_name: str, created_by: str) -> AgentInstructions:
        existing = await self.get(full_name)
        if existing:
            return existing
        return await self.put(full_name, AgentInstructions.seed(full_name, created_by))

    async def set_instructions(self, full_name: str, instructions: str) -> AgentInstructions:
        record = await self.get(full_name) or AgentInstructions.seed(full_name, "")
        record.instructions = instructions
        record.updated_at = now_iso()
        return await self.put(full_name, record)


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
async def api_update_agent_instructions(
    full_name: str,
    body: AgentInstructionsUpdate,
    session: dict[str, Any] = SESSION_DEP,
) -> AgentInstructions:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    return await AGENT_INSTRUCTIONS.set_instructions(full_name, body.instructions)


@router.delete("/agent-instructions/{full_name:path}")
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
