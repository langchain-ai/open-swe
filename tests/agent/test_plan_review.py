from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.tools.errors import ToolError
from tests.conftest import FakeStore


async def test_save_plan_rejects_html_outside_plans_dir() -> None:
    from agent.tools.save_plan import save_plan

    with pytest.raises(ToolError) as raised:
        await save_plan("/workspace/plan.html")
    assert "/workspace/plans" in str(raised.value)


async def test_save_plan_wraps_a_fragment_with_a_title_from_the_filename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    save_plan_tool = importlib.import_module("agent.tools.save_plan")

    saved: dict[str, Any] = {}

    class _Backend:
        async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> dict[str, Any]:
            return {
                "file_data": {
                    "encoding": "utf-8",
                    "content": "<h1>Plan</h1><script>go()</script>",
                }
            }

    async def fake_backend(thread_id: str) -> _Backend:
        return _Backend()

    async def fake_save_content(thread_id: str, **kwargs: Any) -> None:
        saved.update(kwargs)

    monkeypatch.setattr(
        "agent.run_config.get_config", lambda: {"configurable": {"thread_id": "thread-1"}}
    )
    monkeypatch.setattr(save_plan_tool, "get_sandbox_backend", fake_backend)
    monkeypatch.setattr(save_plan_tool, "save_plan_content", fake_save_content)

    result = await save_plan_tool.save_plan("/workspace/plans/2026-06-29-add-webhook-retries.html")

    assert result["success"] is True
    assert saved["html"].startswith("<!doctype html>")
    assert "<title>Add webhook retries</title>" in saved["html"]
    assert "<script>go()</script>" in saved["html"]


async def test_list_workflow_approvals_requires_readable_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from agent.threads import workflow_approval_api

    async def fake_metadata(thread_id: str) -> dict[str, Any]:
        assert thread_id == "thread-1"
        return {"source": "unknown"}

    monkeypatch.setattr(workflow_approval_api, "fetch_thread_metadata", fake_metadata)

    with pytest.raises(HTTPException) as exc:
        await workflow_approval_api.list_workflow_push_approvals(
            "thread-1", {"sub": "octocat", "email": "octo@example.com"}
        )

    assert exc.value.status_code == 404


# --- manual plan editing -------------------------------------------------


