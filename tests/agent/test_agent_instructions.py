import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent import server
from agent.api import routes
from agent.dashboard.agent_instructions import (
    AGENT_INSTRUCTIONS,
    AGENT_INSTRUCTIONS_NAMESPACE,
    AgentInstructions,
    get_repo_agent_instructions,
)
from agent.prompt import construct_system_prompt
from tests.conftest import FakeStore


@pytest.mark.asyncio
async def test_get_repo_agent_instructions_returns_trimmed_text(fake_store: FakeStore) -> None:
    await AGENT_INSTRUCTIONS.set_instructions("acme/repo", "  Always run mypy.\n")
    assert await get_repo_agent_instructions("acme", "repo") == "Always run mypy."


@pytest.mark.asyncio
async def test_get_repo_agent_instructions_returns_none_when_empty(fake_store: FakeStore) -> None:
    await AGENT_INSTRUCTIONS.set_instructions("acme/repo", "   ")
    assert await get_repo_agent_instructions("acme", "repo") is None


@pytest.mark.asyncio
async def test_create_agent_instructions_puts_new_record(fake_store: FakeStore) -> None:
    record = await AGENT_INSTRUCTIONS.create("acme/repo", "octo")

    assert record.full_name == "acme/repo"
    assert record.owner == "acme"
    assert record.instructions == ""
    assert record.created_by == "octo"
    assert fake_store.values(AGENT_INSTRUCTIONS_NAMESPACE)["acme/repo"] == record.model_dump(
        mode="json"
    )


@pytest.mark.asyncio
async def test_create_agent_instructions_is_idempotent(fake_store: FakeStore) -> None:
    first = await AGENT_INSTRUCTIONS.create("acme/repo", "octo")
    second = await AGENT_INSTRUCTIONS.create("acme/repo", "someone-else")
    assert second == first


@pytest.mark.asyncio
async def test_set_agent_instructions_updates_store(fake_store: FakeStore) -> None:
    await AGENT_INSTRUCTIONS.create("acme/repo", "octo")

    record = await AGENT_INSTRUCTIONS.set_instructions("acme/repo", "Use direct tone.")

    assert record.instructions == "Use direct tone."
    assert record.created_by == "octo"
    assert (await AGENT_INSTRUCTIONS.get("acme/repo")) == record


@pytest.mark.asyncio
async def test_list_agent_instructions_sorts_by_full_name(fake_store: FakeStore) -> None:
    await AGENT_INSTRUCTIONS.create("acme/zebra", "octo")
    await AGENT_INSTRUCTIONS.create("acme/apple", "octo")

    assert [r.full_name for r in await AGENT_INSTRUCTIONS.list_all()] == [
        "acme/apple",
        "acme/zebra",
    ]


@pytest.mark.asyncio
async def test_unknown_stored_fields_are_ignored(fake_store: FakeStore) -> None:
    fake_store.seed(
        AGENT_INSTRUCTIONS_NAMESPACE,
        "acme/repo",
        {"full_name": "acme/repo", "instructions": "rules", "retired_field": "junk"},
    )

    record = await AGENT_INSTRUCTIONS.get("acme/repo")

    assert record is not None
    assert record.instructions == "rules"


def test_construct_system_prompt_contains_only_repository_instructions() -> None:
    prompt = construct_system_prompt(
        working_dir="/work",
        repo_custom_instructions="Repository rule sentinel.",
    )

    assert "Repository rule sentinel." in prompt
    assert "Sender's Custom Instructions" not in prompt


def test_resolve_repo_custom_instructions_returns_none_without_repo() -> None:
    result = asyncio.run(server._resolve_repo_custom_instructions(None))
    assert result is None


@pytest.mark.asyncio
async def test_list_agent_instructions_filters_inaccessible_repos(monkeypatch) -> None:
    monkeypatch.setattr(
        routes.AGENT_INSTRUCTIONS,
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

    monkeypatch.setattr(routes, "require_repo_access_for_user", fake_require_repo_access_for_user)

    result = await routes.api_list_agent_instructions(session={"sub": "octocat"})

    assert result == [AgentInstructions(full_name="acme/visible", instructions="visible")]


@pytest.mark.asyncio
async def test_get_agent_instructions_requires_repo_access(monkeypatch) -> None:
    require_access = AsyncMock(return_value="token")
    monkeypatch.setattr(
        routes.AGENT_INSTRUCTIONS,
        "get",
        AsyncMock(return_value=AgentInstructions(full_name="acme/repo", instructions="rules")),
    )
    monkeypatch.setattr(routes, "require_repo_access_for_user", require_access)

    result = await routes.api_get_agent_instructions(
        "https://github.com/acme/repo", session={"sub": "octocat"}
    )

    assert result == AgentInstructions(full_name="acme/repo", instructions="rules")
    require_access.assert_awaited_once_with("octocat", "acme/repo")


@pytest.mark.asyncio
async def test_delete_agent_instructions_requires_repo_access_before_delete(monkeypatch) -> None:
    delete_instructions = AsyncMock()
    get_instructions = AsyncMock(
        return_value=AgentInstructions(full_name="acme/repo", instructions="rules")
    )
    monkeypatch.setattr(routes.AGENT_INSTRUCTIONS, "get", get_instructions)
    monkeypatch.setattr(
        routes,
        "require_repo_access_for_user",
        AsyncMock(side_effect=HTTPException(403, "no access")),
    )
    monkeypatch.setattr(routes.AGENT_INSTRUCTIONS, "delete", delete_instructions)

    with pytest.raises(HTTPException) as exc:
        await routes.api_delete_agent_instructions("acme/repo", session={"sub": "octocat"})

    assert exc.value.status_code == 403
    get_instructions.assert_not_awaited()
    delete_instructions.assert_not_awaited()
