from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openswe.review.reviews import (
    _ALLOWED_IMAGE_CONTENT_TYPES,
    _image_request_headers,
    _is_allowed_image_url,
    _require_image_in_pr,
)


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


def test_is_allowed_image_url_accepts_only_githubs_asset_bucket():
    assert _is_allowed_image_url(
        "https://github-production-user-asset-6210df.s3.amazonaws.com/1/x.png?X-Amz-Signature=y"
    )
    assert not _is_allowed_image_url("https://attacker-bucket.s3.amazonaws.com/x.png")


def test_image_token_only_reaches_githubusercontent():
    assert "Authorization" in _image_request_headers(
        "https://private-user-images.githubusercontent.com/1/x.png", "tok"
    )
    assert "Authorization" not in _image_request_headers(
        "https://github.com/user-attachments/assets/abc", "tok"
    )
    assert "Authorization" not in _image_request_headers(
        "https://github-production-user-asset-6210df.s3.amazonaws.com/1/x.png", "tok"
    )


def test_image_content_type_allowlist_excludes_svg():
    # SVG can execute script in our origin, so it must never be served.
    assert "image/svg+xml" not in _ALLOWED_IMAGE_CONTENT_TYPES
    assert "image/png" in _ALLOWED_IMAGE_CONTENT_TYPES


async def test_require_image_in_pr_rejects_unreferenced_url(monkeypatch):
    async def fake_github_get(github, path, **kwargs):
        return {"body": "see ![diagram](https://x.githubusercontent.com/a.png)"}

    monkeypatch.setattr(
        "openswe.github.app.get_github_app_installation_token", AsyncMock(return_value="tok")
    )
    monkeypatch.setattr("openswe.review.reviews._github_get", fake_github_get)

    # A URL not present in the PR body (cross-repo IDOR attempt) is rejected.
    with pytest.raises(HTTPException) as exc:
        await _require_image_in_pr(
            "acme", "repo", 7, "https://x.githubusercontent.com/other-repo.png"
        )
    assert exc.value.status_code == 403

    # A URL actually embedded in the PR body is allowed.
    await _require_image_in_pr("acme", "repo", 7, "https://x.githubusercontent.com/a.png")
