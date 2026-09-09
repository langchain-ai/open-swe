import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.dashboard import plan_api, workflow_approval_api
from agent.dashboard.threads import access, api, listing, proxy, summary
from agent.tools import threads as tools


@pytest.fixture
def private_thread(monkeypatch):
    thread = {
        "thread_id": "private-thread",
        "status": "idle",
        "metadata": {
            "source": "dashboard",
            "visibility": "private",
            "owner_login": "alice",
            "participant_logins": {"alice": True, "bob": True},
        },
    }
    client = SimpleNamespace(
        threads=SimpleNamespace(get=AsyncMock(return_value=thread), update=AsyncMock()),
        runs=SimpleNamespace(cancel_many=AsyncMock()),
    )
    for module in (access, api, listing, proxy, tools):
        monkeypatch.setattr(module, "langgraph_client", lambda: client)
    monkeypatch.setattr(api, "_thread_summary", AsyncMock(side_effect=lambda t: t["metadata"]))
    monkeypatch.setattr(summary, "is_admin", lambda *args, **kwargs: True)
    return thread, client


@pytest.mark.parametrize("login", [None, "bob", "admin"])
def test_private_access_ignores_participation_and_admin(private_thread, login):
    thread, _ = private_thread
    assert not summary.thread_is_readable(thread["metadata"], login)
    assert summary.thread_is_readable(thread["metadata"], "ALICE")
    assert summary.thread_is_readable({"source": "dashboard"}, login)
    assert not summary.thread_is_readable({"source": "dashboard", "visibility": "private"}, "alice")


@pytest.mark.parametrize(
    "operation",
    [
        access._authorized_thread,
        api.get_dashboard_thread,
        api.get_dashboard_thread_state,
        api.get_dashboard_terminal_sandbox,
        api.delete_dashboard_thread,
        api.cancel_dashboard_thread,
        api.admin_cancel_dashboard_thread,
    ],
)
async def test_private_routes_deny_before_side_effects(private_thread, operation):
    _, client = private_thread
    with pytest.raises(HTTPException) as exc:
        await operation("private-thread", "bob")
    assert exc.value.status_code == 404
    client.threads.update.assert_not_awaited()
    client.runs.cancel_many.assert_not_awaited()


@pytest.mark.parametrize(
    "operation",
    [
        plan_api.get_plan,
        plan_api.get_plan_comments,
        workflow_approval_api.list_workflow_push_approvals,
    ],
)
async def test_private_artifacts_deny_nonowner(private_thread, monkeypatch, operation):
    thread, _ = private_thread
    for module in (plan_api, workflow_approval_api):
        monkeypatch.setattr(
            module, "fetch_thread_metadata", AsyncMock(return_value=thread["metadata"])
        )
    with pytest.raises(HTTPException) as exc:
        await operation("private-thread", {"sub": "bob"})
    assert exc.value.status_code == 404


async def test_visibility_is_owner_only_and_irreversible(private_thread):
    thread, client = private_thread
    thread["metadata"]["visibility"] = "public"
    with pytest.raises(HTTPException) as exc:
        await api.rename_dashboard_thread("private-thread", "bob", visibility="private")
    assert exc.value.status_code == 403
    result = await api.rename_dashboard_thread("private-thread", "alice", visibility="private")
    assert result["visibility"] == "private"
    thread["metadata"]["visibility"] = "private"
    client.threads.update.reset_mock()
    with pytest.raises(HTTPException) as exc:
        await api.rename_dashboard_thread("private-thread", "alice", visibility="public")
    assert exc.value.status_code == 409
    client.threads.update.assert_not_awaited()


async def test_private_candidates_filtered_before_pagination(private_thread, monkeypatch):
    thread, client = private_thread
    public = {"thread_id": "public-thread", "metadata": {"source": "dashboard"}}
    monkeypatch.setattr(listing, "_search_threads_batch", AsyncMock(return_value=[thread, public]))
    result = await listing._collect_thread_candidates(
        client, [{}], viewer_login="bob", target_per_search=1
    )
    assert [item["thread_id"] for item in result] == ["public-thread"]
    result = await listing._collect_thread_candidates(client, [{}], viewer_login="alice")
    assert len(result) == 2
    result = await listing._collect_thread_candidates(
        client, [{}], viewer_login="alice", include_private=False
    )
    assert [item["thread_id"] for item in result] == ["public-thread"]


async def test_private_fork_is_rejected_even_for_owner(private_thread):
    with pytest.raises(HTTPException) as exc:
        await proxy.proxy_dashboard_thread_commands(
            "private-thread", "alice", json.dumps({"method": "state.fork"}).encode()
        )
    assert exc.value.status_code == 403


async def test_tools_do_not_export_private_content_into_public_thread(private_thread, monkeypatch):
    thread, _ = private_thread
    monkeypatch.setattr(
        tools, "get_dashboard_thread", AsyncMock(return_value={"visibility": "private"})
    )
    monkeypatch.setattr(tools, "_config", lambda: {"configurable": {"thread_id": "current"}})
    actor = tools._Actor(login="alice", email=None, name="alice")
    thread["metadata"]["visibility"] = "public"
    with pytest.raises(HTTPException) as exc:
        await tools._authorized_locator("private-thread", actor)
    assert exc.value.status_code == 404
    thread["metadata"]["visibility"] = "private"
    assert (await tools._authorized_locator("private-thread", actor))[0] == "private-thread"
