from __future__ import annotations

from typing import Any, cast

import pytest


def test_dashboard_plan_url_uses_plan_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://example.test")
    from agent.utils.dashboard_links import dashboard_plan_url

    assert dashboard_plan_url("abc-123") == "https://example.test/agents/abc-123/plan"


def test_dashboard_plan_url_none_without_thread() -> None:
    from agent.utils.dashboard_links import dashboard_plan_url

    assert dashboard_plan_url("") is None


def test_format_comments_numbers_and_skips_blank() -> None:
    from agent.dashboard.plan_api import _format_comments

    text = _format_comments(
        [
            {"author": "alice", "body": "add a docstring"},
            {"author": "bob", "body": "looks good"},
            {"author": "carol", "body": "   "},  # blank → skipped
        ]
    )
    assert "1. alice: add a docstring" in text
    assert "2. bob: looks good" in text
    assert "carol" not in text


def test_format_comments_empty() -> None:
    from agent.dashboard.plan_api import _format_comments

    assert _format_comments([]) == ""


def test_plan_approved_slack_text_mentions_comments_actor_and_start() -> None:
    from agent.dashboard.plan_api import _plan_approved_slack_text

    assert (
        _plan_approved_slack_text(2, "Alice")
        == "Plan approved with 2 comments by Alice\nbeginning implementation"
    )


def test_plan_comment_helpers_exported() -> None:
    from agent.dashboard import plan_store

    assert plan_store.PLAN_COMMENTS_NAMESPACE == ["plan", "comments"]
    assert callable(plan_store.add_plan_comment)
    assert callable(plan_store.list_plan_comments)
    assert callable(plan_store.delete_plan_comment)
    assert callable(plan_store.clear_plan_comments)


def _fake_client(store: Any) -> Any:
    return type("C", (), {"store": store})()


