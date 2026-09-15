import base64
import hashlib
from typing import Any

import pytest

from agent.github import pr_media

MEDIA = b"\x89PNG\r\n\x1a\nfake-image-bytes"


def _record(media: bytes = MEDIA) -> dict[str, Any]:
    digest = hashlib.sha256(media).hexdigest()
    return {
        "fingerprint": "fp-1",
        "status": pr_media.MEDIA_REQUEST_APPROVED,
        "owner": "octo",
        "repo": "repo",
        "repo_id": 42,
        "pull_number": 7,
        "pull_title": "Title",
        "file_name": "shot.png",
        "content_type": "image/png",
        "size_bytes": len(media),
        "digest": digest,
        "media_base64": base64.b64encode(media).decode("ascii"),
        "requested_at": "2026-01-01T00:00:00+00:00",
        "expires_at_epoch": 4_102_444_800,
        "decided_by": "alice",
    }


def test_fingerprint_is_content_addressed() -> None:
    kwargs: dict[str, Any] = {
        "owner": "Octo",
        "repo": "Repo",
        "repo_id": 42,
        "pull_number": 7,
        "file_name": "a.png",
        "content_type": "image/png",
        "size_bytes": 3,
        "digest": hashlib.sha256(b"abc").hexdigest().upper(),
    }
    assert pr_media.media_request_fingerprint(**kwargs) == pr_media.media_request_fingerprint(
        **{**kwargs, "owner": "octo", "repo": "repo", "digest": kwargs["digest"].lower()}
    )
    assert pr_media.media_request_fingerprint(**kwargs) != pr_media.media_request_fingerprint(
        **{**kwargs, "digest": hashlib.sha256(b"abcd").hexdigest()}
    )


def test_decode_media_base64_rejects_malformed() -> None:
    with pytest.raises(ValueError):
        pr_media.decode_media_base64("not base64!!!")
    with pytest.raises(ValueError):
        pr_media.decode_media_base64("a b c d")


def test_assert_uploadable_token_allowlist() -> None:
    for token in ("gho_x", "ghp_x", "github_pat_x"):
        pr_media.assert_uploadable_token(token)
    for token in ("ghs_x", "ghu_x", "ghr_x", "anything"):
        with pytest.raises(pr_media.MediaUploadRejected):
            pr_media.assert_uploadable_token(token)


def test_media_request_response_hides_payload() -> None:
    response = pr_media.media_request_response(_record())
    assert "media_base64" not in response
    assert response["digest"] == hashlib.sha256(MEDIA).hexdigest()
    assert response["fileName"] == "shot.png"


