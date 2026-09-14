import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.dashboard.threads import pr_fixes


@asynccontextmanager
async def unlocked(*args):
    yield


@pytest.fixture
def setup(monkeypatch):
    thread = {"thread_id": "existing", "metadata": {"source": "dashboard"}}
    client = SimpleNamespace(
        threads=SimpleNamespace(
            search=AsyncMock(return_value=[thread]),
            get=AsyncMock(return_value=thread),
            update=AsyncMock(),
        )
    )
    monkeypatch.setattr(pr_fixes, "langgraph_client", lambda: client)
    monkeypatch.setattr(pr_fixes, "agent_thread_pr_state_lock", unlocked)
    for name in (
        "require_repo_access_for_user",
        "_ensure_dashboard_github_token",
        "dispatch_agent_run",
    ):
        monkeypatch.setattr(pr_fixes, name, AsyncMock())
    monkeypatch.setattr(pr_fixes, "_build_dashboard_configurable", AsyncMock(return_value={}))
    monkeypatch.setattr(
        pr_fixes,
        "_create_dashboard_thread_record",
        AsyncMock(return_value={"thread_id": "new", "metadata": {}}),
    )
    return client


async def test_reuses_existing_thread_and_enqueues_fix(setup):
    assert await pr_fixes.fix_pull_request("acme", "app", 12, "alice") == {"thread_id": "existing"}
    pr_fixes._create_dashboard_thread_record.assert_not_awaited()
    args = pr_fixes.dispatch_agent_run.await_args
    assert args.args[0] == "existing"
    assert "https://github.com/acme/app/pull/12" in args.args[1]
    assert args.kwargs["multitask_strategy"] == "enqueue"


async def test_creates_when_only_review_or_private_threads_exist(setup):
    setup.threads.search.return_value = [
        {"thread_id": "review", "metadata": {"kind": "reviewer"}},
        {
            "thread_id": "private",
            "metadata": {"source": "dashboard", "visibility": "private", "owner_login": "bob"},
        },
    ]
    assert await pr_fixes.fix_pull_request("acme", "app", 12, "alice") == {"thread_id": "new"}
    assert pr_fixes._create_dashboard_thread_record.await_args.kwargs["repo_config"] == {
        "owner": "acme",
        "name": "app",
    }
    setup.threads.update.assert_any_await(
        thread_id="new",
        metadata={
            "pr_url": "https://github.com/acme/app/pull/12",
            "pr_number": 12,
            "source_context": {"pr_number": 12},
        },
    )


async def test_denied_repo_never_reads_or_starts_threads(setup):
    pr_fixes.require_repo_access_for_user.side_effect = HTTPException(403, "denied")
    with pytest.raises(HTTPException):
        await pr_fixes.fix_pull_request("acme", "app", 12, "alice")
    setup.threads.search.assert_not_awaited()
    pr_fixes.dispatch_agent_run.assert_not_awaited()


async def test_fix_message_includes_full_displayed_failure_context(setup):
    context = pr_fixes.PullRequestFixContext(
        title="Broken build",
        headRef="feature/fix",
        headSha="abc123",
        mergeable=False,
        mergeState="dirty",
        ci="failing",
        failingChecks=["lint", "unit", "build", "integration"],
        pendingChecks=["e2e"],
        statusAvailable=True,
        updatedAt="2026-09-12T12:00:00Z",
        reviewDecision="changes_requested",
    )
    await pr_fixes.fix_pull_request("acme", "app", 12, "alice", context=context)
    prompt = pr_fixes.dispatch_agent_run.await_args.args[1]
    assert json.loads(prompt[prompt.index("{\n") :]) == context.model_dump()
    assert "may be stale" in prompt


async def test_open_reuses_thread_without_dispatching_or_mutating_it(setup):
    assert await pr_fixes.open_pull_request_thread(
        "acme", "app", 12, "alice", title="Fix broken build"
    ) == {"thread_id": "existing"}
    pr_fixes._create_dashboard_thread_record.assert_not_awaited()
    pr_fixes.dispatch_agent_run.assert_not_awaited()
    setup.threads.update.assert_not_awaited()


async def test_open_creates_idle_thread_when_no_accessible_coding_thread_exists(setup):
    setup.threads.search.return_value = [
        {"thread_id": "review", "metadata": {"kind": "reviewer"}},
        {
            "thread_id": "private",
            "metadata": {"source": "dashboard", "visibility": "private", "owner_login": "bob"},
        },
    ]
    assert await pr_fixes.open_pull_request_thread(
        "acme", "app", 12, "alice", title="Fix broken build"
    ) == {"thread_id": "new"}
    assert pr_fixes._create_dashboard_thread_record.await_args.kwargs["title"] == "Fix broken build"
    setup.threads.update.assert_awaited_once_with(
        thread_id="new",
        metadata={
            "pr_url": "https://github.com/acme/app/pull/12",
            "pr_number": 12,
            "source_context": {"pr_number": 12},
        },
    )
    pr_fixes.dispatch_agent_run.assert_not_awaited()


async def test_open_denied_repo_never_searches_or_creates_threads(setup):
    pr_fixes.require_repo_access_for_user.side_effect = HTTPException(403, "denied")
    with pytest.raises(HTTPException):
        await pr_fixes.open_pull_request_thread(
            "acme", "app", 12, "alice", title="Fix broken build"
        )
    setup.threads.search.assert_not_awaited()
    pr_fixes._create_dashboard_thread_record.assert_not_awaited()


async def test_new_thread_supplies_pr_context_to_first_user_run(setup, monkeypatch):
    from agent.dashboard.threads import runs

    setup.threads.search.return_value = []
    await pr_fixes.open_pull_request_thread("acme", "app", 12, "alice", title="Fix broken build")
    metadata = setup.threads.update.await_args.kwargs["metadata"]
    monkeypatch.setattr(runs, "resolve_run_email", AsyncMock(return_value=None))
    configurable = await runs._build_dashboard_configurable(
        "new",
        "alice",
        {**metadata, "repo_owner": "acme", "repo_name": "app", "source": "dashboard"},
        profile={},
    )
    assert configurable["pr_number"] == 12
    assert configurable["repo"] == {"owner": "acme", "name": "app"}
    pr_fixes.dispatch_agent_run.assert_not_awaited()
