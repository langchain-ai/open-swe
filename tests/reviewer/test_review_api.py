from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import HTTPException
from starlette.requests import ClientDisconnect

from openswe.review.reviews import (
    _ALLOWED_MEDIA_CONTENT_TYPES,
    MediaDownload,
    MediaTooLargeError,
    _image_request_headers,
    _is_allowed_image_url,
    _serialize_pr_details,
    proxy_pr_media,
)


@pytest.mark.parametrize("merged, expected", [(False, "closed"), (True, "merged")])
def test_review_details_preserve_closed_draft_lifecycle(merged: bool, expected: str):
    details = _serialize_pr_details({"state": "closed", "draft": True, "merged": merged})

    assert details["state"] == expected


def test_description_references_only_identifies_same_repo_pull_requests():
    from openswe.review.reviews import DescriptionReferences

    references = DescriptionReferences(
        '<a href="https://github.com/acme/repo/issues/4879">#4879</a>'
        '<a href="https://github.com/acme/repo/pull/12">#12</a>'
        '<a href="https://github.com/acme/other/pull/13">acme/other#13</a>'
        "<code>#14</code>"
    )
    assert references.pull_request_numbers("acme", "repo") == [12]


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
        "https://raw.githubusercontent.com/acme/repo/main/x.png", "tok"
    )
    assert "Authorization" not in _image_request_headers(
        "https://private-user-images.githubusercontent.com/1/2-abc.png?jwt=signed", "tok"
    )
    assert "Authorization" not in _image_request_headers(
        "https://github.com/user-attachments/assets/abc", "tok"
    )
    assert "Authorization" not in _image_request_headers(
        "https://github-production-user-asset-6210df.s3.amazonaws.com/1/x.png", "tok"
    )


def test_image_content_type_allowlist_excludes_svg():
    # SVG can execute script in our origin, so it must never be served.
    assert "image/svg+xml" not in _ALLOWED_MEDIA_CONTENT_TYPES
    assert "image/png" in _ALLOWED_MEDIA_CONTENT_TYPES


async def test_label_write_requires_repository_access(monkeypatch):
    from openswe.review.labels import LabelChange
    from openswe.review.routes import api_change_pull_request_label

    async def deny_access(user_id: str, repo: str) -> None:
        raise HTTPException(403, "Repository access denied")

    monkeypatch.setattr("openswe.review.routes.require_repo_access_for_user", deny_access)
    with pytest.raises(HTTPException) as exc:
        await api_change_pull_request_label(
            "acme", "private", 7, LabelChange(name="bug", selected=True), {"sub": "user"}
        )
    assert exc.value.status_code == 403


_UPLOAD = "https://github.com/user-attachments/assets/8eb1005d-2aaa-400f-9159-90b265f8379a"
_SIGNED = (
    "https://private-user-images.githubusercontent.com/1/"
    "701930936-8eb1005d-2aaa-400f-9159-90b265f8379a.mp4?jwt=signed&amp;x=1"
)


class _UpstreamBody(httpx2.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self):
        yield b"\x00\x00\x00\x18"

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def signed_upload(monkeypatch, github_app) -> tuple[list[httpx2.Request], list[_UpstreamBody]]:
    """A private PR whose body shows ``_UPLOAD``, and the requests and bodies its media host sees."""
    pull = httpx2.Response(
        200,
        json={
            "body": f"Demo\n\n{_UPLOAD}\n",
            "body_html": f'<p><video src="{_SIGNED}"></video></p>',
        },
        request=httpx2.Request("GET", "https://api.github.com"),
    )
    monkeypatch.setattr("openswe.github.http.github_request", AsyncMock(return_value=pull))
    monkeypatch.setattr(
        "openswe.review.reviews.get_github_app_installation_token",
        AsyncMock(return_value="app-token"),
    )
    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("185.199.108.133", 0))]
    )
    seen: list[httpx2.Request] = []
    bodies: list[_UpstreamBody] = []

    def upstream(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        bodies.append(_UpstreamBody())
        return httpx2.Response(
            206,
            headers={"Content-Type": "video/mp4", "Content-Range": "bytes 0-3/2048"},
            stream=bodies[-1],
        )

    monkeypatch.setattr(
        "openswe.review.reviews.pooled_transport", lambda: httpx2.MockTransport(upstream)
    )
    return seen, bodies


async def test_proxy_streams_a_private_upload_from_its_signed_url(signed_upload):
    seen, _ = signed_upload

    # A URL not present in the PR body (cross-repo IDOR attempt) is rejected.
    with pytest.raises(HTTPException) as exc:
        await proxy_pr_media(
            "acme", "repo", 7, "https://github.com/user-attachments/assets/0000", None
        )
    assert exc.value.status_code == 403

    response = await proxy_pr_media("acme", "repo", 7, _UPLOAD, "bytes=0-3")
    chunks = [chunk async for chunk in response.body_iterator]

    # A private repo's upload 404s anonymously; only GitHub's signed rendering loads.
    assert str(seen[0].url) == _SIGNED.replace("&amp;", "&")
    assert "authorization" not in seen[0].headers
    # A video element seeks with byte ranges; Safari will not play without them.
    assert seen[0].headers["range"] == "bytes=0-3"
    assert (response.status_code, response.headers["content-range"]) == (206, "bytes 0-3/2048")
    assert chunks == [b"\x00\x00\x00\x18"]


@pytest.mark.parametrize("failing_message", ["http.response.start", "http.response.body"])
async def test_proxy_closes_upstream_when_the_browser_disconnects(
    signed_upload, failing_message: str
):
    _, bodies = signed_upload
    response = await proxy_pr_media("acme", "repo", 7, _UPLOAD, None)

    async def send(message) -> None:
        if message["type"] == failing_message:
            raise OSError("browser went away")

    # Starlette skips the background task here, and a body never started never finalizes.
    with pytest.raises(ClientDisconnect):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, AsyncMock(), send)
    assert bodies[0].closed


async def test_proxy_aborts_media_that_passes_the_cap_after_streaming_starts(monkeypatch):
    monkeypatch.setattr("openswe.review.reviews._MAX_MEDIA_BYTES", 3)
    body = _UpstreamBody()
    unsized = httpx2.Response(200, headers={"Content-Type": "video/mp4"}, stream=body)
    response = MediaDownload(httpx2.AsyncClient(), unsized).streaming_response()
    sent: list[dict] = []

    async def send(message) -> None:
        sent.append(message)

    # The 200 is already out, so only a broken connection tells the browser the file is incomplete.
    with pytest.raises(MediaTooLargeError):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, AsyncMock(), send)
    assert not any(message.get("more_body") is False for message in sent)
    assert body.closed


async def test_review_chat_rejects_thread_not_postable_for_this_pr(monkeypatch) -> None:
    from openswe.review import chat as review_chat_api

    async def no_accessible_threads(*args):
        return []

    monkeypatch.setattr(review_chat_api.pr_fixes, "find_pr_threads", no_accessible_threads)
    with pytest.raises(HTTPException) as error:
        await review_chat_api.proxy_review_chat_commands(
            "acme", "repo", 7, "octocat", "foreign-thread", b"{}"
        )
    assert error.value.status_code == 404
