"""Unit tests for the CI auto-fix orchestration core."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent import ci_autofix

_PR = {
    "number": 5,
    "html_url": "https://github.com/o/r/pull/5",
    "base": {"sha": "base"},
    "head": {"ref": "feat", "sha": "head1"},
}


@pytest.fixture
def happy(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch every dependency of handle_ci_failure to a happy-path default."""
    runs_create = AsyncMock()
    lg_client = MagicMock()
    lg_client.runs.create = runs_create
    threads_update = AsyncMock()
    store_client = MagicMock()
    store_client.threads.update = threads_update

    mocks: dict[str, Any] = {
        "runs_create": runs_create,
        "threads_update": threads_update,
        "status_check": AsyncMock(return_value=True),
    }

    monkeypatch.setattr(ci_autofix, "_user_autofix_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(ci_autofix, "is_review_repo_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        ci_autofix, "get_github_app_installation_token", AsyncMock(return_value="tok")
    )
    monkeypatch.setattr(ci_autofix, "is_pr_autofix_disabled", AsyncMock(return_value=False))
    monkeypatch.setattr(
        ci_autofix,
        "find_agent_thread_for_pr",
        AsyncMock(return_value=("t1", {"github_login": "alice", "autofix_attempts": 0})),
    )
    monkeypatch.setattr(
        ci_autofix,
        "list_failing_check_runs",
        AsyncMock(return_value=[{"name": "lint", "conclusion": "failure", "details_url": ""}]),
    )
    monkeypatch.setattr(ci_autofix, "list_failing_statuses", AsyncMock(return_value=[]))
    monkeypatch.setattr(ci_autofix, "names_failing_on_base", AsyncMock(return_value=set()))
    monkeypatch.setattr(
        ci_autofix, "head_commit_author_login", AsyncMock(return_value="open-swe[bot]")
    )
    monkeypatch.setattr(ci_autofix, "is_thread_active", AsyncMock(return_value=False))
    monkeypatch.setattr(ci_autofix, "post_autofix_status_check", mocks["status_check"])
    monkeypatch.setattr(ci_autofix, "langgraph_client", lambda: lg_client)
    monkeypatch.setattr(ci_autofix, "get_client", lambda: store_client)
    return mocks


async def _run(**overrides: Any) -> str:
    kwargs: dict[str, Any] = {
        "repo_config": {"owner": "o", "name": "r"},
        "branch": "feat",
        "head_sha": "head1",
        "pr": _PR,
    }
    kwargs.update(overrides)
    return await ci_autofix.handle_ci_failure(**kwargs)


@pytest.mark.asyncio
async def test_dispatch_happy_path(happy: dict[str, Any]) -> None:
    result = await _run()
    assert result == "dispatched"
    happy["runs_create"].assert_awaited_once()
    happy["threads_update"].assert_awaited()
    happy["status_check"].assert_awaited()


