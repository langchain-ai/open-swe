from __future__ import annotations

from unittest.mock import patch

from agent.tools.github_comment import github_comment
from agent.tools.linear_comment import linear_comment
from agent.tools.slack_thread_reply import slack_thread_reply


# -- github_comment -------------------------------------------------------


def test_github_comment_missing_issue_number():
    with patch("agent.tools.github_comment.get_config") as mock_config:
        mock_config.return_value = {"configurable": {"repo": {"owner": "o", "name": "r"}}}
        result = github_comment("hello", issue_number=0)
        assert result["success"] is False
        assert "issue_number" in result["error"]


def test_github_comment_missing_repo_config():
    with patch("agent.tools.github_comment.get_config") as mock_config:
        mock_config.return_value = {"configurable": {}}
        result = github_comment("hello", issue_number=42)
        assert result["success"] is False
        assert "repo" in result["error"].lower()


def test_github_comment_empty_message():
    with patch("agent.tools.github_comment.get_config") as mock_config:
        mock_config.return_value = {"configurable": {"repo": {"owner": "o", "name": "r"}}}
        result = github_comment("   ", issue_number=42)
        assert result["success"] is False
        assert "empty" in result["error"].lower()


def test_github_comment_no_token():
    with patch("agent.tools.github_comment.get_config") as mock_config:
        mock_config.return_value = {"configurable": {"repo": {"owner": "o", "name": "r"}}}
        with patch("agent.tools.github_comment.asyncio.run", return_value=None):
            result = github_comment("Fix applied", issue_number=42)
            assert result["success"] is False
            assert "token" in result["error"].lower()


def test_github_comment_success():
    with patch("agent.tools.github_comment.get_config") as mock_config:
        mock_config.return_value = {"configurable": {"repo": {"owner": "o", "name": "r"}}}
        with patch("agent.tools.github_comment.asyncio.run") as mock_run:
            # First call: get_github_app_installation_token → returns token
            # Second call: post_github_comment → returns True
            mock_run.side_effect = ["ghp_token123", True]
            result = github_comment("Fix applied", issue_number=42)
            assert result["success"] is True


# -- linear_comment -------------------------------------------------------


def test_linear_comment_success():
    with patch("agent.tools.linear_comment.asyncio.run", return_value=True):
        result = linear_comment("Task complete", "TICKET-123")
        assert result["success"] is True


def test_linear_comment_failure():
    with patch("agent.tools.linear_comment.asyncio.run", return_value=False):
        result = linear_comment("Task complete", "TICKET-123")
        assert result["success"] is False


# -- slack_thread_reply ---------------------------------------------------


def test_slack_thread_reply_missing_channel():
    with patch("agent.tools.slack_thread_reply.get_config") as mock_config:
        mock_config.return_value = {"configurable": {"slack_thread": {}}}
        result = slack_thread_reply("hello")
        assert result["success"] is False
        assert "channel_id" in result["error"]


def test_slack_thread_reply_missing_thread_ts():
    with patch("agent.tools.slack_thread_reply.get_config") as mock_config:
        mock_config.return_value = {
            "configurable": {"slack_thread": {"channel_id": "C123"}}
        }
        result = slack_thread_reply("hello")
        assert result["success"] is False


def test_slack_thread_reply_empty_message():
    with patch("agent.tools.slack_thread_reply.get_config") as mock_config:
        mock_config.return_value = {
            "configurable": {
                "slack_thread": {"channel_id": "C123", "thread_ts": "1234.5678"}
            }
        }
        result = slack_thread_reply("   ")
        assert result["success"] is False
        assert "empty" in result["error"].lower()


def test_slack_thread_reply_success():
    with patch("agent.tools.slack_thread_reply.get_config") as mock_config:
        mock_config.return_value = {
            "configurable": {
                "slack_thread": {"channel_id": "C123", "thread_ts": "1234.5678"}
            }
        }
        with patch("agent.tools.slack_thread_reply.asyncio.run", return_value=True):
            result = slack_thread_reply("Done!")
            assert result["success"] is True


def test_slack_thread_reply_failure():
    with patch("agent.tools.slack_thread_reply.get_config") as mock_config:
        mock_config.return_value = {
            "configurable": {
                "slack_thread": {"channel_id": "C123", "thread_ts": "1234.5678"}
            }
        }
        with patch("agent.tools.slack_thread_reply.asyncio.run", return_value=False):
            result = slack_thread_reply("Done!")
            assert result["success"] is False
