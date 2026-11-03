from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openswe.dashboard import agent_instructions as instructions_api
from openswe.dashboard import deps
from openswe.dashboard.agent_instructions import (
    AGENT_INSTRUCTIONS,
    AgentInstructions,
    get_repo_agent_instructions,
)


@pytest.mark.usefixtures("registry_db")
async def test_create_agent_instructions_is_idempotent() -> None:
    first = await AGENT_INSTRUCTIONS.create("acme/repo", "octo")
    await AGENT_INSTRUCTIONS.set_instructions("Acme/Repo", "rules")
    second = await AGENT_INSTRUCTIONS.create("ACME/repo", "someone-else")
    assert second.instructions == "rules"
    assert second.created_at == first.created_at
    assert await get_repo_agent_instructions("acme", "REPO") == "rules"


@pytest.mark.parametrize("character", ["a", "😀"])
async def test_update_agent_instructions_limits_utf8_bytes(monkeypatch, character: str) -> None:
    monkeypatch.setattr(instructions_api, "require_repo_access_for_user", AsyncMock())
    monkeypatch.setattr(AGENT_INSTRUCTIONS, "set_instructions", AsyncMock())
    body = instructions_api.AgentInstructionsUpdate(
        instructions=character * (32 * 1024 // len(character.encode("utf-8")))
    )
    await instructions_api.api_update_agent_instructions("acme/repo", body, {"sub": "octocat"})
    body.instructions += "a"
    with pytest.raises(HTTPException) as exc:
        await instructions_api.api_update_agent_instructions("acme/repo", body, {"sub": "octocat"})
    assert exc.value.status_code == 422
    assert exc.value.detail == (
        "Repository custom instructions must not exceed 32 KiB (32,768 UTF-8 bytes)"
    )


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
