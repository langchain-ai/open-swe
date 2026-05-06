"""Tests for the unhandled-error notifier middleware and the model retry policy."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.middleware.notify_unhandled_error import notify_unhandled_error


class _FakeOverloadedError(Exception):
    """Stand-in for ``anthropic.OverloadedError`` (subclass of ``APIStatusError``)."""


class TestNotifyUnhandledError:
    def _make_runtime(self) -> MagicMock:
        return MagicMock()

    @pytest.mark.asyncio
    async def test_posts_slack_reply_when_state_has_error_exception(self) -> None:
        state = {
            "messages": [HumanMessage(content="do the thing")],
            "error": _FakeOverloadedError("Anthropic 529 Overloaded"),
        }

        with (
            patch(
                "agent.middleware.notify_unhandled_error.get_config",
                return_value={
                    "configurable": {
                        "slack_thread": {"channel_id": "C123", "thread_ts": "171.123"}
                    }
                },
            ),
            patch(
                "agent.middleware.notify_unhandled_error.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            result = await notify_unhandled_error.aafter_agent(state, self._make_runtime())

        assert result is None
        mock_post.assert_awaited_once()
        assert mock_post.await_args.args[0:2] == ("C123", "171.123")
        assert "_FakeOverloadedError" in mock_post.await_args.args[2]
        assert "provider error" in mock_post.await_args.args[2].lower()

    @pytest.mark.asyncio
    async def test_posts_exactly_one_notification_when_retries_exhausted(self) -> None:
        # Simulates the after-retry state: model invocation raised, the wrapper
        # recorded the error on state, and the agent terminated.
        state = {
            "messages": [AIMessage(content="Provider error: OverloadedError: 529 Overloaded")],
        }

        with (
            patch(
                "agent.middleware.notify_unhandled_error.get_config",
                return_value={
                    "configurable": {
                        "slack_thread": {"channel_id": "C1", "thread_ts": "1.1"},
                        "linear_issue": {"id": "issue-xyz"},
                    }
                },
            ),
            patch(
                "agent.middleware.notify_unhandled_error.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_slack,
            patch(
                "agent.middleware.notify_unhandled_error.comment_on_linear_issue",
                new_callable=AsyncMock,
            ) as mock_linear,
        ):
            await notify_unhandled_error.aafter_agent(state, self._make_runtime())

        # Slack wins (configured first); Linear is not double-posted.
        mock_slack.assert_awaited_once()
        mock_linear.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_error_marker(self) -> None:
        state = {"messages": [AIMessage(content="all done!")]}

        with (
            patch(
                "agent.middleware.notify_unhandled_error.get_config",
                return_value={
                    "configurable": {
                        "slack_thread": {"channel_id": "C1", "thread_ts": "1.1"}
                    }
                },
            ),
            patch(
                "agent.middleware.notify_unhandled_error.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            await notify_unhandled_error.aafter_agent(state, self._make_runtime())

        mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_surface_configured(self) -> None:
        state = {"error": {"type": "OverloadedError", "message": "529"}}

        with (
            patch(
                "agent.middleware.notify_unhandled_error.get_config",
                return_value={"configurable": {}},
            ),
            patch(
                "agent.middleware.notify_unhandled_error.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_slack,
            patch(
                "agent.middleware.notify_unhandled_error.comment_on_linear_issue",
                new_callable=AsyncMock,
            ) as mock_linear,
        ):
            await notify_unhandled_error.aafter_agent(state, self._make_runtime())

        mock_slack.assert_not_called()
        mock_linear.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_through_to_linear_when_no_slack(self) -> None:
        state = {"error": {"type": "APIConnectionError", "message": "DNS failure"}}

        with (
            patch(
                "agent.middleware.notify_unhandled_error.get_config",
                return_value={
                    "configurable": {"linear_issue": {"id": "issue-abc"}},
                },
            ),
            patch(
                "agent.middleware.notify_unhandled_error.post_slack_thread_reply",
                new_callable=AsyncMock,
            ) as mock_slack,
            patch(
                "agent.middleware.notify_unhandled_error.comment_on_linear_issue",
                new_callable=AsyncMock,
            ) as mock_linear,
        ):
            await notify_unhandled_error.aafter_agent(state, self._make_runtime())

        mock_slack.assert_not_called()
        mock_linear.assert_awaited_once()
        assert mock_linear.await_args.args[0] == "issue-abc"
        assert "APIConnectionError" in mock_linear.await_args.args[1]


class TestMakeModelRetry:
    def test_make_model_wraps_with_retry(self) -> None:
        """``make_model`` must apply ``with_retry`` so transient provider
        errors don't terminate the agent on the first failure."""
        from agent.utils import model as model_module

        fake_inner = MagicMock(name="inner_model")
        fake_inner.with_retry = MagicMock(return_value="retrying-model")
        with patch.object(model_module, "init_chat_model", return_value=fake_inner):
            result = model_module.make_model("anthropic:claude-3-5-sonnet-20241022")

        assert result == "retrying-model"
        fake_inner.with_retry.assert_called_once()
        kwargs = fake_inner.with_retry.call_args.kwargs
        # Retry on transient provider errors, with backoff and a bounded attempt count.
        assert kwargs["wait_exponential_jitter"] is True
        assert kwargs["stop_after_attempt"] >= 2
        retry_types = kwargs["retry_if_exception_type"]
        # Anthropic + OpenAI status/connection errors must all be in the retry set.
        import anthropic
        import openai

        assert anthropic.APIStatusError in retry_types
        assert anthropic.APIConnectionError in retry_types
        assert openai.APIStatusError in retry_types
        assert openai.APIConnectionError in retry_types

    @pytest.mark.asyncio
    async def test_retry_invokes_model_more_than_once_on_transient_error(self) -> None:
        """End-to-end: a runnable wrapped via ``with_retry(retry_if_exception_type=...)``
        retries on the configured exception classes. We verify the wrapped
        model is invoked >1 time when the first call raises an Anthropic
        ``APIStatusError`` subclass."""
        import anthropic
        from langchain_core.runnables import RunnableLambda

        attempts = {"n": 0}

        def _flaky(_input):
            attempts["n"] += 1
            if attempts["n"] < 2:
                # Simulate Anthropic 529 OverloadedError (subclass of APIStatusError).
                # Construct via __new__ to avoid the SDK's strict __init__ signature.
                err = anthropic.APIStatusError.__new__(anthropic.APIStatusError)
                err.message = "Overloaded"  # type: ignore[attr-defined]
                Exception.__init__(err, "Overloaded")
                raise err
            return "ok"

        runnable = RunnableLambda(_flaky).with_retry(
            retry_if_exception_type=(anthropic.APIStatusError,),
            wait_exponential_jitter=False,
            stop_after_attempt=4,
        )

        result = await runnable.ainvoke("hi")
        assert result == "ok"
        assert attempts["n"] > 1
