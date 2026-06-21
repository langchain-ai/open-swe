from __future__ import annotations

import pytest


def test_dashboard_plan_url_uses_plan_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://example.test")
    from agent.utils.dashboard_links import dashboard_plan_url

    assert dashboard_plan_url("abc-123") == "https://example.test/agents/abc-123/plan"


def test_dashboard_plan_url_none_without_thread() -> None:
    from agent.utils.dashboard_links import dashboard_plan_url

    assert dashboard_plan_url("") is None


def test_format_comments_with_quote_and_author() -> None:
    from agent.dashboard.plan_api import PlanComment, _format_comments

    text = _format_comments(
        [
            PlanComment(author="alice", body="add a docstring", quote="def greet"),
            PlanComment(author="bob", body="looks good", resolved=True),
            PlanComment(author="carol", body="   "),  # blank → skipped
        ]
    )
    assert 'On "def greet"' in text
    assert "alice: add a docstring" in text
    assert "bob (resolved): looks good" in text
    assert "carol" not in text


def test_format_comments_empty() -> None:
    from agent.dashboard.plan_api import _format_comments

    assert _format_comments([]) == ""


def test_plan_decision_body_defaults() -> None:
    from agent.dashboard.plan_api import PlanComment, PlanDecisionBody

    body = PlanDecisionBody()
    assert body.comments == []
    comment = PlanComment(body="hi")
    assert comment.author is None
    assert comment.resolved is False


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
    assert "/dashboard/api/plan/yjs/{thread_id}" in paths


def test_save_plan_exported_and_wired() -> None:
    from agent.tools import save_plan

    assert callable(save_plan)


def test_plan_status_constants() -> None:
    from agent.dashboard import plan_store

    assert plan_store.PLAN_STATUS_READY == "ready"
    assert plan_store.PLAN_STATUS_PLANNING == "planning"
    assert plan_store.PLAN_STATUS_APPROVED == "approved"
    assert plan_store.PLAN_STATUS_REVISING == "revising"
