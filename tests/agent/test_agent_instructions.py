import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agent.dashboard import authz, repo_access, routes
from agent.dashboard.agent_instructions import (
    create_agent_instructions,
    get_repo_agent_instructions,
    set_agent_instructions,
)
from agent.dashboard.oauth import COOKIE_NAME, issue_session
from agent.dashboard.routes import agent_instructions as agent_instruction_routes
from agent.graphs import agent as agent_graph
from agent.prompt import construct_system_prompt


def _signed_in_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-with-at-least-32-bytes")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://testserver")
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app, headers={"origin": "http://testserver"})
    client.cookies.set(COOKIE_NAME, issue_session(login="octocat", email=None, avatar_url=None))
    return client


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


def _fake_store_client(stored: dict[str, object] | None) -> MagicMock:
    client = MagicMock()
    client.store.get_item = AsyncMock(return_value={"value": stored} if stored else None)
    client.store.put_item = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_create_agent_instructions_puts_new_record() -> None:
    client = _fake_store_client(None)
    with patch("agent.store.store_client", return_value=client):
        record = await create_agent_instructions("acme/repo", "octo")
    assert record["full_name"] == "acme/repo"
    assert record["instructions"] == ""
    assert record["created_by"] == "octo"
    client.store.put_item.assert_awaited_once_with(["agent_instructions"], "acme/repo", record)


@pytest.mark.asyncio
async def test_set_agent_instructions_updates_store() -> None:
    client = _fake_store_client({"full_name": "acme/repo", "instructions": ""})
    with patch("agent.store.store_client", return_value=client):
        record = await set_agent_instructions("acme/repo", "Use direct tone.")
    assert record["instructions"] == "Use direct tone."
    client.store.put_item.assert_awaited_once_with(["agent_instructions"], "acme/repo", record)


def test_construct_system_prompt_contains_only_repository_instructions() -> None:
    prompt = construct_system_prompt(
        working_dir="/work",
        repo_custom_instructions="Repository rule sentinel.",
    )

    assert "Repository rule sentinel." in prompt
    assert "Sender's Custom Instructions" not in prompt


def test_resolve_repo_custom_instructions_returns_none_without_repo() -> None:
    result = asyncio.run(agent_graph._resolve_repo_custom_instructions(None))
    assert result is None


@pytest.mark.asyncio
async def test_list_agent_instructions_filters_inaccessible_repos(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_instruction_routes,
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

    monkeypatch.setattr(
        repo_access, "require_repo_access_for_user", fake_require_repo_access_for_user
    )

    result = await agent_instruction_routes.api_list_agent_instructions(session={"sub": "octocat"})

    assert result == [{"full_name": "acme/visible", "instructions": "visible"}]


@pytest.mark.asyncio
async def test_get_agent_instructions_requires_repo_access(monkeypatch) -> None:
    """The gate normalizes the path's repo, and hands the endpoint what it proved."""
    require_access = AsyncMock(return_value="token")
    monkeypatch.setattr(
        agent_instruction_routes,
        "get_agent_instructions",
        AsyncMock(return_value={"full_name": "acme/repo", "instructions": "rules"}),
    )
    monkeypatch.setattr(authz, "require_repo_access_for_user", require_access)

    access = await authz.require_repo_full_name_access(
        "https://github.com/acme/repo", {"sub": "octocat"}
    )
    result = await agent_instruction_routes.api_get_agent_instructions(access)

    assert result == {"full_name": "acme/repo", "instructions": "rules"}
    require_access.assert_awaited_once_with("octocat", "acme/repo")


def test_delete_agent_instructions_requires_repo_access_before_delete(monkeypatch) -> None:
    delete_instructions = AsyncMock()
    get_instructions = AsyncMock(return_value={"full_name": "acme/repo", "instructions": "rules"})
    monkeypatch.setattr(agent_instruction_routes, "get_agent_instructions", get_instructions)
    monkeypatch.setattr(
        authz,
        "require_repo_access_for_user",
        AsyncMock(side_effect=HTTPException(403, "no access")),
    )
    monkeypatch.setattr(agent_instruction_routes, "delete_agent_instructions", delete_instructions)

    response = _signed_in_client(monkeypatch).delete("/dashboard/api/agent-instructions/acme/repo")

    assert response.status_code == 403
    get_instructions.assert_not_awaited()
    delete_instructions.assert_not_awaited()
