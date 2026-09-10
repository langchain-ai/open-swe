"""The reviewer opts into replacing an unreachable sandbox, and notifies when it can't.

A reviewer sandbox holds only a checkout `prepare_review_repo` re-derives every
run, and reviewer threads (one per PR) outlive their sandbox, so refusing to
replace one bricks reviews on that PR permanently. The replacement policy itself
is covered in ``tests/coding_agent/sandboxes/test_sandbox_lifecycle.py``.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.runnables import RunnableConfig

from agent.reviewer import PrepareReviewerRunMiddleware, _ensure_reviewer_sandbox_for_thread
from agent.run_config import OpenSWERunConfig
from coding_agent.sandboxes.state import SandboxUnreachableError


@pytest.mark.asyncio
async def test_reviewer_opts_into_replacement_with_repo_scoped_credentials() -> None:
    sandbox_backend = MagicMock()
    ensure = AsyncMock(return_value=sandbox_backend)

    with patch(
        "agent.reviewer.OPEN_SWE_SANDBOXES.with_credentials",
        return_value=MagicMock(ensure_for_thread=ensure),
    ) as with_credentials:
        result, github_token = await _ensure_reviewer_sandbox_for_thread(
            "thread-reviewer",
            OpenSWERunConfig.parse({"repo": {"owner": "langchain-ai", "name": "open-swe"}}),
        )

    assert result is sandbox_backend
    assert github_token is None
    assert ensure.await_args is not None
    assert ensure.await_args.kwargs["allow_replacement"] is True
    credentials = with_credentials.call_args.args[0]
    assert credentials.token is None
    assert credentials.repositories == ["open-swe"]


@pytest.mark.asyncio
async def test_reviewer_notifies_when_replacement_also_fails() -> None:
    config: RunnableConfig = {
        "configurable": {"repo": {"owner": "langchain-ai", "name": "open-swe"}}
    }
    middleware = PrepareReviewerRunMiddleware(
        thread_id="thread-reviewer", config=config, use_gateway=False
    )

    with (
        patch(
            "agent.reviewer._ensure_reviewer_sandbox_for_thread",
            new_callable=AsyncMock,
            side_effect=SandboxUnreachableError("thread-reviewer", "sandbox-deleted", "not found"),
        ),
        patch(
            "agent.reviewer.post_sandbox_unreachable_notification",
            new_callable=AsyncMock,
        ) as notify,
        pytest.raises(SandboxUnreachableError),
    ):
        await middleware._prepare({"messages": []}, MagicMock())

    notify.assert_awaited_once()
    assert notify.await_args is not None
    assert notify.await_args.kwargs == {
        "sandbox_id": "sandbox-deleted",
        "replacement_attempted": True,
    }
