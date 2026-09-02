"""Tests for the Linear trace-URL comment."""

from unittest.mock import AsyncMock, patch

from agent.utils import linear as linear_utils


async def test_posts_resolved_trace_url() -> None:
    with (
        patch.object(
            linear_utils,
            "get_langsmith_trace_url",
            AsyncMock(return_value="https://smith.example/t/thread-1"),
        ),
        patch.object(linear_utils, "comment_on_linear_issue", AsyncMock()) as comment,
    ):
        await linear_utils.post_linear_trace_comment("issue-1", "thread-1", "comment-1")

    assert comment.await_args is not None
    body = comment.await_args.args[1]
    assert body == "On it! [View trace](https://smith.example/t/thread-1)"
    assert "coroutine" not in body


async def test_falls_back_when_trace_url_unresolved() -> None:
    with (
        patch.object(linear_utils, "get_langsmith_trace_url", AsyncMock(return_value=None)),
        patch.object(linear_utils, "comment_on_linear_issue", AsyncMock()) as comment,
    ):
        await linear_utils.post_linear_trace_comment("issue-1", "thread-1", "comment-1")

    assert comment.await_args is not None
    assert comment.await_args.args[1] == "On it!"
