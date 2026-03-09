"""Tests for GitHub PR webhook utilities and webhook endpoint."""

import hashlib
import hmac

from agent.utils.github_pr_webhook import (
    collect_comments_since_last_tag,
    extract_thread_id_from_branch,
    format_review_comment_for_prompt,
    verify_github_signature,
)
from agent.webapp import _is_pr_comment


class TestVerifyGithubSignature:
    def test_returns_true_when_no_secret(self):
        assert verify_github_signature(b"body", "sig", "") is True

    def test_valid_signature(self):
        secret = "test-secret"
        body = b'{"action": "created"}'
        expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        assert verify_github_signature(body, expected, secret) is True

    def test_invalid_signature(self):
        assert verify_github_signature(b"body", "sha256=invalid", "secret") is False


class TestExtractThreadIdFromBranch:
    def test_extracts_thread_id(self):
        assert extract_thread_id_from_branch("open-swe/abc-123-def") == "abc-123-def"

    def test_returns_none_for_non_open_swe_branch(self):
        assert extract_thread_id_from_branch("feature/my-feature") is None

    def test_returns_none_for_empty_string(self):
        assert extract_thread_id_from_branch("") is None

    def test_returns_empty_for_prefix_only(self):
        result = extract_thread_id_from_branch("open-swe/")
        assert result == ""


class TestCollectCommentsSinceLastTag:
    def test_empty_comments(self):
        assert collect_comments_since_last_tag([]) == []

    def test_single_comment_with_tag(self):
        comments = [
            {"id": 1, "body": "Hey @open-swe fix this", "created_at": "2024-01-01T00:00:00Z"},
        ]
        result = collect_comments_since_last_tag(comments, triggering_comment_id=1)
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_collects_from_last_tag(self):
        comments = [
            {"id": 1, "body": "First comment", "created_at": "2024-01-01T00:00:00Z"},
            {"id": 2, "body": "@open-swe do something", "created_at": "2024-01-02T00:00:00Z"},
            {"id": 3, "body": "Follow up", "created_at": "2024-01-03T00:00:00Z"},
            {"id": 4, "body": "@open-swe fix this too", "created_at": "2024-01-04T00:00:00Z"},
        ]
        result = collect_comments_since_last_tag(comments, triggering_comment_id=4)
        assert len(result) == 3
        assert result[0]["id"] == 2

    def test_no_previous_tag_returns_from_trigger(self):
        comments = [
            {"id": 1, "body": "First comment", "created_at": "2024-01-01T00:00:00Z"},
            {"id": 2, "body": "Second comment", "created_at": "2024-01-02T00:00:00Z"},
            {"id": 3, "body": "@open-swe fix this", "created_at": "2024-01-03T00:00:00Z"},
        ]
        result = collect_comments_since_last_tag(comments, triggering_comment_id=3)
        assert len(result) == 1
        assert result[0]["id"] == 3

    def test_case_insensitive_tag(self):
        comments = [
            {"id": 1, "body": "@Open-SWE do this", "created_at": "2024-01-01T00:00:00Z"},
            {"id": 2, "body": "Follow up", "created_at": "2024-01-02T00:00:00Z"},
            {"id": 3, "body": "@open-swe fix this", "created_at": "2024-01-03T00:00:00Z"},
        ]
        result = collect_comments_since_last_tag(comments, triggering_comment_id=3)
        assert len(result) == 3
        assert result[0]["id"] == 1


class TestFormatReviewCommentForPrompt:
    def test_basic_format(self):
        comment = {
            "user": {"login": "testuser"},
            "body": "Please fix this",
            "path": "src/main.py",
            "line": 42,
            "id": 123,
        }
        result = format_review_comment_for_prompt(comment)
        assert "**@testuser**" in result
        assert "comment_id: 123" in result
        assert "`src/main.py`" in result
        assert "line 42" in result
        assert "Please fix this" in result

    def test_line_range(self):
        comment = {
            "user": {"login": "reviewer"},
            "body": "Refactor this block",
            "path": "lib/utils.py",
            "start_line": 10,
            "line": 20,
            "id": 456,
        }
        result = format_review_comment_for_prompt(comment)
        assert "lines 10-20" in result

    def test_no_path(self):
        comment = {
            "user": {"login": "reviewer"},
            "body": "General comment",
            "id": 789,
        }
        result = format_review_comment_for_prompt(comment)
        assert "**@reviewer**" in result
        assert "General comment" in result


class TestIsPrComment:
    def test_pr_comment(self):
        payload = {
            "issue": {
                "number": 1,
                "pull_request": {"url": "https://api.github.com/repos/o/r/pulls/1"},
            }
        }
        assert _is_pr_comment(payload) is True

    def test_issue_comment(self):
        payload = {"issue": {"number": 1}}
        assert _is_pr_comment(payload) is False
