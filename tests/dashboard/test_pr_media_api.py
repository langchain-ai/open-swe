import base64
import hashlib
from typing import Any

import pytest
from fastapi import HTTPException

from agent.github import pr_media, pr_media_api, pr_media_support

MEDIA = b"\x89PNG\r\n\x1a\nfake-image-bytes"
METADATA = {
    "source": "dashboard",
    "visibility": "private",
    "owner_login": "alice",
}
SESSION = {"sub": "alice", "email": "alice@example.com"}
FP = hashlib.sha256(b"fp").hexdigest()


def _pending_record() -> dict[str, Any]:
    digest = hashlib.sha256(MEDIA).hexdigest()
    return {
        "fingerprint": FP,
        "thread_id": "t-1",
        "status": pr_media.MEDIA_REQUEST_PENDING,
        "owner": "octo",
        "repo": "repo",
        "repo_id": 42,
        "pull_number": 7,
        "pull_title": "Title",
        "file_name": "shot.png",
        "content_type": "image/png",
        "size_bytes": len(MEDIA),
        "digest": digest,
        "media_base64": base64.b64encode(MEDIA).decode("ascii"),
        "requested_by": "alice",
        "requested_at": None,
        "expires_at_epoch": 4_102_444_800,
        "decided_by": None,
        "decided_at": None,
        "asset_url": None,
        "error": None,
    }


def _patch_common(monkeypatch: pytest.MonkeyPatch, record: dict[str, Any] | None) -> None:
    async def fake_metadata(thread_id: str) -> dict[str, Any]:
        return dict(METADATA)

    async def fake_get(thread_id: str, fingerprint: str) -> dict[str, Any] | None:
        return dict(record) if record is not None else None

    async def fake_list(thread_id: str) -> list[dict[str, Any]]:
        return [dict(record)] if record is not None else []

    monkeypatch.setattr(pr_media_api, "fetch_thread_metadata", fake_metadata)
    monkeypatch.setattr(pr_media, "get_media_request", fake_get)
    monkeypatch.setattr(pr_media, "list_media_requests", fake_list)


async def test_list_requires_readable_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, None)
    with pytest.raises(HTTPException) as excinfo:
        await pr_media_api.list_media_requests(
            "t-1", session={"sub": "mallory", "email": "m@example.com"}
        )
    assert excinfo.value.status_code == 404


async def test_list_returns_requests_for_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, _pending_record())
    response = await pr_media_api.list_media_requests("t-1", session=dict(SESSION))
    assert response["threadId"] == "t-1"
    assert len(response["requests"]) == 1
    assert response["requests"][0]["fingerprint"] == FP
    assert "media_base64" not in response["requests"][0]


async def test_approve_executes_with_owner_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _pending_record()
    record["status"] = pr_media.MEDIA_REQUEST_COMPLETED
    record["decided_by"] = "alice"
    record["asset_url"] = "https://github.com/octo/repo/assets/1/abc"
    _patch_common(monkeypatch, record)
    executed: dict[str, Any] = {}

    async def fake_claim(thread_id: str, fingerprint: str, *, actor: str) -> str:
        executed["claim_actor"] = actor
        return "claimed"

    async def fake_token(login: str) -> str:
        assert login == "alice"
        return "gho_alice"

    async def fake_execute(
        thread_id: str, fingerprint: str, *, approver_login: str, oauth_token: str
    ) -> dict[str, Any]:
        executed.update({"thread_id": thread_id, "approver": approver_login, "token": oauth_token})
        return record

    monkeypatch.setattr(pr_media, "claim_media_request", fake_claim)
    monkeypatch.setattr(pr_media_support, "get_oauth_token_for_upload", fake_token)
    monkeypatch.setattr(pr_media, "execute_approved_media_request", fake_execute)

    response = await pr_media_api.approve_media_request("t-1", FP, session=dict(SESSION))

    assert executed == {
        "claim_actor": "alice",
        "thread_id": "t-1",
        "approver": "alice",
        "token": "gho_alice",
    }
    assert response["request"]["status"] == "completed"


async def test_approve_rejects_non_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, _pending_record())

    async def fail_claim(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("non-owner must never reach the claim")

    monkeypatch.setattr(pr_media, "claim_media_request", fail_claim)

    with pytest.raises(HTTPException) as excinfo:
        await pr_media_api.approve_media_request(
            "t-1", FP, session={"sub": "mallory", "email": "m@example.com"}
        )
    assert excinfo.value.status_code == 404


async def test_approve_rejects_other_owner_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, _pending_record())

    async def fake_metadata(thread_id: str) -> dict[str, Any]:
        return {"source": "dashboard", "visibility": "private", "owner_login": "other"}

    monkeypatch.setattr(pr_media_api, "fetch_thread_metadata", fake_metadata)

    with pytest.raises(HTTPException) as excinfo:
        await pr_media_api.approve_media_request("t-1", FP, session=dict(SESSION))
    assert excinfo.value.status_code == 404


async def test_approve_conflict_when_already_decided(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, _pending_record())

    async def fake_claim(thread_id: str, fingerprint: str, *, actor: str) -> str:
        return "conflict"

    monkeypatch.setattr(pr_media, "claim_media_request", fake_claim)

    with pytest.raises(HTTPException) as excinfo:
        await pr_media_api.approve_media_request("t-1", FP, session=dict(SESSION))
    assert excinfo.value.status_code == 409


async def test_approve_expired_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, _pending_record())

    async def fake_claim(thread_id: str, fingerprint: str, *, actor: str) -> str:
        return "expired"

    monkeypatch.setattr(pr_media, "claim_media_request", fake_claim)

    with pytest.raises(HTTPException) as excinfo:
        await pr_media_api.approve_media_request("t-1", FP, session=dict(SESSION))
    assert excinfo.value.status_code == 410


