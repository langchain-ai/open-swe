import pytest
from fastapi import HTTPException

from agent.dashboard import review_api
from agent.dashboard.review_api import (
    _ALLOWED_IMAGE_CONTENT_TYPES,
    _finding_counts,
    _is_allowed_image_url,
    _require_image_in_pr,
    _serialize_diff_groups,
    _serialize_finding,
    _thread_review_summary,
    classify_finding,
    get_pr_head_sha,
    reviewer_thread_id,
)
from agent.webhooks.common import generate_reviewer_thread_id


def test_classify_finding():
    assert classify_finding({"severity": "critical", "confidence": "high"}) == "bug"
    assert classify_finding({"severity": "high", "confidence": "high"}) == "bug"
    assert classify_finding({"severity": "high", "confidence": "medium"}) == "investigate"
    assert classify_finding({"severity": "medium", "confidence": "high"}) == "investigate"
    assert classify_finding({"severity": "low", "confidence": "high"}) == "informational"


def test_finding_counts_only_open_in_groups():
    findings = [
        {"id": "f_1", "severity": "high", "confidence": "high", "status": "open"},
        {"id": "f_2", "severity": "medium", "confidence": "high", "status": "open"},
        {"id": "f_3", "severity": "high", "confidence": "high", "status": "resolved"},
        {"id": "f_4", "severity": "low", "confidence": "low", "status": "dismissed"},
    ]
    counts = _finding_counts(findings)
    assert counts == {"open": 2, "resolved": 1, "dismissed": 1, "bugs": 1, "flags": 1}


def test_serialize_finding_outdated():
    finding = {"id": "f_1", "last_confirmed_sha": "aaa"}
    assert _serialize_finding(finding, "bbb")["outdated"] is True
    assert _serialize_finding(finding, "aaa")["outdated"] is False
    assert _serialize_finding(finding, None)["outdated"] is False
    assert _serialize_finding({"id": "f_2"}, "bbb")["outdated"] is False


def test_thread_review_summary():
    thread = {
        "thread_id": "t1",
        "status": "idle",
        "updated_at": "2026-06-10T00:00:00Z",
        "metadata": {
            "kind": "reviewer",
            "pr": {
                "owner": "acme",
                "name": "repo",
                "number": 7,
                "title": "Fix things",
                "head_ref": "fix",
                "base_ref": "main",
            },
            "head_sha": "abc",
            "watch": True,
            "latest_run_status": "success",
            "findings": [{"id": "f_1", "severity": "high", "confidence": "high", "status": "open"}],
        },
    }
    summary = _thread_review_summary(thread)
    assert summary is not None
    assert summary["owner"] == "acme"
    assert summary["number"] == 7
    assert summary["status"] == "idle"
    assert summary["watch"] is True
    assert summary["counts"]["bugs"] == 1


def test_thread_review_summary_requires_pr_meta():
    assert _thread_review_summary({"metadata": {"kind": "reviewer"}}) is None


def test_is_allowed_image_url_accepts_github_hosts():
    assert _is_allowed_image_url("https://github.com/user-attachments/assets/abc-123")
    assert _is_allowed_image_url("https://private-user-images.githubusercontent.com/1/x.png?jwt=y")
    assert _is_allowed_image_url("https://user-images.githubusercontent.com/1/x.png")


def test_is_allowed_image_url_rejects_unsafe_urls():
    # Non-https scheme.
    assert not _is_allowed_image_url("http://github.com/user-attachments/assets/x")
    # github.com but not a user-attachment path.
    assert not _is_allowed_image_url("https://github.com/langchain-ai/open-swe")
    # Arbitrary external host (SSRF guard).
    assert not _is_allowed_image_url("https://evil.example.com/x.png")
    # Lookalike host that merely contains the suffix substring.
    assert not _is_allowed_image_url("https://githubusercontent.com.evil.com/x.png")
    # Internal address.
    assert not _is_allowed_image_url("https://169.254.169.254/latest/meta-data")


def test_image_content_type_allowlist_excludes_svg():
    # SVG can execute script in our origin, so it must never be served.
    assert "image/svg+xml" not in _ALLOWED_IMAGE_CONTENT_TYPES
    assert "image/png" in _ALLOWED_IMAGE_CONTENT_TYPES


@pytest.mark.asyncio
async def test_get_pr_head_sha_returns_head(monkeypatch):
    async def fake_token():
        return "tok"

    async def fake_get(path, token, **kwargs):
        assert path == "/repos/acme/repo/pulls/7"
        return {"head": {"sha": "abc123"}}

    monkeypatch.setattr(review_api, "_require_app_token", fake_token)
    monkeypatch.setattr(review_api, "_github_get", fake_get)
    assert await get_pr_head_sha("acme", "repo", 7) == "abc123"


@pytest.mark.asyncio
async def test_get_pr_head_sha_empty_on_failure(monkeypatch):
    async def fake_token():
        return "tok"

    async def fake_get(path, token, **kwargs):
        raise HTTPException(404, "not found")

    monkeypatch.setattr(review_api, "_require_app_token", fake_token)
    monkeypatch.setattr(review_api, "_github_get", fake_get)
    assert await get_pr_head_sha("acme", "repo", 7) == ""


async def test_require_image_in_pr_rejects_unreferenced_url(monkeypatch):
    async def fake_github_get(path, token, **kwargs):
        return {"body": "see ![diagram](https://x.githubusercontent.com/a.png)"}

    monkeypatch.setattr("agent.dashboard.review_api._github_get", fake_github_get)

    # A URL not present in the PR body (cross-repo IDOR attempt) is rejected.
    with pytest.raises(HTTPException) as exc:
        await _require_image_in_pr(
            "acme", "repo", 7, "https://x.githubusercontent.com/other-repo.png", "tok"
        )
    assert exc.value.status_code == 403

    # A URL actually embedded in the PR body is allowed.
    await _require_image_in_pr("acme", "repo", 7, "https://x.githubusercontent.com/a.png", "tok")


def test_reviewer_thread_id_matches_webapp():
    assert reviewer_thread_id("acme", "repo", 7) == generate_reviewer_thread_id("acme", "repo", 7)


def test_serialize_diff_groups_assigns_index_and_drops_invalid():
    metadata = {
        "diff_groups": {
            "head_sha": "abc",
            "groups": [
                {"title": "Feature", "summary": "Adds it", "files": ["a.py", "b.py"]},
                {"title": "   ", "summary": "x", "files": ["c.py"]},
                {"title": "Empty", "summary": "", "files": []},
                {"title": "Tests", "summary": "Covers it", "files": ["t.py", 5]},
            ],
        }
    }
    groups, stale = _serialize_diff_groups(metadata, "abc")
    assert stale is False
    assert groups == [
        {"index": 1, "title": "Feature", "summary": "Adds it", "files": ["a.py", "b.py"]},
        {"index": 2, "title": "Tests", "summary": "Covers it", "files": ["t.py"]},
    ]


def test_serialize_diff_groups_marks_stale_on_head_mismatch():
    metadata = {
        "diff_groups": {
            "head_sha": "old",
            "groups": [{"title": "T", "summary": "", "files": ["a.py"]}],
        }
    }
    groups, stale = _serialize_diff_groups(metadata, "new")
    assert stale is True
    assert groups[0]["index"] == 1


def test_serialize_diff_groups_handles_missing():
    assert _serialize_diff_groups({}, "abc") == ([], False)
    assert _serialize_diff_groups({"diff_groups": {"groups": "nope"}}, "abc") == ([], False)
