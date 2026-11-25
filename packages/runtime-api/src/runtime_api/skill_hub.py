"""The LangSmith workspace Robin's skill repositories live in.

Which repositories a run mounts is the backend's to name (``runtime_api.skills.RunSkills``),
so the runtime needs no skill configuration of its own.
"""

import os
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr


class SkillHubSettings(BaseModel):
    endpoint: str = Field(pattern=r"^https?://[^\s]+$")
    api_key: SecretStr = Field(min_length=1)
    workspace_id: UUID

    @classmethod
    async def from_env(cls) -> Self:
        return cls(
            endpoint=os.environ.get("LANGSMITH_ENDPOINT") or "https://api.smith.langchain.com",
            api_key=SecretStr(os.environ.get("LANGSMITH_API_KEY", "")),
            workspace_id=UUID(
                os.environ.get("LANGSMITH_WORKSPACE_ID")
                or os.environ.get("LANGSMITH_TENANT_ID", "")
            ),
        )
