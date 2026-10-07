import importlib
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest

connection = importlib.import_module("openswe.tools.request_service_connection")


@pytest.mark.asyncio
async def test_connection_card_follows_current_surface_and_reports_delivery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://api.example")
    monkeypatch.setattr(
        connection,
        "get_config",
        lambda: {"configurable": {"source": "slack", "thread_id": "thread-1"}},
    )
    post = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(connection, "slack_reply", post)
    assert await connection.request_service_connection("notion", {"reply_surface": "web"}) == {
        "success": True
    }
    post.assert_not_awaited()
    assert await connection.request_service_connection("notion", {"reply_surface": "slack"}) == {
        "success": True
    }
    assert post.await_args is not None
    assert post.await_args.args[1] == "final"
    link = post.await_args.kwargs["blocks"][1]["elements"][0]["url"]
    parsed = urlparse(link)
    assert (parsed.netloc, parsed.path) == ("api.example", "/dashboard/api/notion/login")
    assert parse_qs(parsed.query) == {"redirect_to": ["https://dashboard.example/agents/thread-1"]}
    post.return_value = {"success": False, "error": "not_in_channel"}
    assert await connection.request_service_connection("notion", {"reply_surface": "slack"}) == {
        "success": False,
        "error": "not_in_channel",
    }
