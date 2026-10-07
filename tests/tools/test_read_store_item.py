from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.tools.read_store_item import read_store_item


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("admin", "visibility", "allowed"),
    [(True, "private", True), (False, "private", False), (True, "public", False)],
)
async def test_store_read_requires_current_admin_and_private_surface(
    monkeypatch: pytest.MonkeyPatch, admin: bool, visibility: str, allowed: bool
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    client = SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(
                return_value={"metadata": {"visibility": visibility, "owner_type": "user"}}
            )
        )
    )
    monkeypatch.setattr("agent.tools.access.langgraph_sdk.get_client", lambda: client)
    config = {
        "configurable": {
            "thread_id": "t-1",
            "source": "dashboard",
            "admin_thread": True,
            "github_login": "admin" if admin else "not-admin",
        }
    }
    read = AsyncMock(return_value={"setting": "stored"})
    with (
        patch("agent.run_config.get_config", return_value=config),
        patch("agent.tools.read_store_item.get_value", read),
    ):
        result = await read_store_item(["arbitrary", "nested"], "item")
    assert result["ok"] is allowed
    if allowed:
        assert result["value"] == {"setting": "stored"}
        assert result["found"] is True
    else:
        read.assert_not_awaited()
