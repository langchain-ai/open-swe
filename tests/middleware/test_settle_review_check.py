from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware import AgentState

from agent.middleware.settle_review_check import settle_review_check_on_exit


@pytest.mark.parametrize(
    ("flag", "expected"),
    [
        (
            None,
            (
                "neutral",
                "Review did not complete",
                "The Open SWE review run ended without publishing a review. "
                "Re-trigger the review by pushing a commit or re-requesting it.",
            ),
        ),
        (
            "true",
            (
                "failure",
                "Review did not complete",
                "The Open SWE review run ended without publishing a review. "
                "Re-trigger the review by pushing a commit or re-requesting it.",
            ),
        ),
    ],
    ids=["flag-unset-neutral", "flag-true-failure"],
)
async def test_unpublished_review_settle_mapping(
    monkeypatch: pytest.MonkeyPatch,
    flag: str | None,
    expected: tuple[str, str, str],
) -> None:
    if flag is None:
        monkeypatch.delenv("REVIEW_CHECK_BLOCKING", raising=False)
    else:
        monkeypatch.setenv("REVIEW_CHECK_BLOCKING", flag)

    state: AgentState = {"messages": []}
    with (
        patch(
            "agent.middleware.settle_review_check.get_config",
            return_value={
                "configurable": {
                    "thread_id": "thread-1",
                    "repo": {"owner": "acme", "name": "widgets"},
                }
            },
        ),
        patch(
            "agent.middleware.settle_review_check.get_thread_metadata",
            new_callable=AsyncMock,
            return_value={"review_check_run_id": 42},
        ),
        patch(
            "agent.middleware.settle_review_check.get_github_token",
            return_value="token",
        ),
        patch(
            "agent.middleware.settle_review_check.settle_review_check_run",
            new_callable=AsyncMock,
        ) as settle,
    ):
        result = await settle_review_check_on_exit.aafter_agent(state, MagicMock())

    assert result is None
    settle.assert_awaited_once()
    call = settle.await_args
    assert call is not None
    assert (
        call.kwargs["conclusion"],
        call.kwargs["title"],
        call.kwargs["summary"],
    ) == expected


@pytest.mark.asyncio
async def test_settle_review_check_on_exit_skips_without_tracked_id() -> None:
    state: AgentState = {"messages": []}
    with (
        patch(
            "agent.middleware.settle_review_check.get_config",
            return_value={
                "configurable": {
                    "thread_id": "thread-1",
                    "repo": {"owner": "acme", "name": "widgets"},
                }
            },
        ),
        patch(
            "agent.middleware.settle_review_check.get_thread_metadata",
            new_callable=AsyncMock,
            return_value={"review_check_run_id": None},
        ),
        patch(
            "agent.middleware.settle_review_check.settle_review_check_run",
            new_callable=AsyncMock,
        ) as settle,
    ):
        result = await settle_review_check_on_exit.aafter_agent(state, MagicMock())

    assert result is None
    settle.assert_not_awaited()


@pytest.mark.asyncio
async def test_settle_review_check_on_exit_preserves_deferred_check() -> None:
    state: AgentState = {"messages": []}
    with (
        patch(
            "agent.middleware.settle_review_check.get_config",
            return_value={
                "configurable": {
                    "thread_id": "thread-1",
                    "repo": {"owner": "acme", "name": "widgets"},
                }
            },
        ),
        patch(
            "agent.middleware.settle_review_check.get_thread_metadata",
            new_callable=AsyncMock,
            return_value={
                "review_check_run_id": 42,
                "review_check_deferred_result": {"review_check_run_id": 42},
            },
        ),
        patch(
            "agent.middleware.settle_review_check.settle_review_check_run",
            new_callable=AsyncMock,
        ) as settle,
    ):
        result = await settle_review_check_on_exit.aafter_agent(state, MagicMock())

    assert result is None
    settle.assert_not_awaited()


@pytest.mark.asyncio
async def test_settle_review_check_on_exit_skips_finding_reply() -> None:
    state: AgentState = {"messages": []}
    with (
        patch(
            "agent.middleware.settle_review_check.get_config",
            return_value={
                "configurable": {
                    "thread_id": "thread-1",
                    "repo": {"owner": "acme", "name": "widgets"},
                    "reviewer_event": "finding_reply",
                }
            },
        ),
        patch(
            "agent.middleware.settle_review_check.get_thread_metadata",
            new_callable=AsyncMock,
            return_value={"review_check_run_id": 42},
        ),
        patch(
            "agent.middleware.settle_review_check.settle_review_check_run",
            new_callable=AsyncMock,
        ) as settle,
    ):
        result = await settle_review_check_on_exit.aafter_agent(state, MagicMock())

    # A finding reply owns no check of its own: check 42 belongs to whichever
    # review is running, and this hook must not conclude it.
    assert result is None
    settle.assert_not_awaited()


