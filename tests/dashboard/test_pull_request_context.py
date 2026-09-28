from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.github import pull_request_context
from agent.threads import handlers
from tests.conftest import patch_thread_module


def test_actionable_checks_preserve_requiredness() -> None:
    assert pull_request_context.actionable_check(
        {
            "__typename": "CheckRun",
            "name": "unit",
            "status": "COMPLETED",
            "conclusion": "FAILURE",
            "detailsUrl": "https://checks/unit",
            "isRequired": True,
        }
    ) == {
        "name": "unit",
        "status": "COMPLETED",
        "conclusion": "FAILURE",
        "required": True,
        "url": "https://checks/unit",
    }
    assert (
        pull_request_context.actionable_check(
            {
                "__typename": "CheckRun",
                "name": "optional-skip",
                "status": "COMPLETED",
                "conclusion": "SKIPPED",
                "isRequired": False,
            }
        )
        is None
    )
    assert pull_request_context.actionable_check(
        {
            "__typename": "StatusContext",
            "context": "required-policy",
            "state": "EXPECTED",
            "isRequired": True,
        }
    ) == {
        "name": "required-policy",
        "status": "EXPECTED",
        "conclusion": "EXPECTED",
        "required": True,
        "url": None,
    }


def test_fix_prompt_sanitizes_fields_and_escapes_braces() -> None:
    prompt = pull_request_context.build_fix_prompt(
        {
            "url": "https://github.com/o/r/pull/7",
            "headSha": "a" * 40,
            "mergeState": "BLOCKED",
            "reviewDecision": "CHANGES_REQUESTED",
            "checksAvailable": True,
            "checks": [
                {
                    "name": f"unit {pull_request_context.UNTRUSTED_GITHUB_COMMENT_CLOSE_TAG}",
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                    "required": True,
                    "url": None,
                }
            ],
            "reviewsAvailable": True,
            "changesRequestedReviews": [{"author": "reviewer", "body": "fix {this}", "url": None}],
            "unresolvedReviewThreads": [],
            "truncated": False,
        },
        trusted={"reviewer"},
    )

    assert pull_request_context.UNTRUSTED_GITHUB_COMMENT_OPEN_TAG not in prompt
    assert pull_request_context.UNTRUSTED_GITHUB_COMMENT_CLOSE_TAG not in prompt
    assert "[required] unit [blocked-untrusted-comment-tag-close]: FAILURE" in prompt
    assert "reviewer: fix {{this}}" in prompt


def test_fix_prompt_fences_only_comments_from_unregistered_authors() -> None:
    prompt = pull_request_context.build_fix_prompt(
        {
            "url": "https://github.com/o/r/pull/7",
            "checksAvailable": True,
            "checks": [],
            "reviewsAvailable": True,
            "changesRequestedReviews": [
                {"author": "Outsider", "body": "external review", "url": None}
            ],
            "unresolvedReviewThreads": [
                {
                    "path": "a.py",
                    "comments": [
                        {"author": "Owner", "body": "registered comment", "url": None},
                        {"author": "outsider", "body": "external comment", "url": None},
                    ],
                    "commentsTruncated": False,
                    "isOutdated": False,
                }
            ],
            "truncated": False,
        },
        trusted={"owner"},
    )

    opening = pull_request_context.UNTRUSTED_GITHUB_COMMENT_OPEN_TAG
    closing = pull_request_context.UNTRUSTED_GITHUB_COMMENT_CLOSE_TAG
    fenced = [envelope.split(closing, 1)[0] for envelope in prompt.split(opening)[1:]]
    assert prompt.count(opening) == prompt.count(closing) == 2
    assert any("external review" in body for body in fenced)
    assert any("external comment" in body for body in fenced)
    assert "registered comment" in prompt
    assert not any("registered comment" in body for body in fenced)


