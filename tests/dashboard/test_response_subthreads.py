from unittest.mock import AsyncMock

import pytest

from openswe.openai_responses.associations import record_guest_thread
from openswe.threads import listing
from openswe.threads.summary import thread_is_unlisted


@pytest.mark.asyncio
async def test_response_guests_are_nested_and_private_guests_are_hidden(monkeypatch, registry_db):
    parent = {"id": "host", "ownerLogin": "alice", "subagents": []}
    guest = {
        "thread_id": "guest",
        "metadata": {
            "sandbox_host_thread_id": "host",
            "owner_login": "alice",
            "responses_client": "codex_cli",
        },
    }
    private = {
        "thread_id": "private",
        "metadata": {"owner_login": "bob", "visibility": "private"},
    }
    child = {
        "id": "guest",
        "title": "Review",
        "status": "finished",
        "createdAt": 1,
        "updatedAt": 2,
    }
    client = AsyncMock()
    client.threads.search.return_value = [guest, private]
    monkeypatch.setattr(listing, "_should_refresh_latest_run", lambda _: False)
    monkeypatch.setattr(listing, "_summarize_thread", AsyncMock(side_effect=[parent, child]))
    monkeypatch.setattr(listing, "attach_subagents", AsyncMock())
    await record_guest_thread("host", "guest")
    await record_guest_thread("host", "guest")
    await record_guest_thread("host", "private")
    summaries = await listing._summarize_threads(client, [{}], viewer_login="alice")
    assert [item["threadId"] for item in summaries[0]["subagents"]] == ["guest"]
    assert summaries[0]["subagents"][0]["title"] == "[codex_cli] Review"
    assert thread_is_unlisted(guest["metadata"])
