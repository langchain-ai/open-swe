"""Who may attach a cloud terminal to a thread's sandbox.

The route on top of this is covered by ``test_cloud_terminal.py``; what is
asserted here is the lookup it depends on.
"""

from typing import Any

import pytest
from fastapi import HTTPException
from support.langgraph_fakes import FakeLangGraphClient

from agent.dashboard import authz
from agent.dashboard.threads import sandbox as thread_sandbox


async def test_terminal_sandbox_requires_owner_and_existing_sandbox(monkeypatch) -> None:
    metadata: dict[str, Any] = {
        "source": "dashboard",
        "github_login": "owner",
        "sandbox_id": "sandbox-123",
        "repo_name": "repo",
    }
    client = FakeLangGraphClient(thread_metadata=metadata)
    monkeypatch.setattr(authz, "langgraph_client", lambda: client)

    assert await thread_sandbox.get_dashboard_terminal_sandbox("tid", "owner") == (
        "sandbox-123",
        "repo",
    )
    with pytest.raises(HTTPException) as exc_info:
        await thread_sandbox.get_dashboard_terminal_sandbox("tid", "intruder")
    assert exc_info.value.status_code == 404

    metadata["sandbox_id"] = "__creating__"
    with pytest.raises(HTTPException) as exc_info:
        await thread_sandbox.get_dashboard_terminal_sandbox("tid", "owner")
    assert exc_info.value.status_code == 404
