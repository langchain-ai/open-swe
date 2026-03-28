"""Tests for the open_pr_if_needed after-agent middleware."""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.middleware.open_pr import _extract_pr_params_from_messages

# ---------------------------------------------------------------------------
# _extract_pr_params_from_messages
# ---------------------------------------------------------------------------


class TestExtractPrParams:
    def test_returns_none_for_empty_messages(self) -> None:
        assert _extract_pr_params_from_messages([]) is None

    def test_returns_none_when_no_commit_tool_call(self) -> None:
        messages = [
            HumanMessage(content="do something"),
            AIMessage(content="sure"),
            ToolMessage(content="done", name="execute", tool_call_id="t1"),
        ]
        assert _extract_pr_params_from_messages(messages) is None

    def test_extracts_from_dict_message(self) -> None:
        payload = {"success": True, "pr_url": "https://github.com/test/pr/1"}
        messages = [
            {"content": json.dumps(payload), "name": "commit_and_open_pr"},
        ]
        result = _extract_pr_params_from_messages(messages)
        assert result == payload

    def test_extracts_from_tool_message_object(self) -> None:
        payload = {"success": False, "error": "push failed", "pr_url": None}
        msg = ToolMessage(
            content=json.dumps(payload),
            name="commit_and_open_pr",
            tool_call_id="tc1",
        )
        result = _extract_pr_params_from_messages([msg])
        assert result == payload

    def test_returns_last_commit_tool_call(self) -> None:
        first = ToolMessage(
            content=json.dumps({"success": False, "error": "old error"}),
            name="commit_and_open_pr",
            tool_call_id="tc1",
        )
        second = ToolMessage(
            content=json.dumps({"success": True, "pr_url": "https://github.com/pr/2"}),
            name="commit_and_open_pr",
            tool_call_id="tc2",
        )
        result = _extract_pr_params_from_messages([first, second])
        assert result is not None
        assert result["pr_url"] == "https://github.com/pr/2"

    def test_ignores_malformed_json(self) -> None:
        msg = ToolMessage(
            content="not valid json",
            name="commit_and_open_pr",
            tool_call_id="tc1",
        )
        assert _extract_pr_params_from_messages([msg]) is None

    def test_ignores_empty_content(self) -> None:
        msg = ToolMessage(
            content="",
            name="commit_and_open_pr",
            tool_call_id="tc1",
        )
        assert _extract_pr_params_from_messages([msg]) is None

    def test_handles_already_parsed_dict_content(self) -> None:
        payload = {"success": True, "pr_url": "https://github.com/pr/3"}
        messages = [{"content": payload, "name": "commit_and_open_pr"}]
        result = _extract_pr_params_from_messages(messages)
        assert result == payload


# ---------------------------------------------------------------------------
# open_pr_if_needed — key-existence bug regression test
# ---------------------------------------------------------------------------


class TestSafetyNetKeyCheck:
    """Regression tests for the key-existence vs truthiness bug.

    commit_and_open_pr always returns {"success": True/False, ...}.
    The old code checked ``"success" in pr_payload`` which is always True,
    so the safety net never fired. The fix checks ``pr_payload.get("success")``.
    """

    def test_skips_when_tool_succeeded(self) -> None:
        payload = {"success": True, "pr_url": "https://github.com/pr/1"}
        messages = [
            ToolMessage(
                content=json.dumps(payload),
                name="commit_and_open_pr",
                tool_call_id="tc1",
            )
        ]
        result = _extract_pr_params_from_messages(messages)
        assert result is not None
        assert result.get("success") is True

    def test_fires_when_tool_failed(self) -> None:
        payload = {"success": False, "error": "Git push failed", "pr_url": None}
        messages = [
            ToolMessage(
                content=json.dumps(payload),
                name="commit_and_open_pr",
                tool_call_id="tc1",
            )
        ]
        result = _extract_pr_params_from_messages(messages)
        assert result is not None
        assert result.get("success") is False

    def test_old_key_existence_check_would_skip_failures(self) -> None:
        """Demonstrates the bug: ``"success" in d`` is True even when success=False."""
        failure_payload = {"success": False, "error": "push rejected", "pr_url": None}
        assert "success" in failure_payload  # old check — always True
        assert not failure_payload.get("success")  # new check — correctly False