async def test_settle_review_check_on_exit_settles_check_handed_to_finding_reply() -> None:
    """A finding reply that preempted a review must close the check it froze.

    The handoff deliberately leaves that check in progress; if this run ends
    without publishing, nothing else is left to conclude it.
    """
    state: AgentState = {"messages": []}
    with (
        patch(
            "agent.middleware.settle_review_check.get_config",
            return_value={
                "configurable": {
                    "thread_id": "thread-1",
                    "repo": {"owner": "acme", "name": "widgets"},
                    "reviewer_event": "finding_reply",
                }
            },
        ),
        patch(
            "agent.middleware.settle_review_check.get_thread_metadata",
            new_callable=AsyncMock,
            return_value={
                "review_check_run_id": 42,
                "review_check_superseded": {"review_check_run_id": 42},
            },
        ),
        patch(
            "agent.middleware.settle_review_check.get_github_token",
            return_value="token",
        ),
        patch(
            "agent.middleware.settle_review_check.settle_review_check_run",
            new_callable=AsyncMock,
        ) as settle,
    ):
        await settle_review_check_on_exit.aafter_agent(state, MagicMock())

    settle.assert_awaited_once()
    assert settle.await_args is not None
    assert settle.await_args.kwargs["expected_check_run_id"] == 42
    assert settle.await_args.kwargs["title"] == "Review did not complete"


@pytest.mark.asyncio
async def test_settle_review_check_on_exit_targets_owned_check_not_replacement() -> None:
    state: AgentState = {"messages": []}
    with (
        patch(
            "agent.middleware.settle_review_check.get_config",
            return_value={
                "configurable": {
                    "thread_id": "thread-1",
                    "repo": {"owner": "acme", "name": "widgets"},
                    "review_check_run_id": 42,
                }
            },
        ),
        patch(
            "agent.middleware.settle_review_check.get_thread_metadata",
            new_callable=AsyncMock,
            return_value={"review_check_run_id": 43},
        ),
        patch("agent.middleware.settle_review_check.get_github_token", return_value="token"),
        patch(
            "agent.middleware.settle_review_check.fetch_review_check_run_status",
            new_callable=AsyncMock,
            return_value="in_progress",
        ),
        patch(
            "agent.middleware.settle_review_check.settle_review_check_run",
            new_callable=AsyncMock,
        ) as settle,
    ):
        result = await settle_review_check_on_exit.aafter_agent(state, MagicMock())

    assert result is None
    settle.assert_awaited_once()
    assert settle.await_args is not None
    assert settle.await_args.kwargs["expected_check_run_id"] == 42


@pytest.mark.asyncio
async def test_settle_review_check_on_exit_preserves_completed_owned_check() -> None:
    state: AgentState = {"messages": []}
    with (
        patch(
            "agent.middleware.settle_review_check.get_config",
            return_value={
                "configurable": {
                    "thread_id": "thread-1",
                    "repo": {"owner": "acme", "name": "widgets"},
                    "review_check_run_id": 42,
                }
            },
        ),
        patch(
            "agent.middleware.settle_review_check.get_thread_metadata",
            new_callable=AsyncMock,
            return_value={"review_check_run_id": 43},
        ),
        patch("agent.middleware.settle_review_check.get_github_token", return_value="token"),
        patch(
            "agent.middleware.settle_review_check.fetch_review_check_run_status",
            new_callable=AsyncMock,
            return_value="completed",
        ),
        patch(
            "agent.middleware.settle_review_check.settle_review_check_run",
            new_callable=AsyncMock,
        ) as settle,
    ):
        result = await settle_review_check_on_exit.aafter_agent(state, MagicMock())

    assert result is None
    settle.assert_not_awaited()


async def test_settle_review_check_on_exit_refreshes_an_expired_token() -> None:
    state: AgentState = {"messages": []}
    refresh = AsyncMock(return_value="fresh-token")
    with (
        patch(
            "agent.middleware.settle_review_check.get_config",
            return_value={
                "configurable": {
                    "thread_id": "thread-1",
                    "repo": {"owner": "acme", "name": "widgets"},
                    "review_check_run_id": 42,
                }
            },
        ),
        patch(
            "agent.middleware.settle_review_check.get_thread_metadata",
            new_callable=AsyncMock,
            return_value={"review_check_run_id": 42},
        ),
        patch("agent.middleware.settle_review_check.get_github_token", return_value=None),
        patch("agent.middleware.settle_review_check.refresh_github_token", refresh),
        patch(
            "agent.middleware.settle_review_check.settle_review_check_run",
            new_callable=AsyncMock,
        ) as settle,
    ):
        await settle_review_check_on_exit.aafter_agent(state, MagicMock())

    refresh.assert_awaited_once()
    settle.assert_awaited_once()
    assert settle.await_args is not None
    assert settle.await_args.kwargs["token"] == "fresh-token"
