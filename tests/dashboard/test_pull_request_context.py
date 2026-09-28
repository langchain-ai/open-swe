from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.github import pull_request_context
from agent.threads import handlers
from tests.conftest import patch_thread_module


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
