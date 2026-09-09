from typing import cast
from unittest.mock import AsyncMock

import pytest
from langchain_core.runnables import RunnableConfig

from agent import server


@pytest.mark.asyncio
async def test_workspace_mcp_authorized_gates_on_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin@example.com")
    monkeypatch.delenv("OBSERVABILITY_AUTHORIZED_EMAILS", raising=False)
    monkeypatch.setattr(server, "email_for_login", AsyncMock(return_value=None))

    admin_config = cast(RunnableConfig, {"configurable": {"user_email": "admin@example.com"}})
    other_config = cast(RunnableConfig, {"configurable": {"user_email": "attacker@example.com"}})

    assert await server._workspace_mcp_authorized(admin_config, None) is True
    assert await server._workspace_mcp_authorized(other_config, None) is False


@pytest.mark.asyncio
async def test_workspace_mcp_authorized_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "")
    monkeypatch.setenv("OBSERVABILITY_AUTHORIZED_EMAILS", "trusted@example.com")
    monkeypatch.setattr(server, "email_for_login", AsyncMock(return_value=None))

    config = cast(RunnableConfig, {"configurable": {"user_email": "trusted@example.com"}})
    assert await server._workspace_mcp_authorized(config, None) is True


@pytest.mark.asyncio
async def test_workspace_mcp_authorized_resolves_login_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "dev@example.com")
    monkeypatch.delenv("OBSERVABILITY_AUTHORIZED_EMAILS", raising=False)
    monkeypatch.setattr(
        server,
        "email_for_login",
        AsyncMock(side_effect=lambda login: "dev@example.com" if login else None),
    )

    config = cast(RunnableConfig, {"configurable": {"github_login": "dev"}})
    assert await server._workspace_mcp_authorized(config, "dev") is True


@pytest.mark.asyncio
async def test_workspace_mcp_authorized_accepts_admin_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "dev")
    monkeypatch.delenv("OBSERVABILITY_AUTHORIZED_EMAILS", raising=False)
    monkeypatch.setattr(server, "email_for_login", AsyncMock(return_value=None))

    config = cast(RunnableConfig, {"configurable": {"github_login": "dev"}})
    assert await server._workspace_mcp_authorized(config, "dev") is True


@pytest.mark.asyncio
async def test_workspace_mcp_authorization_is_rechecked_per_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin@example.com")
    monkeypatch.delenv("OBSERVABILITY_AUTHORIZED_EMAILS", raising=False)
    monkeypatch.setattr(server, "email_for_login", AsyncMock(return_value=None))
    load = AsyncMock(return_value=["workspace-tool"])
    monkeypatch.setattr(server, "load_workspace_mcp_tools", load)
    admin = cast(RunnableConfig, {"configurable": {"user_email": "admin@example.com"}})
    attacker = cast(RunnableConfig, {"configurable": {"user_email": "attacker@example.com"}})

    assert await server._workspace_mcp_tools_for(admin, "alice") == ["workspace-tool"]
    assert await server._workspace_mcp_tools_for(attacker, "alice") == []
    load.assert_awaited_once()
