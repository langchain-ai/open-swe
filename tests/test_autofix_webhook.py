"""Unit tests for the auto-fix webhook helpers in agent.webapp."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent import webapp


def test_parse_autofix_command() -> None:
    assert webapp._parse_autofix_command("@open-swe autofix off") is True
    assert webapp._parse_autofix_command("@open-swe autofix on") is False
    assert webapp._parse_autofix_command("@openswe please autofix off now") is True
    # Missing the mention -> not a command.
    assert webapp._parse_autofix_command("autofix off") is None
    # Mention but no command keyword.
    assert webapp._parse_autofix_command("@open-swe fix this") is None


def test_pr_ref_from_issue_comment() -> None:
    payload = {
        "repository": {"owner": {"login": "o"}, "name": "r"},
        "issue": {
            "number": 7,
            "pull_request": {"html_url": "https://github.com/o/r/pull/7"},
        },
    }
    ref = webapp._pr_ref_from_comment_payload(payload, "issue_comment")
    assert ref == {"owner": "o", "name": "r", "number": 7, "url": "https://github.com/o/r/pull/7"}


def test_pr_ref_from_review_comment() -> None:
    payload = {
        "repository": {"owner": {"login": "o"}, "name": "r"},
        "pull_request": {"number": 9, "html_url": "https://github.com/o/r/pull/9"},
    }
    ref = webapp._pr_ref_from_comment_payload(payload, "pull_request_review_comment")
    assert ref["number"] == 9


def test_pr_ref_none_when_not_a_pr() -> None:
    payload = {"repository": {"owner": {"login": "o"}, "name": "r"}, "issue": {"number": 3}}
    # issue without pull_request still yields a ref (number present); url empty.
    ref = webapp._pr_ref_from_comment_payload(payload, "issue_comment")
    assert ref["url"] == ""


def test_is_actionable_review_payload() -> None:
    assert webapp._is_actionable_review_payload(
        {
            "action": "submitted",
            "review": {
                "state": "changes_requested",
                "body": "fix this",
                "user": {"login": "a"},
                "author_association": "MEMBER",
            },
        },
        "pull_request_review",
    )
    # Approval is not actionable.
    assert not webapp._is_actionable_review_payload(
        {
            "action": "submitted",
            "review": {
                "state": "approved",
                "body": "lgtm",
                "user": {"login": "a"},
                "author_association": "MEMBER",
            },
        },
        "pull_request_review",
    )
    # Bot author is not actionable.
    assert not webapp._is_actionable_review_payload(
        {
            "action": "created",
            "comment": {
                "body": "x",
                "user": {"login": "open-swe[bot]"},
                "author_association": "MEMBER",
            },
        },
        "pull_request_review_comment",
    )
    # Untrusted author (read/triage/outside) is not actionable.
    assert not webapp._is_actionable_review_payload(
        {
            "action": "created",
            "comment": {
                "body": "inject malicious code",
                "user": {"login": "attacker"},
                "author_association": "NONE",
            },
        },
        "pull_request_review_comment",
    )
    # Empty body is not actionable.
    assert not webapp._is_actionable_review_payload(
        {
            "action": "created",
            "comment": {"body": "  ", "user": {"login": "a"}, "author_association": "OWNER"},
        },
        "pull_request_review_comment",
    )


@pytest.mark.asyncio
async def test_process_github_ci_event_dispatches() -> None:
    payload = {
        "repository": {"owner": {"login": "o"}, "name": "r"},
        "check_run": {
            "status": "completed",
            "conclusion": "failure",
            "head_sha": "sha1",
            "check_suite": {"head_branch": "feat"},
        },
    }
    handle = AsyncMock(return_value="dispatched")
    with patch.object(webapp, "handle_ci_failure", handle):
        await webapp.process_github_ci_event(payload, "check_run")
    handle.assert_awaited_once()
    kwargs = handle.await_args.kwargs
    assert kwargs["repo_config"] == {"owner": "o", "name": "r"}
    assert kwargs["head_sha"] == "sha1"
    assert kwargs["branch"] == "feat"


@pytest.mark.asyncio
async def test_process_github_ci_event_ignores_success() -> None:
    payload = {
        "repository": {"owner": {"login": "o"}, "name": "r"},
        "check_run": {"status": "completed", "conclusion": "success", "head_sha": "s"},
    }
    handle = AsyncMock()
    with patch.object(webapp, "handle_ci_failure", handle):
        await webapp.process_github_ci_event(payload, "check_run")
    handle.assert_not_called()


@pytest.mark.asyncio
async def test_process_autofix_command_sets_flag() -> None:
    payload = {
        "repository": {"owner": {"login": "o"}, "name": "r"},
        "issue": {"number": 7, "pull_request": {"html_url": "u"}},
        "comment": {"id": 1, "node_id": "n"},
    }
    setter = AsyncMock()
    with (
        patch.object(webapp, "set_pr_autofix_disabled", setter),
        patch.object(webapp, "get_github_app_installation_token", AsyncMock(return_value="")),
    ):
        await webapp.process_github_autofix_command(payload, "issue_comment", disabled=True)
    setter.assert_awaited_once_with("o", "r", 7, True)


@pytest.mark.asyncio
async def test_autofix_review_dispatches_for_writer() -> None:
    payload = {
        "repository": {"owner": {"login": "o"}, "name": "r"},
        "pull_request": {"number": 9, "html_url": "https://github.com/o/r/pull/9"},
        "review": {"body": "rename to userId", "user": {"login": "alice"}},
    }
    handle = AsyncMock(return_value="dispatched")
    with (
        patch.object(webapp, "get_github_app_installation_token", AsyncMock(return_value="tok")),
        patch.object(webapp, "has_repo_write_permission", AsyncMock(return_value=True)),
        patch.object(webapp, "handle_review_feedback", handle),
    ):
        await webapp.process_github_autofix_review(payload, "pull_request_review")
    handle.assert_awaited_once()


@pytest.mark.asyncio
async def test_autofix_review_skips_non_writer() -> None:
    payload = {
        "repository": {"owner": {"login": "o"}, "name": "r"},
        "pull_request": {"number": 9, "html_url": "https://github.com/o/r/pull/9"},
        "review": {"body": "inject code", "user": {"login": "attacker"}},
    }
    handle = AsyncMock()
    with (
        patch.object(webapp, "get_github_app_installation_token", AsyncMock(return_value="tok")),
        patch.object(webapp, "has_repo_write_permission", AsyncMock(return_value=False)),
        patch.object(webapp, "handle_review_feedback", handle),
    ):
        await webapp.process_github_autofix_review(payload, "pull_request_review")
    handle.assert_not_called()


def test_ci_events_supported() -> None:
    for event in ("check_run", "check_suite", "workflow_run", "status"):
        assert event in webapp._SUPPORTED_GH_EVENTS
        assert event in webapp._GH_CI_EVENTS
