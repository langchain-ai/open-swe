from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openswe.web import agent_instructions as instructions_api
from openswe.web import deps
from openswe.web.agent_instructions import (
    AGENT_INSTRUCTIONS,
    AgentInstructions,
)
from tests.conftest import FakeStore


@pytest.mark.asyncio
async def test_create_agent_instructions_is_idempotent(fake_store: FakeStore) -> None:
    first = await AGENT_INSTRUCTIONS.create("acme/repo", "octo")
    second = await AGENT_INSTRUCTIONS.create("acme/repo", "someone-else")
    assert second == first


@pytest.mark.asyncio
async def test_list_agent_instructions_filters_inaccessible_repos(monkeypatch) -> None:
    monkeypatch.setattr(
        AGENT_INSTRUCTIONS,
        "list_all",
        AsyncMock(
            return_value=[
                AgentInstructions(full_name="acme/visible", instructions="visible"),
                AgentInstructions(full_name="acme/private", instructions="private"),
            ]
        ),
    )

    async def fake_require_repo_access_for_user(login: str, full_name: str) -> str:
        if full_name == "acme/private":
            raise HTTPException(403, "no access")
        return "token"

    monkeypatch.setattr(deps, "require_repo_access_for_user", fake_require_repo_access_for_user)

    result = await instructions_api.api_list_agent_instructions(session={"sub": "octocat"})

    assert result == [AgentInstructions(full_name="acme/visible", instructions="visible")]


@pytest.mark.asyncio
async def test_delete_agent_instructions_requires_repo_access_before_delete(monkeypatch) -> None:
    delete_instructions = AsyncMock()
    get_instructions = AsyncMock(
        return_value=AgentInstructions(full_name="acme/repo", instructions="rules")
    )
    monkeypatch.setattr(AGENT_INSTRUCTIONS, "get", get_instructions)
    monkeypatch.setattr(
        instructions_api,
        "require_repo_access_for_user",
        AsyncMock(side_effect=HTTPException(403, "no access")),
    )
    monkeypatch.setattr(AGENT_INSTRUCTIONS, "delete", delete_instructions)

    with pytest.raises(HTTPException) as exc:
        await instructions_api.api_delete_agent_instructions(
            "acme/repo", session={"sub": "octocat"}
        )

    assert exc.value.status_code == 403
    get_instructions.assert_not_awaited()
    delete_instructions.assert_not_awaited()
