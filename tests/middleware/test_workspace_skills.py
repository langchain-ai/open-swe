from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from deepagents.backends.state import StateBackend
from deepagents.middleware.skills import SkillsMiddleware
from langchain.agents.middleware.types import ModelRequest


@pytest.mark.asyncio
async def test_resumed_public_skill_prompt_excludes_cached_personal_context():
    from agent.middleware.workspace_skills import WorkspaceSkillsMiddleware

    middleware = WorkspaceSkillsMiddleware(
        backend=StateBackend(), sources=["/organization-skills/", "/bundled-skills/"]
    )
    state = {
        "messages": [],
        "skills_metadata": [
            {
                "name": "personal",
                "description": "private detail",
                "path": "/skills/personal/SKILL.md",
                "allowed_tools": [],
            },
            {
                "name": "workspace",
                "description": "team detail",
                "path": "/organization-skills/workspace/SKILL.md",
                "allowed_tools": [],
            },
        ],
        "skills_load_errors": ["private diagnostic"],
    }
    request = ModelRequest(
        model=MagicMock(), messages=[], tools=[], runtime=MagicMock(), state=state
    )
    seen = []

    async def capture(request):
        seen.append(request.system_message.text)
        return MagicMock()

    await middleware.awrap_model_call(request, capture)
    assert "team detail" in seen[0]
    assert "private detail" not in seen[0]
    assert "private diagnostic" not in seen[0]
    assert len(state["skills_metadata"]) == 2
    update = await middleware.abefore_agent(cast(Any, state), MagicMock(), {})
    assert [skill["name"] for skill in update["skills_metadata"]] == ["workspace"]
    assert update["skills_load_errors"] == []
    assert middleware.name == SkillsMiddleware(backend=StateBackend(), sources=[]).name
