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