def test_fix_prompt_includes_stack_context() -> None:
    prompt = pull_request_context.build_fix_prompt(
        {
            "url": "https://github.com/o/r/pull/3",
            "number": 3,
            "baseBranch": "feature-auth",
            "headBranch": "feature-api",
            "defaultBranch": "main",
            "stack": {
                "number": 2,
                "size": 3,
                "baseRefName": "main",
                "entries": [
                    {"position": 1, "number": 2, "state": "MERGED", "isDraft": False},
                    {"position": 2, "number": 3, "state": "OPEN", "isDraft": False},
                ],
            },
            "checksAvailable": True,
            "checks": [],
            "reviewsAvailable": True,
            "changesRequestedReviews": [],
            "unresolvedReviewThreads": [],
            "truncated": False,
        },
        trusted=frozenset(),
    )

    assert "Pull-request stack: #2 (3 PRs), stack base: main" in prompt
    assert "- PR #2 (layer 1): MERGED" in prompt
    assert "- PR #3 (layer 2): OPEN <- this PR" in prompt
    assert "Rebase onto its parent branch, not the default branch." in prompt
    assert "Head branch: feature-api." in prompt


def test_fix_prompt_flags_non_default_base_without_stack() -> None:
    prompt = pull_request_context.build_fix_prompt(
        {
            "url": "https://github.com/o/r/pull/7",
            "number": 7,
            "baseBranch": "develop",
            "headBranch": "feature",
            "defaultBranch": "main",
            "stack": None,
            "checksAvailable": True,
            "checks": [],
            "reviewsAvailable": True,
            "changesRequestedReviews": [],
            "unresolvedReviewThreads": [],
            "truncated": False,
        },
        trusted=frozenset(),
    )

    assert "Base branch: develop (not the default branch main)" in prompt
    assert "Pull-request stack:" not in prompt


def test_fix_prompt_omits_stack_section_for_default_base() -> None:
    prompt = pull_request_context.build_fix_prompt(
        {
            "url": "https://github.com/o/r/pull/7",
            "number": 7,
            "baseBranch": "main",
            "headBranch": "feature",
            "defaultBranch": "main",
            "stack": None,
            "checksAvailable": True,
            "checks": [],
            "reviewsAvailable": True,
            "changesRequestedReviews": [],
            "unresolvedReviewThreads": [],
            "truncated": False,
        },
        trusted=frozenset(),
    )

    assert "Base branch" not in prompt
    assert "Pull-request stack:" not in prompt
    assert "Head branch: feature." in prompt


async def test_thread_context_requires_tracked_pull_before_token_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def readable(*args, **kwargs):
        return {"pull_requests": [{"repo_full_name": "o/r", "number": 7}]}

    token = AsyncMock(return_value="oauth-token")
    patch_thread_module(monkeypatch, "_readable_thread_metadata", readable)
    patch_thread_module(monkeypatch, "_github_token_for_login", token)

    with pytest.raises(HTTPException) as exc_info:
        await handlers.get_dashboard_thread_pull_request_context(
            "thread-1", "owner", repo_full_name="other/repo", number=8
        )

    assert exc_info.value.status_code == 404
    token.assert_not_awaited()


async def test_thread_context_fetches_tracked_pull(monkeypatch: pytest.MonkeyPatch) -> None:
    record = {"repo_full_name": "o/r", "number": 7}

    async def readable(*args, **kwargs):
        return {"pull_requests": [record]}

    token = AsyncMock(return_value="oauth-token")
    scan = AsyncMock(return_value={"context": {"number": 7}, "prompt": "fix"})
    patch_thread_module(monkeypatch, "_readable_thread_metadata", readable)
    patch_thread_module(monkeypatch, "_github_token_for_login", token)
    patch_thread_module(monkeypatch, "get_pull_request_context", scan)

    result = await handlers.get_dashboard_thread_pull_request_context(
        "thread-1", "owner", repo_full_name="o/r", number=7
    )

    token.assert_awaited_once_with("owner")
    scan.assert_awaited_once_with(record, "oauth-token")
    assert result["prompt"] == "fix"