async def test_list_plan_comments_swallows_errors_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.dashboard import plan_store

    class _Store:
        async def search_items(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("boom")

    monkeypatch.setattr(plan_store, "_client", lambda: _fake_client(_Store()))
    assert await plan_store.list_plan_comments("t") == []


async def test_list_plan_comments_raises_with_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.dashboard import plan_store

    class _Store:
        async def search_items(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("boom")

    monkeypatch.setattr(plan_store, "_client", lambda: _fake_client(_Store()))
    with pytest.raises(RuntimeError):
        await plan_store.list_plan_comments("t", raise_on_error=True)


async def test_clear_plan_comments_deletes_each(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.dashboard import plan_store

    deleted: list[str] = []

    class _Store:
        async def search_items(self, *a: Any, **k: Any) -> Any:
            return {"items": [{"value": {"id": "a"}}, {"value": {"id": "b"}}]}

        async def delete_item(self, _ns: Any, key: str) -> None:
            deleted.append(key)

    monkeypatch.setattr(plan_store, "_client", lambda: _fake_client(_Store()))
    await plan_store.clear_plan_comments("t")
    assert deleted == ["a", "b"]


async def test_save_plan_requires_run_context() -> None:
    from agent.tools.save_plan import save_plan

    # No LangGraph run context → no thread_id → graceful error, not a crash.
    result = await save_plan("/workspace/plans/2026-06-29-test-plan.md")
    assert result["success"] is False
    assert "thread_id" in result["error"]


async def test_save_plan_rejects_empty_path() -> None:
    from agent.tools.save_plan import save_plan

    result = await save_plan("   ")
    assert result["success"] is False
    assert "empty" in result["error"]


async def test_save_plan_rejects_non_markdown_path() -> None:
    from agent.tools.save_plan import save_plan

    result = await save_plan("/workspace/plans/plan.txt")
    assert result["success"] is False
    assert "Markdown" in result["error"]


async def test_save_plan_rejects_markdown_outside_plans_dir() -> None:
    from agent.tools.save_plan import save_plan

    result = await save_plan("/workspace/plan.md")
    assert result["success"] is False
    assert "/workspace/plans" in result["error"]


async def test_save_plan_reads_markdown_file_from_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    save_plan_tool = importlib.import_module("agent.tools.save_plan")

    saved: dict[str, Any] = {}
    reads: list[tuple[str, int, int]] = []

    class _Backend:
        async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> dict[str, Any]:
            reads.append((file_path, offset, limit))
            return {"file_data": {"encoding": "utf-8", "content": "# Plan\n\nDo it.\n"}}

    async def fake_backend(thread_id: str) -> _Backend:
        assert thread_id == "thread-1"
        return _Backend()

    async def fake_save_content(
        thread_id: str,
        *,
        markdown: str,
        status: str,
        plan_file_path: str | None = None,
        plan_mode: bool | None = True,
    ) -> None:
        saved.update(
            thread_id=thread_id,
            markdown=markdown,
            status=status,
            plan_file_path=plan_file_path,
            plan_mode=plan_mode,
        )

    monkeypatch.setattr(
        save_plan_tool,
        "get_config",
        lambda: {"configurable": {"thread_id": "thread-1"}},
    )
    monkeypatch.setattr(save_plan_tool, "get_sandbox_backend", fake_backend)
    monkeypatch.setattr(save_plan_tool, "save_plan_content", fake_save_content)

    result = await save_plan_tool.save_plan("/workspace/plans/2026-06-29-test-plan.md")

    assert result == {"success": True, "path": "/workspace/plans/2026-06-29-test-plan.md"}
    assert reads == [
        ("/workspace/plans/2026-06-29-test-plan.md", 0, save_plan_tool._MAX_PLAN_LINES)
    ]
    assert saved == {
        "thread_id": "thread-1",
        "markdown": "# Plan\n\nDo it.",
        "status": "shared",
        "plan_file_path": "/workspace/plans/2026-06-29-test-plan.md",
        "plan_mode": None,
    }


async def test_save_plan_preserves_plan_mode_from_state_when_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    save_plan_tool = importlib.import_module("agent.tools.save_plan")

    saved: dict[str, Any] = {}

    class _Backend:
        async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> dict[str, Any]:
            return {"file_data": {"encoding": "utf-8", "content": "# Plan\n"}}

    async def fake_save_content(
        thread_id: str,
        *,
        markdown: str,
        status: str,
        plan_file_path: str | None = None,
        plan_mode: bool | None = True,
    ) -> None:
        saved.update(plan_mode=plan_mode, status=status)

    monkeypatch.setattr(
        save_plan_tool,
        "get_config",
        lambda: {"configurable": {"thread_id": "thread-1"}},
    )

    async def fake_backend(thread_id: str) -> _Backend:
        return _Backend()

    monkeypatch.setattr(save_plan_tool, "get_sandbox_backend", fake_backend)
    monkeypatch.setattr(save_plan_tool, "save_plan_content", fake_save_content)

    result = await save_plan_tool.save_plan(
        "/workspace/plans/2026-06-29-test-plan.md", state={"plan_mode": True}
    )

    assert result["success"] is True
    assert saved["plan_mode"] is True
    assert saved["status"] == "ready"


async def test_save_plan_preserves_plan_mode_from_config_when_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    save_plan_tool = importlib.import_module("agent.tools.save_plan")

    saved: dict[str, Any] = {}

    class _Backend:
        async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> dict[str, Any]:
            return {"file_data": {"encoding": "utf-8", "content": "# Plan\n"}}

    async def fake_save_content(
        thread_id: str,
        *,
        markdown: str,
        status: str,
        plan_file_path: str | None = None,
        plan_mode: bool | None = True,
    ) -> None:
        saved.update(plan_mode=plan_mode, status=status)

    monkeypatch.setattr(
        save_plan_tool,
        "get_config",
        lambda: {"configurable": {"thread_id": "thread-1", "plan_mode": True}},
    )

    async def fake_backend(thread_id: str) -> _Backend:
        return _Backend()

    monkeypatch.setattr(save_plan_tool, "get_sandbox_backend", fake_backend)
    monkeypatch.setattr(save_plan_tool, "save_plan_content", fake_save_content)

    result = await save_plan_tool.save_plan("/workspace/plans/2026-06-29-test-plan.md")

    assert result["success"] is True
    assert saved["plan_mode"] is True
    assert saved["status"] == "ready"


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
    assert "/dashboard/api/plan/{thread_id}/approve" in paths
    assert "/dashboard/api/plan/{thread_id}/reject" in paths
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

    from agent.dashboard import workflow_approval_api

    async def fake_metadata(thread_id: str) -> dict[str, Any]:
        assert thread_id == "thread-1"
        return {"source": "unknown"}

    monkeypatch.setattr(workflow_approval_api, "_thread_metadata", fake_metadata)
    monkeypatch.setattr(workflow_approval_api, "_thread_is_readable", lambda metadata: False)

    with pytest.raises(HTTPException) as exc:
        await workflow_approval_api.list_workflow_push_approvals(
            "thread-1", {"sub": "octocat", "email": "octo@example.com"}
        )

    assert exc.value.status_code == 404


async def test_list_workflow_approvals_returns_owner_and_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.dashboard import workflow_approval_api

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

    monkeypatch.setattr(workflow_approval_api, "_thread_metadata", fake_metadata)
    monkeypatch.setattr(workflow_approval_api, "_thread_is_readable", lambda metadata: True)
    monkeypatch.setattr(workflow_approval_api, "get_workflow_push_approvals", fake_approvals)

    result = await workflow_approval_api.list_workflow_push_approvals(
        "thread-1", {"sub": "octocat", "email": "octo@example.com"}
    )

    assert result["isOwner"] is True
    assert result["approvals"][0]["fingerprint"] == "abc"
    assert result["approvals"][0]["diffStats"] == {"files": 1, "additions": 1, "deletions": 0}


def test_save_plan_exported_and_wired() -> None:
    from agent.tools import save_plan

    assert callable(save_plan)


def test_save_plan_description_warns_about_slack_images() -> None:
    from agent.tools import save_plan

    description = save_plan.__doc__ or ""
    assert "persist Markdown text only" in description
    assert "post them directly in Slack" in description


def test_plan_status_constants() -> None:
    from agent.dashboard import plan_store

    assert plan_store.PLAN_STATUS_READY == "ready"
    assert plan_store.PLAN_STATUS_SHARED == "shared"
    assert plan_store.PLAN_STATUS_PLANNING == "planning"
    assert plan_store.PLAN_STATUS_APPROVED == "approved"
    assert plan_store.PLAN_STATUS_REVISING == "revising"


def test_plan_file_path_for_thread_uses_plans_dir_and_slug() -> None:
    from agent.dashboard import plan_store

    path = plan_store.plan_file_path_for_thread("Thread ABC/123")
    assert path.startswith("/workspace/plans/")
    assert path.endswith("-thread-abc-123.md")


def test_http_request_excluded_in_plan_mode() -> None:
    from agent.server import PLAN_MODE_EXCLUDED_TOOLS

    assert "http_request" in PLAN_MODE_EXCLUDED_TOOLS


def test_file_edit_tools_available_in_plan_mode_for_plan_file() -> None:
    from agent.server import PLAN_MODE_EXCLUDED_TOOLS

    assert "write_file" not in PLAN_MODE_EXCLUDED_TOOLS
    assert "edit_file" not in PLAN_MODE_EXCLUDED_TOOLS


class _FakeReq:
    def __init__(self, tools: list[Any], state: dict[str, Any]) -> None:
        self.tools = tools
        self.state = state

    def override(self, **kw: Any) -> _FakeReq:
        return _FakeReq(kw.get("tools", self.tools), self.state)


def _names(req: _FakeReq) -> set[str]:
    return {t["name"] for t in req.tools}


def test_plan_mode_middleware_initial_always_filters() -> None:
    from agent.middleware import PlanModeMiddleware

    mw = PlanModeMiddleware(excluded=frozenset({"write_file"}), initial=True)
    req = _FakeReq([{"name": "read_file"}, {"name": "write_file"}], {})
    assert _names(cast(_FakeReq, mw._filter(cast(Any, req)))) == {"read_file"}


def test_plan_mode_middleware_self_activation_via_state() -> None:
    from agent.middleware import PlanModeMiddleware

    mw = PlanModeMiddleware(excluded=frozenset({"write_file"}), initial=False)
    # Plan mode not yet active: nothing filtered.
    off = _FakeReq([{"name": "read_file"}, {"name": "write_file"}], {})
    assert _names(cast(_FakeReq, mw._filter(cast(Any, off)))) == {"read_file", "write_file"}
    # After enter_plan_mode sets state: the next request is filtered.
    on = _FakeReq([{"name": "read_file"}, {"name": "write_file"}], {"plan_mode": True})
    assert _names(cast(_FakeReq, mw._filter(cast(Any, on)))) == {"read_file"}


def test_plan_mode_middleware_self_deactivation_via_state() -> None:
    from agent.middleware import PlanModeMiddleware

    mw = PlanModeMiddleware(excluded=frozenset({"write_file"}), initial=True)
    off = _FakeReq([{"name": "read_file"}, {"name": "write_file"}], {"plan_mode": False})
    assert _names(cast(_FakeReq, mw._filter(cast(Any, off)))) == {"read_file", "write_file"}


# --- manual plan editing -------------------------------------------------


async def test_set_plan_status_preserves_plan_file_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.dashboard import plan_store

    existing = {
        "markdown": "# Plan",
        "status": "ready",
        "plan_file_path": "/workspace/plans/foo.md",
    }
    saved: dict[str, Any] = {}

    class _Store:
        async def get_item(self, *a: Any, **k: Any) -> Any:
            return {"value": existing}

        async def put_item(self, namespace: Any, key: str, value: Any, *a: Any, **k: Any) -> None:
            saved.update(value)

    async def fake_merge(thread_id: str, metadata: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(plan_store, "_client", lambda: _fake_client(_Store()))
    monkeypatch.setattr(plan_store, "_merge_thread_metadata", fake_merge)

    await plan_store.set_plan_status("t", plan_store.PLAN_STATUS_REVISING, plan_mode=True)
    assert saved["plan_file_path"] == "/workspace/plans/foo.md"
    assert saved["status"] == plan_store.PLAN_STATUS_REVISING


async def test_set_plan_status_clears_shared_content_when_entering_plan_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.dashboard import plan_store

    existing = {
        "markdown": "# Old report",
        "status": "shared",
        "plan_file_path": "/workspace/plans/old-report.md",
    }
    saved: dict[str, Any] = {}
    merged: dict[str, Any] = {}

    class _Store:
        async def get_item(self, *a: Any, **k: Any) -> Any:
            return {"value": existing}

        async def put_item(self, namespace: Any, key: str, value: Any, *a: Any, **k: Any) -> None:
            saved.update(value)

    async def fake_merge(thread_id: str, metadata: dict[str, Any]) -> None:
        merged.update(metadata)

    monkeypatch.setattr(plan_store, "_client", lambda: _fake_client(_Store()))
    monkeypatch.setattr(plan_store, "_merge_thread_metadata", fake_merge)

    await plan_store.set_plan_status("t", plan_store.PLAN_STATUS_PLANNING, plan_mode=True)

    assert saved == {"markdown": "", "status": "planning"}
    assert merged == {"plan_status": "planning", "plan_mode": True}


async def test_save_plan_content_clear_comments_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.dashboard import plan_store

    cleared: list[str] = []

    class _Store:
        async def put_item(self, *a: Any, **k: Any) -> None:
            return None

    async def fake_clear(thread_id: str) -> None:
        cleared.append(thread_id)

    async def fake_merge(thread_id: str, metadata: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(plan_store, "_client", lambda: _fake_client(_Store()))
    monkeypatch.setattr(plan_store, "clear_plan_comments", fake_clear)
    monkeypatch.setattr(plan_store, "_merge_thread_metadata", fake_merge)

    # A manual edit keeps reviewer comments; the agent's republish clears them.
    await plan_store.save_plan_content("t", markdown="x", clear_comments=False)
    assert cleared == []
    await plan_store.save_plan_content("t", markdown="x")
    assert cleared == ["t"]


async def test_save_plan_content_can_skip_plan_mode_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.dashboard import plan_store

    merged: dict[str, Any] = {}

    class _Store:
        async def put_item(self, *a: Any, **k: Any) -> None:
            return None

    async def fake_clear(thread_id: str) -> None:
        return None

    async def fake_merge(thread_id: str, metadata: dict[str, Any]) -> None:
        merged.update(metadata)

    monkeypatch.setattr(plan_store, "_client", lambda: _fake_client(_Store()))
    monkeypatch.setattr(plan_store, "clear_plan_comments", fake_clear)
    monkeypatch.setattr(plan_store, "_merge_thread_metadata", fake_merge)

    await plan_store.save_plan_content("t", markdown="x", plan_mode=None)

    assert merged == {"plan_status": "ready"}


def _patch_update_plan_deps(
    monkeypatch: pytest.MonkeyPatch,
    *,
    metadata: dict[str, Any],
    owner: bool,
    content: dict[str, Any],
    saved: dict[str, Any],
    sandbox: dict[str, Any],
) -> None:
    from agent.dashboard import plan_api

    async def fake_meta(thread_id: str) -> dict[str, Any]:
        return metadata

    async def fake_get_content(thread_id: str) -> dict[str, Any]:
        return content

    async def fake_save(
        thread_id: str,
        *,
        markdown: str,
        status: str,
        clear_comments: bool = True,
        plan_file_path: str | None = None,
    ) -> None:
        saved.update(
            markdown=markdown,
            status=status,
            clear_comments=clear_comments,
            plan_file_path=plan_file_path,
        )

    async def fake_write(thread_id: str, c: str, *, plan_file_path: str | None = None) -> str:
        sandbox["content"] = c
        sandbox["plan_file_path"] = plan_file_path
        return plan_file_path or "/workspace/plans/fallback.md"

    monkeypatch.setattr(plan_api, "_thread_metadata", fake_meta)
    monkeypatch.setattr(plan_api, "_user_owns_thread", lambda *a, **k: owner)
    monkeypatch.setattr(plan_api, "get_plan_content", fake_get_content)
    monkeypatch.setattr(plan_api, "save_plan_content", fake_save)
    monkeypatch.setattr(plan_api, "write_plan_to_sandbox", fake_write)


async def test_update_plan_owner_saves_and_mirrors_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.dashboard import plan_api

    saved: dict[str, Any] = {}
    sandbox: dict[str, Any] = {}
    _patch_update_plan_deps(
        monkeypatch,
        metadata={"plan_status": "ready"},
        owner=True,
        content={
            "markdown": "old",
            "status": "ready",
            "plan_file_path": "/workspace/plans/2026-06-29-existing.md",
        },
        saved=saved,
        sandbox=sandbox,
    )

    result = await plan_api.update_plan(
        "t1", plan_api.PlanUpdate(markdown="# New\n\ndo x"), session={"sub": "a", "email": None}
    )
    assert result == {"status": "ready", "markdown": "# New\n\ndo x"}
    assert saved["status"] == "ready"
    assert saved["clear_comments"] is False
    assert saved["plan_file_path"] == "/workspace/plans/2026-06-29-existing.md"
    assert sandbox["content"] == "# New\n\ndo x"
    assert sandbox["plan_file_path"] == "/workspace/plans/2026-06-29-existing.md"


async def test_update_plan_rejects_non_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    from agent.dashboard import plan_api

    _patch_update_plan_deps(monkeypatch, metadata={}, owner=False, content={}, saved={}, sandbox={})
    with pytest.raises(HTTPException) as exc:
        await plan_api.update_plan(
            "t1", plan_api.PlanUpdate(markdown="x"), session={"sub": "b", "email": None}
        )
    assert exc.value.status_code == 403


async def test_update_plan_rejects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    from agent.dashboard import plan_api

    _patch_update_plan_deps(monkeypatch, metadata={}, owner=True, content={}, saved={}, sandbox={})
    with pytest.raises(HTTPException) as exc:
        await plan_api.update_plan(
            "t1", plan_api.PlanUpdate(markdown="   "), session={"sub": "a", "email": None}
        )
    assert exc.value.status_code == 422


async def test_update_plan_blocked_once_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    from agent.dashboard import plan_api

    _patch_update_plan_deps(
        monkeypatch,
        metadata={"plan_status": "approved"},
        owner=True,
        content={"markdown": "old", "status": "approved"},
        saved={},
        sandbox={},
    )
    with pytest.raises(HTTPException) as exc:
        await plan_api.update_plan(
            "t1", plan_api.PlanUpdate(markdown="x"), session={"sub": "a", "email": None}
        )
    assert exc.value.status_code == 409


async def test_approve_plan_dispatches_published_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.dashboard import plan_api

    dispatched: dict[str, Any] = {}

    async def fake_meta(thread_id: str) -> dict[str, Any]:
        return {"plan_status": "ready"}

    async def fake_get_content(thread_id: str, *, raise_on_error: bool = False) -> dict[str, Any]:
        return {"markdown": "# Edited plan\n\nstep one", "status": "ready"}

    async def fake_list(thread_id: str, *, raise_on_error: bool = False) -> list[dict[str, Any]]:
        return [{"author": "bob", "body": "use snake_case"}]

    async def fake_set_status(thread_id: str, status: str, *, plan_mode: Any = None) -> None:
        return None

    async def fake_dispatch(
        thread_id: str, metadata: dict[str, Any], text: str, *, plan_mode: bool
    ) -> None:
        dispatched.update(text=text, plan_mode=plan_mode)

    monkeypatch.setattr(plan_api, "_thread_metadata", fake_meta)
    monkeypatch.setattr(plan_api, "_user_owns_thread", lambda *a, **k: True)
    monkeypatch.setattr(plan_api, "get_plan_content", fake_get_content)
    monkeypatch.setattr(plan_api, "list_plan_comments", fake_list)
    monkeypatch.setattr(plan_api, "set_plan_status", fake_set_status)
    monkeypatch.setattr(plan_api, "_dispatch_followup", fake_dispatch)

    result = await plan_api.approve_plan("t1", session={"sub": "a", "email": None})
    assert result["status"] == "approved"
    # The (possibly edited) published plan is the source of truth, plus feedback.
    assert "# Edited plan" in dispatched["text"]
    assert "use snake_case" in dispatched["text"]
    assert dispatched["plan_mode"] is False


async def test_approve_plan_posts_slack_approval_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.dashboard import plan_api

    posted: dict[str, Any] = {}
    dispatched: dict[str, Any] = {}

    async def fake_meta(thread_id: str) -> dict[str, Any]:
        return {
            "plan_status": "ready",
            "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "123.45"}},
        }

    async def fake_get_content(thread_id: str, *, raise_on_error: bool = False) -> dict[str, Any]:
        return {"markdown": "# Plan", "status": "ready"}

    async def fake_list(thread_id: str, *, raise_on_error: bool = False) -> list[dict[str, Any]]:
        return [
            {"author": "alice", "body": "looks good"},
            {"author": "bob", "body": "add a test"},
        ]

    async def fake_set_status(thread_id: str, status: str, *, plan_mode: Any = None) -> None:
        return None

    async def fake_post(channel_id: str, thread_ts: str, text: str) -> bool:
        posted.update(channel_id=channel_id, thread_ts=thread_ts, text=text)
        return True

    async def fake_dispatch(
        thread_id: str, metadata: dict[str, Any], text: str, *, plan_mode: bool
    ) -> None:
        dispatched.update(text=text, plan_mode=plan_mode)

    monkeypatch.setattr(plan_api, "_thread_metadata", fake_meta)
    monkeypatch.setattr(plan_api, "_user_owns_thread", lambda *a, **k: True)
    monkeypatch.setattr(plan_api, "get_plan_content", fake_get_content)
    monkeypatch.setattr(plan_api, "list_plan_comments", fake_list)
    monkeypatch.setattr(plan_api, "set_plan_status", fake_set_status)
    monkeypatch.setattr(plan_api, "post_slack_thread_reply", fake_post)
    monkeypatch.setattr(plan_api, "_dispatch_followup", fake_dispatch)

    result = await plan_api.approve_plan(
        "t1", session={"sub": "alice", "email": None, "name": "Alice Example"}
    )

    assert result["status"] == "approved"
    assert posted == {
        "channel_id": "C1",
        "thread_ts": "123.45",
        "text": "Plan approved with 2 comments by Alice Example\nbeginning implementation",
    }
    assert dispatched["plan_mode"] is False


async def test_approve_plan_aborts_when_plan_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.dashboard import plan_api

    dispatched: list[Any] = []

    async def fake_meta(thread_id: str) -> dict[str, Any]:
        return {"plan_status": "ready"}

    async def fake_get_content(thread_id: str, *, raise_on_error: bool = False) -> dict[str, Any]:
        # A transient store failure must abort approval, not silently drop the
        # owner's edited plan and dispatch the generic fallback text.
        raise RuntimeError("store down")

    async def fake_set_status(thread_id: str, status: str, *, plan_mode: Any = None) -> None:
        return None

    async def fake_dispatch(*a: Any, **k: Any) -> None:
        dispatched.append((a, k))

    monkeypatch.setattr(plan_api, "_thread_metadata", fake_meta)
    monkeypatch.setattr(plan_api, "_user_owns_thread", lambda *a, **k: True)
    monkeypatch.setattr(plan_api, "get_plan_content", fake_get_content)
    monkeypatch.setattr(plan_api, "set_plan_status", fake_set_status)
    monkeypatch.setattr(plan_api, "_dispatch_followup", fake_dispatch)

    with pytest.raises(RuntimeError):
        await plan_api.approve_plan("t1", session={"sub": "a", "email": None})
    assert dispatched == []


async def test_approve_plan_rejects_shared_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from agent.dashboard import plan_api

    dispatched: list[Any] = []

    async def fake_meta(thread_id: str) -> dict[str, Any]:
        return {"plan_status": "shared"}

    async def fake_get_content(thread_id: str, *, raise_on_error: bool = False) -> dict[str, Any]:
        return {"markdown": "# Report", "status": "shared"}

    async def fake_dispatch(*a: Any, **k: Any) -> None:
        dispatched.append((a, k))

    monkeypatch.setattr(plan_api, "_thread_metadata", fake_meta)
    monkeypatch.setattr(plan_api, "_user_owns_thread", lambda *a, **k: True)
    monkeypatch.setattr(plan_api, "get_plan_content", fake_get_content)
    monkeypatch.setattr(plan_api, "_dispatch_followup", fake_dispatch)

    with pytest.raises(HTTPException) as exc:
        await plan_api.approve_plan("t1", session={"sub": "a", "email": None})
    assert exc.value.status_code == 409
    assert dispatched == []


async def test_reject_plan_rejects_shared_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from agent.dashboard import plan_api

    dispatched: list[Any] = []

    async def fake_meta(thread_id: str) -> dict[str, Any]:
        return {"plan_status": "shared"}

    async def fake_get_content(thread_id: str, *, raise_on_error: bool = False) -> dict[str, Any]:
        return {"markdown": "# Report", "status": "shared"}

    async def fake_dispatch(*a: Any, **k: Any) -> None:
        dispatched.append((a, k))

    monkeypatch.setattr(plan_api, "_thread_metadata", fake_meta)
    monkeypatch.setattr(plan_api, "_thread_is_readable", lambda metadata: True)
    monkeypatch.setattr(plan_api, "get_plan_content", fake_get_content)
    monkeypatch.setattr(plan_api, "_dispatch_followup", fake_dispatch)

    with pytest.raises(HTTPException) as exc:
        await plan_api.reject_plan("t1", session={"sub": "a", "email": None})
    assert exc.value.status_code == 409
    assert dispatched == []