@pytest.mark.asyncio
async def test_batches_when_thread_busy(happy: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setattr(ci_autofix, "is_thread_active", AsyncMock(return_value=True))
    result = await _run()
    assert result == "batched"
    happy["threads_update"].assert_awaited()
    happy["runs_create"].assert_not_called()


@pytest.mark.asyncio
async def test_skip_user_disabled(happy: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setattr(ci_autofix, "_user_autofix_enabled", AsyncMock(return_value=False))
    assert await _run() == "autofix_disabled_user"


@pytest.mark.asyncio
async def test_skip_repo_not_enabled(happy: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setattr(ci_autofix, "is_review_repo_enabled", AsyncMock(return_value=False))
    assert await _run() == "repo_not_enabled"


@pytest.mark.asyncio
async def test_skip_pr_disabled(happy: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setattr(ci_autofix, "is_pr_autofix_disabled", AsyncMock(return_value=True))
    assert await _run() == "pr_disabled"


@pytest.mark.asyncio
async def test_skip_no_agent_thread(happy: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setattr(ci_autofix, "find_agent_thread_for_pr", AsyncMock(return_value=None))
    assert await _run() == "no_agent_thread"


@pytest.mark.asyncio
async def test_skip_max_attempts(happy: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setattr(
        ci_autofix,
        "find_agent_thread_for_pr",
        AsyncMock(
            return_value=(
                "t1",
                {"github_login": "alice", "autofix_attempts": ci_autofix.MAX_AUTOFIX_ATTEMPTS},
            )
        ),
    )
    assert await _run() == "max_attempts"
    happy["status_check"].assert_awaited()
    happy["runs_create"].assert_not_called()


@pytest.mark.asyncio
async def test_skip_all_failing_on_base(happy: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setattr(ci_autofix, "names_failing_on_base", AsyncMock(return_value={"lint"}))
    assert await _run() == "all_failing_on_base"


@pytest.mark.asyncio
async def test_skip_already_handled(happy: dict[str, Any], monkeypatch) -> None:
    key = ci_autofix._dedupe_key("head1")
    monkeypatch.setattr(
        ci_autofix,
        "find_agent_thread_for_pr",
        AsyncMock(return_value=("t1", {"github_login": "alice", "autofix_handled": [key]})),
    )
    assert await _run() == "already_handled"


@pytest.mark.asyncio
async def test_skip_human_commit(happy: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setattr(ci_autofix, "head_commit_author_login", AsyncMock(return_value="mallory"))
    assert await _run() == "human_commit"
    happy["runs_create"].assert_not_called()


@pytest.mark.asyncio
async def test_no_failing_checks(happy: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setattr(ci_autofix, "list_failing_check_runs", AsyncMock(return_value=[]))
    assert await _run(failing_checks=None) == "no_failing_checks"


@pytest.mark.asyncio
async def test_ci_read_failed(happy: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setattr(ci_autofix, "list_failing_check_runs", AsyncMock(return_value=None))
    monkeypatch.setattr(ci_autofix, "list_failing_statuses", AsyncMock(return_value=None))
    assert await _run(failing_checks=None) == "ci_read_failed"


@pytest.mark.asyncio
async def test_review_feedback_skips_user_disabled(happy: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setattr(ci_autofix, "_user_autofix_enabled", AsyncMock(return_value=False))
    assert (
        await ci_autofix.handle_review_feedback(
            repo_config={"owner": "o", "name": "r"},
            pr_number=5,
            pr_url="https://github.com/o/r/pull/5",
            reviewer="alice",
            body="fix this",
        )
        == "autofix_disabled_user"
    )
    happy["runs_create"].assert_not_called()


@pytest.mark.asyncio
async def test_review_feedback_batches_when_thread_busy(happy: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setattr(ci_autofix, "has_repo_write_permission", AsyncMock(return_value=True))
    monkeypatch.setattr(ci_autofix, "is_thread_active", AsyncMock(return_value=True))
    result = await ci_autofix.handle_review_feedback(
        repo_config={"owner": "o", "name": "r"},
        pr_number=5,
        pr_url="https://github.com/o/r/pull/5",
        reviewer="alice",
        body="fix this",
    )
    assert result == "batched"
    happy["runs_create"].assert_not_called()


@pytest.mark.asyncio
async def test_review_feedback_checks_write_permission_after_user_gate(
    happy: dict[str, Any], monkeypatch
) -> None:
    permission = AsyncMock(return_value=False)
    monkeypatch.setattr(ci_autofix, "has_repo_write_permission", permission)
    result = await ci_autofix.handle_review_feedback(
        repo_config={"owner": "o", "name": "r"},
        pr_number=5,
        pr_url="https://github.com/o/r/pull/5",
        reviewer="alice",
        body="fix this",
    )
    assert result == "reviewer_no_write_permission"
    permission.assert_awaited_once()
    happy["runs_create"].assert_not_called()


@pytest.mark.asyncio
async def test_find_agent_thread_picks_agent_skips_reviewer(monkeypatch) -> None:
    client = MagicMock()
    client.threads.search = AsyncMock(
        return_value=[
            {"thread_id": "rev", "metadata": {"kind": "reviewer", "agent_kind": "agent"}},
            {"thread_id": "ag", "metadata": {"agent_kind": "agent"}},
        ]
    )
    monkeypatch.setattr(ci_autofix, "get_client", lambda: client)
    found = await ci_autofix.find_agent_thread_for_pr("https://github.com/o/r/pull/5")
    assert found is not None
    assert found[0] == "ag"


@pytest.mark.asyncio
async def test_find_agent_thread_none_when_only_reviewer(monkeypatch) -> None:
    client = MagicMock()
    client.threads.search = AsyncMock(
        return_value=[{"thread_id": "rev", "metadata": {"kind": "reviewer"}}]
    )
    monkeypatch.setattr(ci_autofix, "get_client", lambda: client)
    assert await ci_autofix.find_agent_thread_for_pr("u") is None
