import json

import httpx
import pytest
from fastapi import HTTPException
from langgraph_sdk.client import LangGraphClient

from agent.threads import access, listing


@pytest.mark.parametrize("authorized", [False, True])
async def test_metadata_authorization_does_not_load_checkpoint_state(
    monkeypatch: pytest.MonkeyPatch, authorized: bool
) -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/threads/search"
        payload = json.loads(request.content)
        assert payload["ids"] == ["private-thread"]
        assert "values" not in payload["select"]
        return httpx.Response(
            200,
            json=[
                {
                    "metadata": {
                        "source": "dashboard",
                        "visibility": "private",
                        "owner_login": "alice",
                    }
                }
            ],
        )

    async with httpx.AsyncClient(
        base_url="https://runtime.test", transport=httpx.MockTransport(handle)
    ) as http:
        client = LangGraphClient(http)
        monkeypatch.setattr(access, "langgraph_client", lambda: client)
        if authorized:
            metadata = await access._authorized_thread_metadata("private-thread", "alice")
        else:
            metadata = await access._readable_thread_metadata("private-thread", login="alice")
        assert metadata["owner_login"] == "alice"
        with pytest.raises(HTTPException) as error:
            await access._readable_thread_metadata("private-thread", login="bob")
        assert error.value.status_code == 404


async def test_pins_batch_metadata_reads_preserving_order_and_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def pins(login: str) -> list[str]:
        return ["second", "private", "deleted", "first"]

    async def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/threads/search"
        payload = json.loads(request.content)
        assert set(payload["ids"]) == {"second", "private", "deleted", "first"}
        assert "values" not in payload["select"]
        return httpx.Response(
            200,
            json=[
                {
                    "thread_id": thread_id,
                    "metadata": {
                        "source": "dashboard",
                        "visibility": "private" if thread_id == "private" else "public",
                        "owner_login": "someone-else",
                        "latest_run_status": "success",
                    },
                }
                for thread_id in ["first", "private", "second"]
            ],
        )

    async with httpx.AsyncClient(
        base_url="https://runtime.test", transport=httpx.MockTransport(handle)
    ) as http:
        client = LangGraphClient(http)
        monkeypatch.setattr(listing, "langgraph_client", lambda: client)
        monkeypatch.setattr(listing, "list_thread_pin_ids", pins)
        result = await listing.list_dashboard_pinned_threads("alice")

    assert [item["id"] for item in result] == ["second", "first"]


async def test_repository_discovery_reads_all_matches_in_bulk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = [
        {
            "thread_id": f"thread-{index}",
            "metadata": {"source": "dashboard", "repo_owner": "acme", "repo_name": f"r{index}"},
        }
        for index in range(250)
    ]
    searches = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal searches
        searches += 1
        payload = json.loads(request.content)
        assert "values" not in payload["select"]
        offset = payload["offset"]
        return httpx.Response(200, json=threads[offset : offset + payload["limit"]])

    async def workspace(owner: str, repo: str) -> None:
        return None

    async with httpx.AsyncClient(
        base_url="https://runtime.test", transport=httpx.MockTransport(handle)
    ) as http:
        client = LangGraphClient(http)
        monkeypatch.setattr(listing, "langgraph_client", lambda: client)
        monkeypatch.setattr(listing, "workspace_for_repo", workspace)
        result = await listing.list_dashboard_thread_repos("alice", include_all=True)

    assert {item["repoFullName"] for item in result} == {f"acme/r{i}" for i in range(250)}
    assert searches == 1
