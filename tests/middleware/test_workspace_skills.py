from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from deepagents.backends.state import StateBackend

from openswe.middleware.model_call_shape import CallShape


@pytest.mark.asyncio
async def test_resumed_public_skill_prompt_excludes_cached_personal_context():
    from openswe.middleware.workspace_skills import WorkspaceSkillsMiddleware

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
    prompt = (await middleware.ashape_model_call(CallShape(), state, MagicMock())).system_prompt
    assert "team detail" in prompt
    assert "private detail" not in prompt
    assert "private diagnostic" not in prompt
    assert len(state["skills_metadata"]) == 2
    update = await middleware.abefore_agent(cast(Any, state), MagicMock(), {})
    assert [skill["name"] for skill in update["skills_metadata"]] == ["workspace"]
    assert update["skills_load_errors"] == []
