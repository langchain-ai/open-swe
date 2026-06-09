from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from agent import server
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


def test_resolve_repo_custom_instructions_returns_none_without_repo() -> None:
    result = asyncio.run(server._resolve_repo_custom_instructions(None))
    assert result is None
