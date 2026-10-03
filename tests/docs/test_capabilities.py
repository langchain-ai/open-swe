"""Docs sandbox capabilities cannot reach the unrestricted coding tool API."""

from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import HTTPException

from agent.sandboxes import tool_access


@pytest.mark.parametrize(
    "metadata", [{"agent_kind": "docs"}, {"agent_kind": "reviewer", "docs_context": True}]
)
async def test_docs_cannot_use_a_coding_tool_capability(
    monkeypatch: pytest.MonkeyPatch, metadata: dict[str, object]
) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "docs-test-signing-key-32-characters")
    client = MagicMock()
    client.threads.get = AsyncMock(return_value={"metadata": {**metadata, "sandbox_id": "box"}})
    monkeypatch.setattr(tool_access, "get_client", lambda: client)
    capability = jwt.encode(
        {"aud": tool_access.TOOLS_AUDIENCE, "thread_id": "thread", "sandbox_id": "box"},
        "docs-test-signing-key-32-characters",
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as error:
        await tool_access.authenticate_tool_access(capability)
    assert error.value.status_code == 403
    monkeypatch.setattr(
        tool_access,
        "issue_tool_access",
        AsyncMock(return_value=("https://example.com/tools", "capability")),
    )
    monkeypatch.setattr(tool_access, "sandbox_host_thread_id", AsyncMock(return_value="thread"))
    assert await tool_access.tool_proxy_rule("thread", "box") is None
