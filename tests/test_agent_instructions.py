from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from agent import server
from agent.dashboard import routes
from agent.dashboard.agent_instructions import (
    create_agent_instructions,
    get_repo_agent_instructions,
    set_agent_instructions,
)
from agent.prompt import construct_system_prompt


@pytest.mark.asyncio
async def test_get_repo_agent_instructions_returns_trimmed_text() -> None:
    with patch(
        "agent.dashboard.agent_instructions.get_agent_instructions",
        new_callable=AsyncMock,
        return_value={"instructions": "  Always run mypy.\n"},
    ):
        result = await get_repo_agent_instructions("acme", "repo")
    assert result == "Always run mypy."


@pytest.mark.asyncio
async def test_get_repo_agent_instructions_returns_none_when_empty() -> None:
    with patch(
        "agent.dashboard.agent_instructions.get_agent_instructions",
        new_callable=AsyncMock,
        return_value={"instructions": "   "},
    ):
        result = await get_repo_agent_instructions("acme", "repo")
    assert result is None


@pytest.mark.asyncio
async def test_create_agent_instructions_puts_new_record() -> None:
    mock_put = AsyncMock()
    with (
        patch(
            "agent.dashboard.agent_instructions._get_value",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("agent.dashboard.agent_instructions._client") as mock_client,
    ):
        mock_client.return_value.store.put_item = mock_put
        record = await create_agent_instructions("acme/repo", "octo")
    assert record["full_name"] == "acme/repo"
    assert record["instructions"] == ""
    mock_put.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_agent_instructions_updates_store() -> None:
    mock_put = AsyncMock()
    with (
        patch(
            "agent.dashboard.agent_instructions.get_agent_instructions",
            new_callable=AsyncMock,
            return_value={"full_name": "acme/repo", "instructions": ""},
        ),
        patch("agent.dashboard.agent_instructions._client") as mock_client,
    ):
        mock_client.return_value.store.put_item = mock_put
        record = await set_agent_instructions("acme/repo", "Use direct tone.")
    assert record["instructions"] == "Use direct tone."
    mock_put.assert_awaited_once()


def test_construct_system_prompt_appends_repo_instructions() -> None:
    prompt = construct_system_prompt(
        working_dir="/work",
        repo_custom_instructions="Prefer pytest over unittest.",
    )
    assert "Repository-specific Custom Instructions" in prompt
    assert "Prefer pytest over unittest." in prompt


def test_construct_system_prompt_without_repo_instructions() -> None:
    prompt = construct_system_prompt(working_dir="/work")
    assert "Repository-specific Custom Instructions" not in prompt


def test_construct_system_prompt_adds_wide_read_for_fireworks_model() -> None:
    prompt = construct_system_prompt(
        working_dir="/work",
        model_id="fireworks:accounts/fireworks/models/glm-5p2",
    )
    assert "Reading Files Efficiently" in prompt
    assert "limit` >= 200" in prompt


def test_construct_system_prompt_omits_wide_read_for_default_model() -> None:
    prompt = construct_system_prompt(working_dir="/work", model_id="openai:gpt-5.5")
    assert "Reading Files Efficiently" not in prompt


def test_construct_system_prompt_omits_wide_read_for_claude() -> None:
    prompt = construct_system_prompt(working_dir="/work", model_id="anthropic:claude-opus-4-8")
    assert "Reading Files Efficiently" not in prompt


def test_construct_system_prompt_omits_wide_read_without_model_id() -> None:
    prompt = construct_system_prompt(working_dir="/work")
    assert "Reading Files Efficiently" not in prompt


def test_resolve_repo_custom_instructions_returns_none_without_repo() -> None:
    result = asyncio.run(server._resolve_repo_custom_instructions(None))
    assert result is None


@pytest.mark.asyncio
async def test_list_agent_instructions_filters_inaccessible_repos(monkeypatch) -> None:
    monkeypatch.setattr(
        routes,
        "list_agent_instructions",
        AsyncMock(
            return_value=[
                {"full_name": "acme/visible", "instructions": "visible"},
                {"full_name": "acme/private", "instructions": "private"},
            ]
        ),
    )

    async def fake_require_repo_access_for_user(login: str, full_name: str) -> str:
        if full_name == "acme/private":
            raise HTTPException(403, "no access")
        return "token"

    monkeypatch.setattr(routes, "require_repo_access_for_user", fake_require_repo_access_for_user)

    result = await routes.api_list_agent_instructions(session={"sub": "octocat"})

    assert result == [{"full_name": "acme/visible", "instructions": "visible"}]


@pytest.mark.asyncio
async def test_get_agent_instructions_requires_repo_access(monkeypatch) -> None:
    require_access = AsyncMock(return_value="token")
    monkeypatch.setattr(
        routes,
        "get_agent_instructions",
        AsyncMock(return_value={"full_name": "acme/repo", "instructions": "rules"}),
    )
    monkeypatch.setattr(routes, "require_repo_access_for_user", require_access)

    result = await routes.api_get_agent_instructions(
        "https://github.com/acme/repo", session={"sub": "octocat"}
    )

    assert result == {"full_name": "acme/repo", "instructions": "rules"}
    require_access.assert_awaited_once_with("octocat", "acme/repo")


@pytest.mark.asyncio
async def test_delete_agent_instructions_requires_repo_access_before_delete(monkeypatch) -> None:
    delete_instructions = AsyncMock()
    get_instructions = AsyncMock(return_value={"full_name": "acme/repo", "instructions": "rules"})
    monkeypatch.setattr(routes, "get_agent_instructions", get_instructions)
    monkeypatch.setattr(
        routes,
        "require_repo_access_for_user",
        AsyncMock(side_effect=HTTPException(403, "no access")),
    )
    monkeypatch.setattr(routes, "delete_agent_instructions", delete_instructions)

    with pytest.raises(HTTPException) as exc:
        await routes.api_delete_agent_instructions("acme/repo", session={"sub": "octocat"})

    assert exc.value.status_code == 403
    get_instructions.assert_not_awaited()
    delete_instructions.assert_not_awaited()
