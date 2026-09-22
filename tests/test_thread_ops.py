import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from langgraph_sdk.client import LangGraphClient

from agent.utils import thread_ops


@pytest.mark.asyncio
async def test_queue_message_for_thread_deduplicates_queue_id(monkeypatch) -> None:
    existing = {
        "content": {
            "queue_id": "8a60896d-65ca-4e40-8a2d-1fbe81777001",
            "text": "follow up",
        }
    }
    client = MagicMock()
    client.store.get_item = AsyncMock(return_value={"value": {"messages": [existing]}})
    client.store.put_item = AsyncMock()
    monkeypatch.setattr(thread_ops, "langgraph_client", lambda: client)

    queued = await thread_ops.queue_message_for_thread(
        "thread-1",
        {
            "queue_id": "8a60896d-65ca-4e40-8a2d-1fbe81777001",
            "text": "follow up",
        },
    )

    assert queued is True
    client.store.put_item.assert_not_awaited()


async def test_active_status_reads_only_status(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        thread = {"thread_id": "thread-1", "status": "busy"}
        return httpx.Response(200, json=[thread] if request.method == "POST" else thread)

    async with httpx.AsyncClient(
        base_url="http://langgraph.test", transport=httpx.MockTransport(handle)
    ) as http:
        monkeypatch.setattr(thread_ops, "langgraph_client", lambda: LangGraphClient(http))
        assert await thread_ops.get_thread_active_status("thread-1") is True
    assert len(requests) == 1
    assert requests[0].url.path == "/threads/search"
    assert json.loads(requests[0].content) == {
        "ids": ["thread-1"],
        "select": ["status"],
        "limit": 1,
        "offset": 0,
    }


@pytest.mark.parametrize("status", [403, 404])
async def test_missing_or_forbidden_status_remains_unknown(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=[])
        return httpx.Response(status, json={"detail": "unavailable"})

    async with httpx.AsyncClient(
        base_url="http://langgraph.test", transport=httpx.MockTransport(handle)
    ) as http:
        monkeypatch.setattr(thread_ops, "langgraph_client", lambda: LangGraphClient(http))
        assert await thread_ops.get_thread_active_status("thread-1") is None