async def test_republished_artifact_drops_legacy_approval(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    from agent.threads import plan_api, plan_store

    metadata: dict[str, object] = {
        "source": "slack",
        "plan_status": "approved",
        "plan_approved_by": "reviewer",
        "plan_approved_at": "2026-08-16T12:00:00+00:00",
    }

    async def merge_metadata(thread_id: str, changes: dict[str, object]) -> None:
        metadata.update(changes)

    monkeypatch.setattr(plan_store, "_merge_thread_metadata", merge_metadata)
    monkeypatch.setattr(plan_api, "fetch_thread_metadata", AsyncMock(return_value=metadata))
    await plan_store.save_plan_content("t1", html="<p>Updated artifact</p>")
    result = await plan_api.get_plan(
        "t1", session={"sub": "reviewer", "email": None, "name": "Reviewer"}
    )
    assert result["status"] == "shared"
    assert result["approvedBy"] is None
    assert result["approvedAt"] is None


async def test_dismissal_clears_when_artifact_is_republished(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    from agent.threads import plan_api, plan_store

    session = {"sub": "owner", "email": None}
    monkeypatch.setattr(plan_store, "_merge_thread_metadata", AsyncMock())
    monkeypatch.setattr(
        plan_api,
        "fetch_thread_metadata",
        AsyncMock(return_value={"source": "dashboard", "github_login": "owner"}),
    )
    await plan_store.save_plan_content("t1", html="<p>v1</p>")
    await plan_api.update_plan("t1", plan_api.PlanUpdate(dismissed=True), session=session)
    assert (await plan_api.get_plan("t1", session=session))["dismissed"] is True

    await plan_store.save_plan_content("t1", html="<p>v2</p>")
    assert (await plan_api.get_plan("t1", session=session))["dismissed"] is False


@pytest.mark.parametrize("status", ["ready", "approved", "cancelled", "shared"])
async def test_artifact_comments_remain_authorized_without_approval_gates(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore, status: str
) -> None:
    from agent.threads import plan_api, plan_store

    fake_store.seed(
        plan_store.PLAN_CONTENT_NAMESPACE, "t", {"html": "<p>Report</p>", "status": status}
    )
    monkeypatch.setattr(
        plan_api, "fetch_thread_metadata", AsyncMock(return_value={"source": "slack"})
    )
    author = {"sub": "alice", "name": "Alice"}
    comment = await plan_api.post_plan_comment("t", plan_api.CommentBody(body="Feedback"), author)
    assert (await plan_api.get_plan_comments("t", author))["comments"] == [comment]
    with pytest.raises(HTTPException) as exc:
        await plan_api.remove_plan_comment("t", comment["id"], {"sub": "bob"})
    assert exc.value.status_code == 403
    await plan_api.remove_plan_comment("t", comment["id"], author)
    assert (await plan_api.get_plan_comments("t", author))["comments"] == []


async def test_submit_artifact_comments_dispatches_only_for_comment_author(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    from agent.threads import plan_api, plan_store

    fake_store.seed(plan_store.PLAN_CONTENT_NAMESPACE, "t", {"html": "<p>Report</p>"})
    monkeypatch.setattr(
        plan_api, "fetch_thread_metadata", AsyncMock(return_value={"source": "slack"})
    )
    author = {"sub": "alice", "name": "Alice"}
    await plan_api.post_plan_comment("t", plan_api.CommentBody(body="Feedback"), author)
    dispatch = AsyncMock()
    monkeypatch.setattr(plan_api, "dispatch_agent_run", dispatch)
    monkeypatch.setattr(plan_api, "_ensure_dashboard_github_token", AsyncMock())
    monkeypatch.setattr(plan_api, "_build_dashboard_configurable", AsyncMock(return_value={}))
    monkeypatch.setattr(plan_api, "langgraph_client", lambda: None)

    with pytest.raises(HTTPException) as exc:
        await plan_api.submit_plan_comments("t", {"sub": "bob"})
    assert exc.value.status_code == 422
    dispatch.assert_not_awaited()

    assert await plan_api.submit_plan_comments("t", author) == {"status": "submitted"}
    dispatch.assert_awaited_once()
    assert dispatch.await_args.kwargs["multitask_strategy"] == "enqueue"


async def test_legacy_markdown_artifact_edits_preserve_comments_and_path(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    from agent.threads import plan_api, plan_store

    path = "/workspace/plans/legacy.md"
    fake_store.seed(
        plan_store.PLAN_CONTENT_NAMESPACE,
        "t",
        {
            "markdown": "# Old plan",
            "status": "approved",
            "plan_file_path": path,
        },
    )
    monkeypatch.setattr(
        plan_api, "fetch_thread_metadata", AsyncMock(return_value={"source": "slack"})
    )
    monkeypatch.setattr(plan_store, "_merge_thread_metadata", AsyncMock())
    write = AsyncMock(return_value=path)
    monkeypatch.setattr(plan_api, "write_plan_to_sandbox", write)
    author = {"sub": "alice"}
    comment = await plan_api.post_plan_comment("t", plan_api.CommentBody(body="Keep this"), author)
    result = await plan_api.update_plan("t", plan_api.PlanUpdate(markdown="# Updated"), author)
    assert result == {"status": "shared", "markdown": "# Updated"}
    assert (await plan_api.get_plan("t", author))["markdown"] == "# Updated"
    assert (await plan_api.get_plan_comments("t", author))["comments"] == [comment]
    write.assert_awaited_once_with("t", "# Updated", plan_file_path=path)