async def test_approve_releases_claim_when_reauth_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch, _pending_record())
    released: list[str] = []

    async def fake_claim(thread_id: str, fingerprint: str, *, actor: str) -> str:
        return "claimed"

    async def fake_token(login: str) -> None:
        return None

    async def fake_release(thread_id: str, fingerprint: str, *, actor: str) -> None:
        released.append(actor)

    monkeypatch.setattr(pr_media, "claim_media_request", fake_claim)
    monkeypatch.setattr(pr_media_support, "get_oauth_token_for_upload", fake_token)
    monkeypatch.setattr(pr_media, "release_media_request_claim", fake_release)

    with pytest.raises(HTTPException) as excinfo:
        await pr_media_api.approve_media_request("t-1", FP, session=dict(SESSION))
    assert excinfo.value.status_code == 401
    assert released == ["alice"]


async def test_reject_marks_record(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _pending_record()
    record["status"] = pr_media.MEDIA_REQUEST_REJECTED
    record["decided_by"] = "alice"
    _patch_common(monkeypatch, record)

    async def fake_reject(thread_id: str, fingerprint: str, *, actor: str) -> str:
        assert actor == "alice"
        return "rejected"

    monkeypatch.setattr(pr_media, "reject_media_request", fake_reject)

    response = await pr_media_api.reject_media_request("t-1", FP, session=dict(SESSION))
    assert response["request"]["status"] == "rejected"
    assert response["request"]["decidedBy"] == "alice"


async def test_create_rejects_unsupported_type(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, None)
    body = pr_media_api.MediaRequestCreate(
        owner="octo",
        repo="repo",
        pull_number=7,
        file_name="evil.exe",
        media_base64=base64.b64encode(MEDIA).decode(),
    )
    with pytest.raises(HTTPException) as excinfo:
        await pr_media_api.create_media_request("t-1", body, session=dict(SESSION))
    assert excinfo.value.status_code == 422


async def test_create_uses_bot_credentials_never_personal_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch, None)
    created: dict[str, Any] = {}

    async def fail_oauth(login: str) -> str:
        raise AssertionError("preparation must not touch personal OAuth tokens")

    async def fake_repo(*, owner: str, repo: str) -> int:
        return 42

    async def fake_title(*, owner: str, repo: str, pull_number: int) -> str:
        return "Some PR"

    async def fake_create(thread_id: str, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        created.update(kwargs)
        record = _pending_record()
        record.update(
            fingerprint=FP,
            thread_id=thread_id,
            repo_id=kwargs["repo_id"],
            pull_title=kwargs["pull_title"],
            requested_by=kwargs["requested_by"],
        )
        return record, True

    monkeypatch.setattr(pr_media_support, "get_oauth_token_for_upload", fail_oauth)
    monkeypatch.setattr(pr_media_support, "get_valid_access_token", fail_oauth)
    monkeypatch.setattr(pr_media_support, "resolve_repository", fake_repo)
    monkeypatch.setattr(pr_media_support, "fetch_pull_title", fake_title)
    monkeypatch.setattr(pr_media, "create_media_request", fake_create)

    body = pr_media_api.MediaRequestCreate(
        owner="octo",
        repo="repo",
        pull_number=7,
        file_name="shot.png",
        media_base64=base64.b64encode(MEDIA).decode(),
    )
    response = await pr_media_api.create_media_request("t-1", body, session=dict(SESSION))

    assert response["created"] is True
    assert created["repo_id"] == 42
    assert created["requested_by"] == "alice"
    assert response["request"]["repo"] == "repo"
    assert "media_base64" not in response["request"]


async def test_create_rejects_cross_thread_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, None)

    async def fake_repo(*, owner: str, repo: str) -> int:
        return 42

    async def fake_title(*, owner: str, repo: str, pull_number: int) -> str:
        return "Some PR"

    async def fake_create(thread_id: str, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        record = _pending_record()
        record["thread_id"] = "t-other"
        return record, False

    monkeypatch.setattr(pr_media_support, "resolve_repository", fake_repo)
    monkeypatch.setattr(pr_media_support, "fetch_pull_title", fake_title)
    monkeypatch.setattr(pr_media, "create_media_request", fake_create)

    body = pr_media_api.MediaRequestCreate(
        owner="octo",
        repo="repo",
        pull_number=7,
        file_name="shot.png",
        media_base64=base64.b64encode(MEDIA).decode(),
    )
    with pytest.raises(HTTPException) as excinfo:
        await pr_media_api.create_media_request("t-1", body, session=dict(SESSION))
    assert excinfo.value.status_code == 409


async def test_preview_serves_image_bytes_only_to_readers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch, _pending_record())
    response = await pr_media_api.preview_media_request("t-1", FP, session=dict(SESSION))
    assert response.body == MEDIA
    assert response.media_type == "image/png"

    with pytest.raises(HTTPException) as excinfo:
        await pr_media_api.preview_media_request(
            "t-1", FP, session={"sub": "mallory", "email": "m@example.com"}
        )
    assert excinfo.value.status_code == 404


async def test_preview_rejects_video(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _pending_record()
    record["content_type"] = "video/mp4"
    record["file_name"] = "clip.mp4"
    _patch_common(monkeypatch, record)
    with pytest.raises(HTTPException) as excinfo:
        await pr_media_api.preview_media_request("t-1", FP, session=dict(SESSION))
    assert excinfo.value.status_code == 415
