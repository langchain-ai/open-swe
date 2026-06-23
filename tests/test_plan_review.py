from __future__ import annotations

from typing import Any

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


def test_plan_comment_helpers_exported() -> None:
    from agent.dashboard import plan_store

    assert plan_store.PLAN_COMMENTS_NAMESPACE == ["plan", "comments"]
    assert callable(plan_store.add_plan_comment)
    assert callable(plan_store.list_plan_comments)
    assert callable(plan_store.delete_plan_comment)


def test_save_plan_requires_run_context() -> None:
    from agent.tools.save_plan import save_plan

    # No LangGraph run context → no thread_id → graceful error, not a crash.
    result = save_plan("## Plan")
    assert result["success"] is False
    assert "thread_id" in result["error"]


def test_save_plan_rejects_empty_markdown() -> None:
    from agent.tools.save_plan import save_plan

    result = save_plan("   ")
    assert result["success"] is False
    assert "empty" in result["error"]


def test_plan_routes_registered() -> None:
    from agent.webapp import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/dashboard/api/plan/{thread_id}" in paths
    assert "/dashboard/api/plan/{thread_id}/approve" in paths
    assert "/dashboard/api/plan/{thread_id}/reject" in paths
    assert "/dashboard/api/plan/{thread_id}/comments" in paths
    assert "/dashboard/api/plan/{thread_id}/comments/{comment_id}" in paths
    assert "/dashboard/api/plan/yjs/{thread_id}" not in paths


def test_save_plan_exported_and_wired() -> None:
    from agent.tools import save_plan

    assert callable(save_plan)


def test_plan_status_constants() -> None:
    from agent.dashboard import plan_store

    assert plan_store.PLAN_STATUS_READY == "ready"
    assert plan_store.PLAN_STATUS_PLANNING == "planning"
    assert plan_store.PLAN_STATUS_APPROVED == "approved"
    assert plan_store.PLAN_STATUS_REVISING == "revising"


def test_http_request_excluded_in_plan_mode() -> None:
    from agent.server import PLAN_MODE_EXCLUDED_TOOLS

    assert "http_request" in PLAN_MODE_EXCLUDED_TOOLS


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
    assert _names(mw._filter(req)) == {"read_file"}


def test_plan_mode_middleware_self_activation_via_state() -> None:
    from agent.middleware import PlanModeMiddleware

    mw = PlanModeMiddleware(excluded=frozenset({"write_file"}), initial=False)
    # Plan mode not yet active: nothing filtered.
    off = _FakeReq([{"name": "read_file"}, {"name": "write_file"}], {})
    assert _names(mw._filter(off)) == {"read_file", "write_file"}
    # After enter_plan_mode sets state: the next request is filtered.
    on = _FakeReq([{"name": "read_file"}, {"name": "write_file"}], {"plan_mode": True})
    assert _names(mw._filter(on)) == {"read_file"}
