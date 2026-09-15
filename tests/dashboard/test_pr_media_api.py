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


def _pending_record() -> dict[str, Any]:
    digest = hashlib.sha256(MEDIA).hexdigest()
    return {
        "fingerprint": "fp-1",
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
        "requested_at": "2026-01-01T00:00:00+00:00",
        "expires_at_epoch": 4_102_444_800,
    }


def _patch_common(monkeypatch: pytest.MonkeyPatch, records: dict[str, Any]) -> None:
    async def fake_metadata(thread_id: str) -> dict[str, Any]:
        return dict(METADATA)

    async def fake_get(thread_id: str) -> dict[str, Any]:
        return {fp: dict(r) for fp, r in records.items()}

    async def fake_save(thread_id: str, requests: dict[str, Any]) -> None:
        records.clear()
        records.update(requests)

    monkeypatch.setattr(pr_media_api, "fetch_thread_metadata", fake_metadata)
    monkeypatch.setattr(pr_media, "get_media_requests", fake_get)
    monkeypatch.setattr(pr_media, "save_media_requests", fake_save)


async def test_list_requires_readable_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, {})
    with pytest.raises(HTTPException) as excinfo:
        await pr_media_api.list_media_requests(
            "t-1", session={"sub": "mallory", "email": "m@example.com"}
        )
    assert excinfo.value.status_code == 404


async def test_list_returns_requests_for_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, {"fp-1": _pending_record()})
    response = await pr_media_api.list_media_requests("t-1", session=dict(SESSION))
    assert response["threadId"] == "t-1"
    assert len(response["requests"]) == 1
    assert response["requests"][0]["fingerprint"] == "fp-1"
    assert "media_base64" not in response["requests"][0]


async def test_approve_executes_with_owner_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    records = {"fp-1": _pending_record()}
    _patch_common(monkeypatch, records)
    executed: dict[str, Any] = {}

    async def fake_token(login: str) -> str:
        assert login == "alice"
        return "gho_alice"

    async def fake_execute(
        thread_id: str, fingerprint: str, *, approver_login: str, oauth_token: str
    ) -> dict[str, Any]:
        executed.update({"thread_id": thread_id, "approver": approver_login, "token": oauth_token})
        record = records[fingerprint]
        record["status"] = pr_media.MEDIA_REQUEST_COMPLETED
        record["asset_url"] = "https://github.com/octo/repo/assets/1/abc"
        return record

    monkeypatch.setattr(pr_media_support, "get_oauth_token_for_upload", fake_token)
    monkeypatch.setattr(pr_media, "execute_approved_media_request", fake_execute)

    response = await pr_media_api.approve_media_request("t-1", "fp-1", session=dict(SESSION))

    assert executed == {"thread_id": "t-1", "approver": "alice", "token": "gho_alice"}
    assert records["fp-1"]["decided_by"] == "alice"
    assert response["request"]["status"] == "completed"


async def test_approve_rejects_non_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, {"fp-1": _pending_record()})
    with pytest.raises(HTTPException) as excinfo:
        await pr_media_api.approve_media_request(
            "t-1", "fp-1", session={"sub": "mallory", "email": "m@example.com"}
        )
    assert excinfo.value.status_code == 404


async def test_approve_rejects_system_owned_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, {"fp-1": _pending_record()})

    async def fake_metadata(thread_id: str) -> dict[str, Any]:
        return {"source": "dashboard", "visibility": "private", "owner_login": "other"}

    monkeypatch.setattr(pr_media_api, "fetch_thread_metadata", fake_metadata)

    with pytest.raises(HTTPException) as excinfo:
        await pr_media_api.approve_media_request("t-1", "fp-1", session=dict(SESSION))
    assert excinfo.value.status_code == 404


async def test_approve_rejects_terminal_record(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _pending_record()
    record["status"] = pr_media.MEDIA_REQUEST_COMPLETED
    _patch_common(monkeypatch, {"fp-1": record})
    with pytest.raises(HTTPException) as excinfo:
        await pr_media_api.approve_media_request("t-1", "fp-1", session=dict(SESSION))
    assert excinfo.value.status_code == 409


async def test_approve_rolls_back_claim_when_reauth_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {"fp-1": _pending_record()}
    _patch_common(monkeypatch, records)

    async def fake_token(login: str) -> None:
        return None

    monkeypatch.setattr(pr_media_support, "get_oauth_token_for_upload", fake_token)

    with pytest.raises(HTTPException) as excinfo:
        await pr_media_api.approve_media_request("t-1", "fp-1", session=dict(SESSION))
    assert excinfo.value.status_code == 401
    assert records["fp-1"]["status"] == pr_media.MEDIA_REQUEST_PENDING
    assert "decided_by" not in records["fp-1"]


async def test_reject_marks_record(monkeypatch: pytest.MonkeyPatch) -> None:
    records = {"fp-1": _pending_record()}
    _patch_common(monkeypatch, records)
    response = await pr_media_api.reject_media_request("t-1", "fp-1", session=dict(SESSION))
    assert response["request"]["status"] == "rejected"
    assert records["fp-1"]["decided_by"] == "alice"


async def test_create_rejects_unsupported_type(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, {})
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


async def test_create_records_immutable_request(monkeypatch: pytest.MonkeyPatch) -> None:
    records: dict[str, Any] = {}
    _patch_common(monkeypatch, records)

    async def fake_repo(login: str, *, owner: str, repo: str) -> dict[str, Any]:
        return {"repo_id": 42, "token": "gho_alice"}

    async def fake_title(token: str, *, owner: str, repo: str, pull_number: int) -> str:
        return "Some PR"

    monkeypatch.setattr(pr_media_support, "resolve_repository", fake_repo)
    monkeypatch.setattr(pr_media_support, "fetch_pull_title", fake_title)

    body = pr_media_api.MediaRequestCreate(
        owner="octo",
        repo="repo",
        pull_number=7,
        file_name="shot.png",
        media_base64=base64.b64encode(MEDIA).decode(),
    )
    response = await pr_media_api.create_media_request("t-1", body, session=dict(SESSION))

    assert response["created"] is True
    record = next(iter(records.values()))
    assert record["digest"] == hashlib.sha256(MEDIA).hexdigest()
    assert record["repo_id"] == 42
    assert record["status"] == pr_media.MEDIA_REQUEST_PENDING
    assert record["expires_at_epoch"] > 0
