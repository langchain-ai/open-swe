from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.dashboard import plan_api, workflow_approval_api
from agent.dashboard.threads import access, api, listing, summary
from agent.tools import threads as tools

_ADMINS = {"admin", "admin@example.com"}


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
        threads=SimpleNamespace(
            get=AsyncMock(return_value=thread),
            update=AsyncMock(),
            create=AsyncMock(),
            get_state=AsyncMock(return_value={"values": {}}),
            update_state=AsyncMock(),
        ),
        runs=SimpleNamespace(cancel_many=AsyncMock()),
    )
    for module in (access, api, listing, tools):
        monkeypatch.setattr(module, "langgraph_client", lambda: client)
    monkeypatch.setattr(api, "_thread_summary", AsyncMock(side_effect=lambda t, **_: t["metadata"]))
    monkeypatch.setattr(
        summary,
        "is_admin",
        lambda email, login=None: bool({email, login} & _ADMINS),
    )
    return thread, client


def test_private_readable_by_owner_and_admin_but_promptable_by_owner_only(private_thread):
    thread, _ = private_thread
    metadata = thread["metadata"]
    assert summary.thread_is_readable(metadata, "ALICE")
    assert summary.thread_is_readable(metadata, "admin")
    assert summary.thread_is_readable(metadata, "someone", "admin@example.com")
    assert not summary.thread_is_readable(metadata, "bob")
    assert not summary.thread_is_readable(metadata, None)
    assert summary.thread_is_promptable(metadata, "alice")
    assert not summary.thread_is_promptable(metadata, "admin")
    assert summary.thread_is_readable({"source": "dashboard"}, "bob")
    assert summary.thread_is_promptable({"source": "dashboard"}, "bob")


@pytest.mark.parametrize(
    "operation",
    [
        api.get_dashboard_thread_state,
        api.get_dashboard_terminal_sandbox,
        api.delete_dashboard_thread,
        api.cancel_dashboard_thread,
    ],
)
async def test_private_routes_deny_nonowner_before_side_effects(private_thread, operation):
    _, client = private_thread
    with pytest.raises(HTTPException) as exc:
        await operation("private-thread", "bob")
    assert exc.value.status_code == 404
    client.threads.update.assert_not_awaited()
    client.runs.cancel_many.assert_not_awaited()


async def test_admin_can_view_but_not_open_terminal(private_thread):
    thread, _ = private_thread
    thread["metadata"]["sandbox_id"] = "sbx"
    assert await access._readable_thread_metadata("private-thread", login="admin") is not None
    with pytest.raises(HTTPException) as exc:
        await api.get_dashboard_terminal_sandbox("private-thread", "admin")
    assert exc.value.status_code == 404
    assert await api.get_dashboard_terminal_sandbox("private-thread", "alice") == ("sbx", None)


async def test_admin_can_read_plan_but_not_approve(private_thread, monkeypatch):
    thread, _ = private_thread
    for module in (plan_api, workflow_approval_api):
        monkeypatch.setattr(
            module, "fetch_thread_metadata", AsyncMock(return_value=thread["metadata"])
        )
    monkeypatch.setattr(plan_api, "get_plan_content", AsyncMock(return_value={}))
    monkeypatch.setattr(plan_api, "list_plan_comments", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        workflow_approval_api, "get_workflow_push_approvals", AsyncMock(return_value={})
    )
    admin = {"sub": "admin"}
    assert await plan_api.get_plan_comments("private-thread", admin) == {"comments": []}
    await workflow_approval_api.list_workflow_push_approvals("private-thread", admin)
    with pytest.raises(HTTPException) as exc:
        await plan_api.approve_plan("private-thread", admin)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await plan_api.reject_plan("private-thread", None, admin)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await workflow_approval_api.approve_workflow_push("private-thread", "fp", admin)
    assert exc.value.status_code == 404


async def test_private_candidates_filtered_before_pagination(private_thread, monkeypatch):
    thread, client = private_thread
    public = {"thread_id": "public-thread", "metadata": {"source": "dashboard"}}
    monkeypatch.setattr(listing, "_search_threads_batch", AsyncMock(return_value=[thread, public]))
    result = await listing._collect_thread_candidates(
        client, [{}], viewer_login="bob", target_per_search=1
    )
    assert [item["thread_id"] for item in result] == ["public-thread"]
    for viewer in ("alice", "admin"):
        result = await listing._collect_thread_candidates(client, [{}], viewer_login=viewer)
        assert len(result) == 2
    result = await listing._collect_thread_candidates(
        client, [{}], viewer_login="alice", include_private=False
    )
    assert [item["thread_id"] for item in result] == ["public-thread"]


