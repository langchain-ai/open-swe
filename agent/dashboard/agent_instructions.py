"""Per-repository custom instructions for the main coding agent.

Each record holds a user-authored instruction prompt (edited in the dashboard)
that is appended to the main agent's system prompt for runs targeting that repo.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.dashboard.review_styles import normalize_repo_full_name
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
    def seed(cls, full_name: str, created_by: str) -> "AgentInstructions":
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
