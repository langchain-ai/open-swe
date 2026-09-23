from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent import store as agent_store
from tests.conftest import FakeStore


def test_dashboard_plan_url_uses_plan_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://example.test")
    from agent.utils.dashboard_links import dashboard_plan_url

    assert dashboard_plan_url("abc-123") == "https://example.test/agents/abc-123/plan"


def test_dashboard_plan_url_none_without_thread() -> None:
    from agent.utils.dashboard_links import dashboard_plan_url

    assert dashboard_plan_url("") is None


def test_comment_anchor_validation() -> None:
    from agent.threads.plan_api import CommentBody, TextAnchor

    anchor = TextAnchor(
        exact="text",
        prefix="",
        suffix="",
        context_before="one\ntwo\nthree",
        context_after="four\nfive\nsix",
        start=0,
        end=4,
    )
    assert anchor.context_before == "one\ntwo\nthree"
    with pytest.raises(ValueError, match="anchor range"):
        CommentBody(
            body="comment",
            anchor=TextAnchor(exact="text", prefix="", suffix="", start=0, end=3),
        )


def _fake_client(store: Any) -> Any:
    return type("C", (), {"store": store})()


async def test_list_plan_comments_swallows_errors_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.threads import plan_store

    class _Store:
        async def search_items(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("boom")

    monkeypatch.setattr(agent_store, "store_client", lambda: _fake_client(_Store()))
    assert await plan_store.list_plan_comments("t") == []


async def test_list_plan_comments_raises_with_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.threads import plan_store

    class _Store:
        async def search_items(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("boom")

    monkeypatch.setattr(agent_store, "store_client", lambda: _fake_client(_Store()))
    with pytest.raises(RuntimeError):
        await plan_store.list_plan_comments("t", raise_on_error=True)


async def test_clear_plan_comments_deletes_each(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.threads import plan_store

    deleted: list[str] = []

    class _Store:
        async def search_items(self, *a: Any, **k: Any) -> Any:
            return {"items": [{"value": {"id": "a"}}, {"value": {"id": "b"}}]}

        async def delete_item(self, _ns: Any, key: str) -> None:
            deleted.append(key)

    monkeypatch.setattr(agent_store, "store_client", lambda: _fake_client(_Store()))
    await plan_store.clear_plan_comments("t")
    assert deleted == ["a", "b"]


async def test_save_plan_requires_run_context() -> None:
    from agent.tools.save_plan import save_plan

    with pytest.raises(RuntimeError, match="outside of a runnable context"):
        await save_plan("/workspace/plans/2026-06-29-test-plan.html")


async def test_save_plan_rejects_empty_path() -> None:
    from agent.tools.save_plan import save_plan

    result = await save_plan("   ")
    assert result["success"] is False
    assert "empty" in result["error"]


async def test_save_plan_rejects_non_html_path() -> None:
    from agent.tools.save_plan import save_plan

    result = await save_plan("/workspace/plans/plan.txt")
    assert result["success"] is False
    assert "HTML" in result["error"]


async def test_save_plan_rejects_html_outside_plans_dir() -> None:
    from agent.tools.save_plan import save_plan

    result = await save_plan("/workspace/plan.html")
    assert result["success"] is False
    assert "/workspace/plans" in result["error"]


async def test_save_plan_reads_html_file_from_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    save_plan_tool = importlib.import_module("agent.tools.save_plan")

    saved: dict[str, Any] = {}
    reads: list[tuple[str, int, int]] = []

    class _Backend:
        async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> dict[str, Any]:
            reads.append((file_path, offset, limit))
            return {
                "file_data": {
                    "encoding": "utf-8",
                    "content": "<!doctype html><html><head><title>Plan</title></head><body><h1>Plan</h1></body></html>",
                }
            }

    async def fake_backend(thread_id: str) -> _Backend:
        assert thread_id == "thread-1"
        return _Backend()

    async def fake_save_content(
        thread_id: str,
        *,
        html: str,
        status: str,
        plan_file_path: str | None = None,
    ) -> None:
        saved.update(
            thread_id=thread_id,
            html=html,
            status=status,
            plan_file_path=plan_file_path,
        )

    monkeypatch.setattr(
        "agent.run_config.get_config", lambda: {"configurable": {"thread_id": "thread-1"}}
    )
    monkeypatch.setattr(save_plan_tool, "get_sandbox_backend", fake_backend)
    monkeypatch.setattr(save_plan_tool, "save_plan_content", fake_save_content)

    result = await save_plan_tool.save_plan("/workspace/plans/2026-06-29-test-plan.html")

    assert result == {"success": True, "path": "/workspace/plans/2026-06-29-test-plan.html"}
    assert reads == [
        ("/workspace/plans/2026-06-29-test-plan.html", 0, save_plan_tool._MAX_PLAN_LINES)
    ]
    assert saved == {
        "thread_id": "thread-1",
        "html": "<!doctype html><html><head><title>Plan</title></head><body><h1>Plan</h1></body></html>",
        "status": "shared",
        "plan_file_path": "/workspace/plans/2026-06-29-test-plan.html",
    }


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


def test_plan_routes_registered() -> None:
    from agent.webapp import app

    def route_paths(routes: list[Any]) -> set[str]:
        paths: set[str] = set()
        for route in routes:
            path = getattr(route, "path", None)
            if isinstance(path, str):
                paths.add(path)
            original_router = getattr(route, "original_router", None)
            nested_routes = getattr(original_router, "routes", None)
            if nested_routes:
                paths.update(route_paths(nested_routes))
        return paths

    paths = route_paths(app.routes)
    assert "/dashboard/api/plan/{thread_id}" in paths
    assert "/dashboard/api/plan/{thread_id}/approve" not in paths
    assert "/dashboard/api/plan/{thread_id}/reject" not in paths
    assert "/dashboard/api/plan/{thread_id}/comments" in paths
    assert "/dashboard/api/plan/{thread_id}/comments/{comment_id}" in paths
    assert "/dashboard/api/plan/yjs/{thread_id}" not in paths
    assert "/dashboard/api/workflow-approval/{thread_id}" in paths
    assert "/dashboard/api/workflow-approval/{thread_id}/{fingerprint}/approve" in paths
    assert "/dashboard/api/workflow-approval/{thread_id}/{fingerprint}/reject" in paths


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


async def test_list_workflow_approvals_returns_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.threads import workflow_approval_api

    async def fake_metadata(thread_id: str) -> dict[str, Any]:
        assert thread_id == "thread-1"
        return {"source": "slack", "github_login": "octocat"}

    async def fake_approvals(thread_id: str) -> dict[str, dict[str, Any]]:
        assert thread_id == "thread-1"
        return {
            "abc": {
                "fingerprint": "abc",
                "status": "pending",
                "files": [".github/workflows/ci.yml"],
                "diff_stats": {"files": 1, "additions": 1, "deletions": 0},
            }
        }

    monkeypatch.setattr(workflow_approval_api, "fetch_thread_metadata", fake_metadata)
    monkeypatch.setattr(workflow_approval_api, "get_workflow_push_approvals", fake_approvals)

    result = await workflow_approval_api.list_workflow_push_approvals(
        "thread-1", {"sub": "octocat", "email": "octo@example.com"}
    )

    assert result["approvals"][0]["fingerprint"] == "abc"
    assert result["approvals"][0]["diffStats"] == {"files": 1, "additions": 1, "deletions": 0}


def test_plan_file_path_for_thread_uses_plans_dir_and_slug() -> None:
    from agent.threads import plan_store

    path = plan_store.plan_file_path_for_thread("Thread ABC/123")
    assert path.startswith("/workspace/plans/")
    assert path.endswith("-thread-abc-123.html")


# --- manual plan editing -------------------------------------------------


async def test_save_plan_content_clear_comments_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.threads import plan_store

    cleared: list[str] = []

    class _Store:
        async def put_item(self, *a: Any, **k: Any) -> None:
            return None

    async def fake_clear(thread_id: str) -> None:
        cleared.append(thread_id)

    async def fake_merge(thread_id: str, metadata: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(agent_store, "store_client", lambda: _fake_client(_Store()))
    monkeypatch.setattr(plan_store, "clear_plan_comments", fake_clear)
    monkeypatch.setattr(plan_store, "_merge_thread_metadata", fake_merge)

    # A manual edit keeps reviewer comments; the agent's republish clears them.
    await plan_store.save_plan_content("t", html="x", clear_comments=False)
    assert cleared == []
    await plan_store.save_plan_content("t", html="x")
    assert cleared == ["t"]


async def test_save_plan_content_publishes_shared_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.threads import plan_store

    merged: dict[str, Any] = {}

    class _Store:
        async def put_item(self, *a: Any, **k: Any) -> None:
            return None

    async def fake_clear(thread_id: str) -> None:
        return None

    async def fake_merge(thread_id: str, metadata: dict[str, Any]) -> None:
        merged.update(metadata)

    monkeypatch.setattr(agent_store, "store_client", lambda: _fake_client(_Store()))
    monkeypatch.setattr(plan_store, "clear_plan_comments", fake_clear)
    monkeypatch.setattr(plan_store, "_merge_thread_metadata", fake_merge)

    await plan_store.save_plan_content("t", html="x")

    assert merged == {
        "plan_status": "shared",
        "plan_approved_by": None,
        "plan_approved_at": None,
    }


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


async def test_get_plan_returns_approval_attribution(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.threads import plan_api

    async def fake_meta(thread_id: str) -> dict[str, Any]:
        return {"source": "slack", "github_login": "owner"}

    async def fake_content(thread_id: str) -> dict[str, Any]:
        return {
            "markdown": "# Plan",
            "status": "approved",
            "approved_by": {"id": "reviewer", "name": "Reviewer", "source": "dashboard"},
            "approved_at": "2026-08-16T12:00:00+00:00",
        }

    monkeypatch.setattr(plan_api, "fetch_thread_metadata", fake_meta)
    monkeypatch.setattr(plan_api, "get_plan_content", fake_content)

    result = await plan_api.get_plan(
        "t1",
        session={"sub": "reviewer", "email": None, "name": "Reviewer"},
    )

    assert result["approvedBy"] == {
        "id": "reviewer",
        "name": "Reviewer",
        "source": "dashboard",
    }
    assert result["approvedAt"] == "2026-08-16T12:00:00+00:00"


def _patch_update_plan_deps(
    monkeypatch: pytest.MonkeyPatch,
    *,
    metadata: dict[str, Any],
    content: dict[str, Any],
    saved: dict[str, Any],
    sandbox: dict[str, Any],
) -> None:
    from agent.threads import plan_api

    async def fake_meta(thread_id: str) -> dict[str, Any]:
        return metadata

    async def fake_get_content(thread_id: str) -> dict[str, Any]:
        return content

    async def fake_save(
        thread_id: str,
        *,
        html: str,
        status: str,
        clear_comments: bool = True,
        plan_file_path: str | None = None,
    ) -> None:
        saved.update(
            html=html,
            status=status,
            clear_comments=clear_comments,
            plan_file_path=plan_file_path,
        )

    async def fake_write(thread_id: str, c: str, *, plan_file_path: str | None = None) -> str:
        sandbox["content"] = c
        sandbox["plan_file_path"] = plan_file_path
        return plan_file_path or "/workspace/plans/fallback.html"

    monkeypatch.setattr(plan_api, "fetch_thread_metadata", fake_meta)
    monkeypatch.setattr(plan_api, "get_plan_content", fake_get_content)
    monkeypatch.setattr(plan_api, "save_plan_content", fake_save)
    monkeypatch.setattr(plan_api, "write_plan_to_sandbox", fake_write)


@pytest.mark.parametrize("status", ["ready", "approved", "cancelled", "shared"])
async def test_update_plan_saves_and_mirrors_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    from agent.threads import plan_api

    saved: dict[str, Any] = {}
    sandbox: dict[str, Any] = {}
    _patch_update_plan_deps(
        monkeypatch,
        metadata={"plan_status": status},
        content={
            "html": "old",
            "status": status,
            "plan_file_path": "/workspace/plans/2026-06-29-existing.html",
        },
        saved=saved,
        sandbox=sandbox,
    )

    result = await plan_api.update_plan(
        "t1", plan_api.PlanUpdate(html="# New\n\ndo x"), session={"sub": "a", "email": None}
    )
    assert result == {"status": "shared", "html": "# New\n\ndo x"}
    assert saved["status"] == "shared"
    assert saved["clear_comments"] is False
    assert saved["plan_file_path"] == "/workspace/plans/2026-06-29-existing.html"
    assert sandbox["content"] == "# New\n\ndo x"
    assert sandbox["plan_file_path"] == "/workspace/plans/2026-06-29-existing.html"


async def test_update_plan_rejects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    from agent.threads import plan_api

    _patch_update_plan_deps(monkeypatch, metadata={}, content={}, saved={}, sandbox={})
    with pytest.raises(HTTPException) as exc:
        await plan_api.update_plan(
            "t1", plan_api.PlanUpdate(html="   "), session={"sub": "a", "email": None}
        )
    assert exc.value.status_code == 422


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
