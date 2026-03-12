"""Tests for the open_pr_if_needed after-agent middleware.

Verifies that the safety net middleware correctly fires (or skips) based on
the success value from commit_and_open_pr tool results.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.middleware.open_pr import _extract_pr_params_from_messages, open_pr_if_needed


class TestExtractPrParamsFromMessages:
    """Tests for the helper that parses commit_and_open_pr tool results."""

    def test_returns_none_when_no_commit_and_open_pr_message(self) -> None:
        messages = [
            HumanMessage(content="fix the bug"),
            AIMessage(content="sure"),
            ToolMessage(content="done", tool_call_id="1", name="bash"),
        ]
        assert _extract_pr_params_from_messages(messages) is None

    def test_returns_none_for_empty_messages(self) -> None:
        assert _extract_pr_params_from_messages([]) is None

    def test_returns_payload_from_commit_and_open_pr_success(self) -> None:
        payload = {"success": True, "error": None, "pr_url": "https://github.com/org/repo/pull/42"}
        messages = [
            ToolMessage(
                content=json.dumps(payload),
                tool_call_id="1",
                name="commit_and_open_pr",
            )
        ]
        result = _extract_pr_params_from_messages(messages)
        assert result == payload

    def test_returns_payload_from_commit_and_open_pr_failure(self) -> None:
        payload = {"success": False, "error": "Git push failed: non-fast-forward", "pr_url": None}
        messages = [
            ToolMessage(
                content=json.dumps(payload),
                tool_call_id="1",
                name="commit_and_open_pr",
            )
        ]
        result = _extract_pr_params_from_messages(messages)
        assert result == payload

    def test_returns_last_commit_and_open_pr_message_when_multiple(self) -> None:
        first_payload = {"success": False, "error": "Git push failed", "pr_url": None}
        second_payload = {"success": True, "error": None, "pr_url": "https://github.com/pr/2"}
        messages = [
            ToolMessage(
                content=json.dumps(first_payload),
                tool_call_id="1",
                name="commit_and_open_pr",
            ),
            ToolMessage(
                content=json.dumps(second_payload),
                tool_call_id="2",
                name="commit_and_open_pr",
            ),
        ]
        result = _extract_pr_params_from_messages(messages)
        # reversed() returns the last one first
        assert result == second_payload

    def test_ignores_other_tool_names(self) -> None:
        messages = [
            ToolMessage(content='{"success": true}', tool_call_id="1", name="bash"),
            ToolMessage(content='{"success": true}', tool_call_id="2", name="git_push"),
        ]
        assert _extract_pr_params_from_messages(messages) is None


class TestOpenPrIfNeededMiddleware:
    """Tests for the open_pr_if_needed after-agent safety net middleware.

    The middleware should:
    - Return None (skip) when commit_and_open_pr succeeded (success=True)
    - Proceed (attempt to create PR) when commit_and_open_pr failed (success=False)
    - Return None (skip) when no commit_and_open_pr call is found in messages
    """

    def _make_runtime(self) -> MagicMock:
        return MagicMock()

    def _make_state(self, messages: list) -> dict:
        return {"messages": messages}

    def test_skips_when_commit_and_open_pr_succeeded(self) -> None:
        """When success=True, the tool handled everything — middleware should be a no-op."""
        payload = {"success": True, "error": None, "pr_url": "https://github.com/org/repo/pull/42"}
        state = self._make_state(
            [
                HumanMessage(content="fix the bug"),
                ToolMessage(
                    content=json.dumps(payload),
                    tool_call_id="1",
                    name="commit_and_open_pr",
                ),
            ]
        )

        with patch(
            "agent.middleware.open_pr.get_config",
            return_value={"configurable": {"thread_id": "thread-success"}},
        ):
            result = open_pr_if_needed.after_agent(state, self._make_runtime())

        assert result is None

    @pytest.mark.asyncio
    async def test_proceeds_when_commit_and_open_pr_failed_git_push(self) -> None:
        """When success=False due to git push failure, safety net should attempt PR creation."""
        payload = {
            "success": False,
            "error": "Git push failed: Updates were rejected because the remote contains work",
            "pr_url": None,
        }
        state = self._make_state(
            [
                HumanMessage(content="fix the bug"),
                ToolMessage(
                    content=json.dumps(payload),
                    tool_call_id="1",
                    name="commit_and_open_pr",
                ),
            ]
        )

        with patch(
            "agent.middleware.open_pr.get_config",
            return_value={
                "configurable": {
                    "thread_id": "thread-push-fail",
                    "repo": {"owner": "org", "name": "repo"},
                }
            },
        ):
            with patch(
                "agent.middleware.open_pr.get_sandbox_backend",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ) as mock_sandbox:
                with patch(
                    "agent.middleware.open_pr.git_has_uncommitted_changes",
                    return_value=True,
                ):
                    with patch(
                        "agent.middleware.open_pr.git_fetch_origin",
                        return_value=None,
                    ):
                        with patch(
                            "agent.middleware.open_pr.git_has_unpushed_commits",
                            return_value=False,
                        ):
                            # Middleware should NOT short-circuit; it reaches sandbox logic
                            # We verify get_sandbox_backend was called (safety net fired)
                            await open_pr_if_needed.aafter_agent(state, self._make_runtime())

        # The safety net fired: get_sandbox_backend was called
        mock_sandbox.assert_called_once_with("thread-push-fail")

    @pytest.mark.asyncio
    async def test_proceeds_when_commit_and_open_pr_failed_pr_creation(self) -> None:
        """When success=False due to PR creation failure, safety net should attempt PR creation."""
        payload = {
            "success": False,
            "error": "Failed to create GitHub PR",
            "pr_url": None,
        }
        state = self._make_state(
            [
                HumanMessage(content="fix the bug"),
                ToolMessage(
                    content=json.dumps(payload),
                    tool_call_id="1",
                    name="commit_and_open_pr",
                ),
            ]
        )

        with patch(
            "agent.middleware.open_pr.get_config",
            return_value={
                "configurable": {
                    "thread_id": "thread-pr-fail",
                    "repo": {"owner": "org", "name": "repo"},
                }
            },
        ):
            with patch(
                "agent.middleware.open_pr.get_sandbox_backend",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ) as mock_sandbox:
                with patch(
                    "agent.middleware.open_pr.git_has_uncommitted_changes",
                    return_value=False,
                ):
                    with patch(
                        "agent.middleware.open_pr.git_fetch_origin",
                        return_value=None,
                    ):
                        with patch(
                            "agent.middleware.open_pr.git_has_unpushed_commits",
                            return_value=True,
                        ):
                            await open_pr_if_needed.aafter_agent(state, self._make_runtime())

        # The safety net fired: get_sandbox_backend was called
        mock_sandbox.assert_called_once_with("thread-pr-fail")

    def test_skips_when_no_commit_and_open_pr_call_found(self) -> None:
        """When no commit_and_open_pr call exists in messages, middleware returns None."""
        state = self._make_state(
            [
                HumanMessage(content="fix the bug"),
                ToolMessage(content="edited file", tool_call_id="1", name="bash"),
                AIMessage(content="done"),
            ]
        )

        with patch(
            "agent.middleware.open_pr.get_config",
            return_value={"configurable": {"thread_id": "thread-no-call"}},
        ):
            result = open_pr_if_needed.after_agent(state, self._make_runtime())

        assert result is None

    @pytest.mark.asyncio
    async def test_does_not_skip_when_success_is_false_not_missing(self) -> None:
        """Regression: key-existence check `'success' in payload` was always True.

        This test confirms the fix: checking the VALUE via `.get('success')` means
        a payload with success=False will NOT trigger the early return.
        """
        payload = {
            "success": False,
            "error": "Git push failed: remote rejected (permission denied)",
            "pr_url": None,
        }
        state = self._make_state(
            [
                ToolMessage(
                    content=json.dumps(payload),
                    tool_call_id="1",
                    name="commit_and_open_pr",
                )
            ]
        )

        reached_sandbox_call = False

        async def fake_get_sandbox(thread_id: str):
            nonlocal reached_sandbox_call
            reached_sandbox_call = True
            return None  # return None so middleware bails out early after this point

        with patch(
            "agent.middleware.open_pr.get_config",
            return_value={
                "configurable": {
                    "thread_id": "thread-regression",
                    "repo": {"owner": "org", "name": "repo"},
                }
            },
        ):
            with patch(
                "agent.middleware.open_pr.get_sandbox_backend", side_effect=fake_get_sandbox
            ):
                await open_pr_if_needed.aafter_agent(state, self._make_runtime())

        # If the old buggy `"success" in pr_payload` check was used, the middleware
        # would have returned None before reaching get_sandbox_backend.
        assert reached_sandbox_call, (
            "Safety net middleware returned early due to key-existence check bug; "
            "fix should use pr_payload.get('success') to check the VALUE"
        )