async def test_upload_media_asset_uses_attach_endpoint(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200
        content = b'{"url": "https://github.com/octo/repo/assets/1/abc"}'
        text = content.decode()
        headers: dict[str, str] = {}

        def json(self) -> dict[str, Any]:
            return {"url": "https://github.com/octo/repo/assets/1/abc"}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> FakeResponse:
            captured["url"] = url
            captured["post_kwargs"] = kwargs
            return FakeResponse()

    monkeypatch.setattr(pr_media.httpx2, "AsyncClient", FakeClient)

    url = await pr_media.upload_media_asset(
        "gho_token",
        repo_id=42,
        file_name="shot.png",
        content_type="image/png",
        media=MEDIA,
    )

    assert url == "https://github.com/octo/repo/assets/1/abc"
    assert captured["url"] == pr_media.UPLOAD_ENDPOINT
    params = captured["post_kwargs"]["params"]
    assert params == {"name": "shot.png", "content_type": "image/png", "repository_id": "42"}
    assert captured["post_kwargs"]["content"] == MEDIA
    headers = captured["post_kwargs"]["headers"]
    assert headers["Authorization"] == "Bearer gho_token"
    assert headers["Content-Type"] == "application/octet-stream"


async def test_upload_media_asset_rejects_non_200(monkeypatch) -> None:
    class FakeResponse:
        status_code = 404
        content = b'{"message": "Not Found"}'
        text = '{"message": "Not Found"}'
        headers: dict[str, str] = {}

        def json(self) -> dict[str, Any]:
            return {"message": "Not Found"}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(pr_media.httpx2, "AsyncClient", FakeClient)

    with pytest.raises(pr_media.MediaUploadFailed) as excinfo:
        await pr_media.upload_media_asset(
            "gho_token", repo_id=42, file_name="a.png", content_type="image/png", media=MEDIA
        )
    assert excinfo.value.status_code == 404
    assert "Not Found" in str(excinfo.value)


async def test_execute_rejects_unapproved(monkeypatch) -> None:
    async def fake_get(thread_id: str) -> dict[str, Any]:
        record = _record()
        record["status"] = pr_media.MEDIA_REQUEST_PENDING
        return {"fp-1": record}

    monkeypatch.setattr(pr_media, "get_media_requests", fake_get)

    with pytest.raises(pr_media.MediaUploadFailed, match="not approved"):
        await pr_media.execute_approved_media_request(
            "t-1", "fp-1", approver_login="alice", oauth_token="gho_x"
        )


async def test_execute_rejects_wrong_approver(monkeypatch) -> None:
    async def fake_get(thread_id: str) -> dict[str, Any]:
        return {"fp-1": _record()}

    monkeypatch.setattr(pr_media, "get_media_requests", fake_get)

    with pytest.raises(pr_media.MediaUploadFailed, match="approver"):
        await pr_media.execute_approved_media_request(
            "t-1", "fp-1", approver_login="mallory", oauth_token="gho_x"
        )


async def test_execute_rejects_tampered_payload(monkeypatch) -> None:
    async def fake_get(thread_id: str) -> dict[str, Any]:
        record = _record()
        record["media_base64"] = base64.b64encode(b"evil").decode("ascii")
        return {"fp-1": record}

    monkeypatch.setattr(pr_media, "get_media_requests", fake_get)

    with pytest.raises(pr_media.MediaUploadFailed, match="digest"):
        await pr_media.execute_approved_media_request(
            "t-1", "fp-1", approver_login="alice", oauth_token="gho_x"
        )


async def test_execute_rejects_expired_and_marks_failed(monkeypatch) -> None:
    saved: list[dict[str, Any]] = []

    async def fake_get(thread_id: str) -> dict[str, Any]:
        record = _record()
        record["expires_at_epoch"] = 1
        return {"fp-1": record}

    async def fake_save(thread_id: str, requests: dict[str, Any]) -> None:
        saved.extend(requests.values())

    monkeypatch.setattr(pr_media, "get_media_requests", fake_get)
    monkeypatch.setattr(pr_media, "save_media_requests", fake_save)

    with pytest.raises(pr_media.MediaUploadFailed, match="expired"):
        await pr_media.execute_approved_media_request(
            "t-1", "fp-1", approver_login="alice", oauth_token="gho_x"
        )
    assert saved[0]["status"] == pr_media.MEDIA_REQUEST_FAILED


async def test_execute_uploads_and_comments(monkeypatch) -> None:
    calls: list[str] = []
    saved: list[dict[str, Any]] = []

    async def fake_get(thread_id: str) -> dict[str, Any]:
        return {"fp-1": _record()}

    async def fake_save(thread_id: str, requests: dict[str, Any]) -> None:
        saved.extend(dict(r) for r in requests.values())

    async def fake_upload(token: str, **kwargs: Any) -> str:
        calls.append(f"upload:{token}:{kwargs['repo_id']}:{kwargs['file_name']}")
        return "https://github.com/octo/repo/assets/1/abc"

    async def fake_comment(token: str, **kwargs: Any) -> None:
        calls.append(f"comment:{token}:{kwargs['owner']}/{kwargs['repo']}#{kwargs['pull_number']}")

    monkeypatch.setattr(pr_media, "get_media_requests", fake_get)
    monkeypatch.setattr(pr_media, "save_media_requests", fake_save)
    monkeypatch.setattr(pr_media, "upload_media_asset", fake_upload)
    monkeypatch.setattr(pr_media, "append_media_comment", fake_comment)

    record = await pr_media.execute_approved_media_request(
        "t-1", "fp-1", approver_login="alice", oauth_token="gho_alice"
    )

    assert calls == ["upload:gho_alice:42:shot.png", "comment:gho_alice:octo/repo#7"]
    assert record["status"] == pr_media.MEDIA_REQUEST_COMPLETED
    assert record["asset_url"] == "https://github.com/octo/repo/assets/1/abc"
    assert saved[-1]["status"] == pr_media.MEDIA_REQUEST_COMPLETED


async def test_execute_failure_is_terminal_without_retry(monkeypatch) -> None:
    saved: list[dict[str, Any]] = []

    async def fake_get(thread_id: str) -> dict[str, Any]:
        return {"fp-1": _record()}

    async def fake_save(thread_id: str, requests: dict[str, Any]) -> None:
        saved.extend(dict(r) for r in requests.values())

    async def fake_upload(token: str, **kwargs: Any) -> str:
        raise pr_media.MediaUploadFailed(500, "server error")

    monkeypatch.setattr(pr_media, "get_media_requests", fake_get)
    monkeypatch.setattr(pr_media, "save_media_requests", fake_save)
    monkeypatch.setattr(pr_media, "upload_media_asset", fake_upload)

    with pytest.raises(pr_media.MediaUploadFailed, match="server error"):
        await pr_media.execute_approved_media_request(
            "t-1", "fp-1", approver_login="alice", oauth_token="gho_alice"
        )
    assert saved[-1]["status"] == pr_media.MEDIA_REQUEST_FAILED
    assert saved[-1]["error"] == "server error"