async def test_continue_privately_copies_transcript_and_drops_linkage(private_thread):
    thread, client = private_thread
    thread["metadata"] = {
        "source": "slack",
        "origin": "slack",
        "title": "Fix the flaky build",
        "model": "gpt",
        "sandbox_id": "sbx",
        "latest_run_id": "run-1",
        "latest_run_status": "success",
        "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}},
        "participant_logins": {"alice": True, "bob": True},
        "repo_owner": "acme",
        "repo_name": "app",
    }
    client.threads.get_state.return_value = {
        "values": {
            "messages": [
                {"type": "human", "content": "hi", "additional_kwargs": {"x": 1}},
                {"type": "ai", "content": "hello"},
            ]
        }
    }
    await api.continue_thread_privately("private-thread", "Bob", email="bob@x")

    metadata = client.threads.create.call_args.kwargs["metadata"]
    assert metadata["visibility"] == "private"
    assert metadata["owner_type"] == "user"
    assert metadata["owner_login"] == "Bob"
    assert metadata["source"] == metadata["origin"] == "dashboard"
    assert metadata["continued_from_thread_id"] == "private-thread"
    assert metadata["title"] == "Fix the flaky build"
    assert metadata["repo_owner"] == "acme"
    assert metadata["participant_logins"] == {"bob": True}
    assert metadata["graph_id"] == "agent"
    for key in ("source_context", "sandbox_id", "latest_run_id", "latest_run_status"):
        assert key not in metadata
    (state_call,) = client.threads.update_state.await_args_list
    assert state_call.args[0] == client.threads.create.call_args.kwargs["thread_id"]
    copied = state_call.kwargs["values"]["messages"]
    assert [m["content"] for m in copied] == ["hi", "hello"]
    assert all(
        m["additional_kwargs"]["collaborative_origin_thread_id"] == "private-thread" for m in copied
    )
    assert copied[0]["additional_kwargs"]["x"] == 1


async def test_continue_privately_rolls_back_when_copy_fails(private_thread):
    thread, client = private_thread
    thread["metadata"]["visibility"] = "public"
    client.threads.get_state.return_value = {"values": {"messages": [{"type": "human"}]}}
    client.threads.update_state.side_effect = RuntimeError("boom")
    client.threads.delete = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await api.continue_thread_privately("private-thread", "bob")
    assert exc.value.status_code == 502
    client.threads.delete.assert_awaited_once()


async def test_manage_thread_denies_private_thread_outside_private_context(
    private_thread, monkeypatch
):
    thread, _ = private_thread
    cancel = AsyncMock()
    monkeypatch.setattr(tools, "cancel_dashboard_thread", cancel)
    monkeypatch.setattr(
        tools,
        "get_dashboard_thread",
        AsyncMock(return_value={"id": "private-thread", "visibility": "private"}),
    )
    monkeypatch.setattr(tools, "_config", lambda: {"configurable": {"thread_id": "current"}})
    actor = tools._Actor(login="alice", email=None, name="alice")
    monkeypatch.setattr(tools, "_actor", AsyncMock(return_value=actor))

    thread["metadata"]["visibility"] = "public"
    result = await tools.manage_thread("private-thread", "cancel")
    assert result == {"success": False, "error": "thread not found", "status_code": 404}
    cancel.assert_not_awaited()

    thread["metadata"]["visibility"] = "private"
    cancel.return_value = {"id": "private-thread", "metadata": thread["metadata"]}
    result = await tools.manage_thread("private-thread", "cancel")
    assert result["success"] is True
    cancel.assert_awaited_once()


async def test_admin_cancel_reaches_private_thread_without_exporting_details(
    private_thread, monkeypatch
):
    cancel = AsyncMock(return_value={"id": "private-thread", "title": "Secret", "status": "idle"})
    monkeypatch.setattr(tools, "admin_cancel_dashboard_thread", cancel)
    monkeypatch.setattr(
        tools,
        "get_dashboard_thread",
        AsyncMock(
            return_value={"id": "private-thread", "visibility": "private", "title": "Secret"}
        ),
    )
    monkeypatch.setattr(tools, "_config", lambda: {"configurable": {"thread_id": "current"}})
    monkeypatch.setattr(tools, "is_admin", lambda email, login=None: login == "admin")
    monkeypatch.setattr(
        tools, "_actor", AsyncMock(return_value=tools._Actor(login="admin", email=None, name="a"))
    )

    result = await tools.manage_thread("private-thread", "admin_cancel")

    assert result == {"success": True, "thread": {"id": "private-thread", "status": "idle"}}
    cancel.assert_awaited_once_with("private-thread", "admin", email=None)


async def test_email_admin_can_cancel_private_thread(private_thread, monkeypatch):
    monkeypatch.setattr(api, "_cancel_active_thread_runs", AsyncMock())
    with pytest.raises(HTTPException):
        await api.admin_cancel_dashboard_thread("private-thread", "someone")
    await api.admin_cancel_dashboard_thread("private-thread", "someone", email="admin@example.com")
