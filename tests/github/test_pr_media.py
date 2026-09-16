import base64
import hashlib
import os
from typing import Any

import pytest

from agent.github import pr_media
from tests.conftest import isolated_schema

MEDIA = b"\x89PNG\r\n\x1a\nfake-image-bytes"
THREAD = "t-1"
ALICE = "alice"


@pytest.fixture
async def media_db(monkeypatch: pytest.MonkeyPatch):
    uri = os.environ.get("TEST_ANALYTICS_POSTGRES_URI")
    if not uri:
        pytest.skip("TEST_ANALYTICS_POSTGRES_URI is required for PostgreSQL regressions")
    async with isolated_schema(uri, monkeypatch):
        yield


async def _create_pending(
    media: bytes = MEDIA, *, thread_id: str = THREAD, pull_number: int = 7
) -> dict[str, Any]:
    record, created = await pr_media.create_media_request(
        thread_id,
        owner="octo",
        repo="repo",
        repo_id=42,
        pull_number=pull_number,
        pull_title="Title",
        file_name="shot.png",
        content_type="image/png",
        media=media,
        requested_by=ALICE,
    )
    assert created
    return record


def test_fingerprint_is_content_addressed() -> None:
    kwargs: dict[str, Any] = {
        "thread_id": THREAD,
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
    assert pr_media.media_request_fingerprint(**kwargs) != pr_media.media_request_fingerprint(
        **{**kwargs, "thread_id": "other-thread"}
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


async def test_create_is_idempotent_and_public_shape_hides_payload(media_db) -> None:
    record = await _create_pending()
    again, created = await pr_media.create_media_request(
        THREAD,
        owner="OCTO",
        repo="REPO",
        repo_id=42,
        pull_number=7,
        pull_title="Changed title",
        file_name="shot.png",
        content_type="image/png",
        media=MEDIA,
        requested_by="bob",
    )
    assert not created
    assert again["fingerprint"] == record["fingerprint"]
    assert again["requested_by"] == ALICE

    response = pr_media.media_request_response(record)
    assert "media_base64" not in response
    assert response["digest"] == hashlib.sha256(MEDIA).hexdigest()
    assert response["fileName"] == "shot.png"

    listed = await pr_media.list_media_requests(THREAD)
    assert [r["fingerprint"] for r in listed] == [record["fingerprint"]]
    assert await pr_media.get_media_request("other-thread", record["fingerprint"]) is None


async def test_expired_request_is_renewed(media_db) -> None:
    from sqlalchemy import text

    from agent.database import postgres

    record = await _create_pending()
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE pr_media_request SET expires_at_epoch = 1 WHERE fingerprint = :fp"),
            {"fp": record["fingerprint"]},
        )

    renewed, created = await pr_media.create_media_request(
        THREAD,
        owner="octo",
        repo="repo",
        repo_id=42,
        pull_number=7,
        pull_title="New title",
        file_name="shot.png",
        content_type="image/png",
        media=MEDIA,
        requested_by="bob",
    )

    assert created
    assert renewed["status"] == pr_media.MEDIA_REQUEST_PENDING
    assert renewed["expires_at_epoch"] > 1
    assert renewed["requested_by"] == "bob"


async def test_identical_request_is_scoped_to_thread(media_db) -> None:
    first = await _create_pending()
    second = await _create_pending(thread_id="other-thread")

    assert first["fingerprint"] != second["fingerprint"]


async def test_concurrent_claim_has_exactly_one_winner(media_db) -> None:
    import asyncio

    record = await _create_pending()
    outcomes = await asyncio.gather(
        pr_media.claim_media_request(THREAD, record["fingerprint"], actor=ALICE),
        pr_media.claim_media_request(THREAD, record["fingerprint"], actor="alice"),
        pr_media.claim_media_request(THREAD, record["fingerprint"], actor="alice"),
    )
    assert outcomes.count("claimed") == 1
    assert outcomes.count("conflict") == 2

    stored = await pr_media.get_media_request(THREAD, record["fingerprint"])
    assert stored is not None
    assert stored["status"] == pr_media.MEDIA_REQUEST_APPROVED
    assert stored["decided_by"] == ALICE


async def test_claim_rejects_wrong_owner_thread(media_db) -> None:
    record = await _create_pending()
    outcome = await pr_media.claim_media_request("other-thread", record["fingerprint"], actor=ALICE)
    assert outcome == "missing"
    stored = await pr_media.get_media_request(THREAD, record["fingerprint"])
    assert stored is not None
    assert stored["status"] == pr_media.MEDIA_REQUEST_PENDING


async def test_claim_expired_request_marks_failed(media_db) -> None:
    from sqlalchemy import text

    from agent.database import postgres

    record = await _create_pending()
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE pr_media_request SET expires_at_epoch = 1 WHERE fingerprint = :fp"),
            {"fp": record["fingerprint"]},
        )

    outcome = await pr_media.claim_media_request(THREAD, record["fingerprint"], actor=ALICE)
    assert outcome == "expired"
    stored = await pr_media.get_media_request(THREAD, record["fingerprint"])
    assert stored is not None
    assert stored["status"] == pr_media.MEDIA_REQUEST_FAILED
    assert "expired" in (stored["error"] or "")


async def test_reject_then_approve_conflicts(media_db) -> None:
    record = await _create_pending()
    assert (
        await pr_media.reject_media_request(THREAD, record["fingerprint"], actor=ALICE)
        == "rejected"
    )
    assert (
        await pr_media.claim_media_request(THREAD, record["fingerprint"], actor=ALICE) == "conflict"
    )
    stored = await pr_media.get_media_request(THREAD, record["fingerprint"])
    assert stored is not None
    assert stored["status"] == pr_media.MEDIA_REQUEST_REJECTED


async def test_release_only_returns_own_claim(media_db) -> None:
    record = await _create_pending()
    assert (
        await pr_media.claim_media_request(THREAD, record["fingerprint"], actor=ALICE) == "claimed"
    )
    await pr_media.release_media_request_claim(THREAD, record["fingerprint"], actor="mallory")
    stored = await pr_media.get_media_request(THREAD, record["fingerprint"])
    assert stored is not None
    assert stored["status"] == pr_media.MEDIA_REQUEST_APPROVED

    await pr_media.release_media_request_claim(THREAD, record["fingerprint"], actor=ALICE)
    stored = await pr_media.get_media_request(THREAD, record["fingerprint"])
    assert stored is not None
    assert stored["status"] == pr_media.MEDIA_REQUEST_PENDING
    assert stored["decided_by"] is None


async def test_execute_uploads_and_comments_then_replay_fails(
    media_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = await _create_pending()
    assert (
        await pr_media.claim_media_request(THREAD, record["fingerprint"], actor=ALICE) == "claimed"
    )
    calls: list[str] = []

    async def fake_upload(token: str, **kwargs: Any) -> str:
        calls.append(f"upload:{token}:{kwargs['repo_id']}:{kwargs['file_name']}")
        return "https://github.com/octo/repo/assets/1/abc"

    async def fake_comment(token: str, **kwargs: Any) -> None:
        calls.append(f"comment:{token}:{kwargs['owner']}/{kwargs['repo']}#{kwargs['pull_number']}")

    monkeypatch.setattr(pr_media, "upload_media_asset", fake_upload)
    monkeypatch.setattr(pr_media, "append_media_comment", fake_comment)

    done = await pr_media.execute_approved_media_request(
        THREAD, record["fingerprint"], approver_login=ALICE, oauth_token="gho_alice"
    )
    assert done["status"] == pr_media.MEDIA_REQUEST_COMPLETED
    assert done["asset_url"] == "https://github.com/octo/repo/assets/1/abc"
    assert calls == ["upload:gho_alice:42:shot.png", "comment:gho_alice:octo/repo#7"]

    with pytest.raises(pr_media.MediaUploadFailed, match="already executed"):
        await pr_media.execute_approved_media_request(
            THREAD, record["fingerprint"], approver_login=ALICE, oauth_token="gho_alice"
        )
    assert len(calls) == 2


async def test_execute_rejects_mutated_media_and_wrong_approver(
    media_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import text

    from agent.database import postgres

    record = await _create_pending()
    assert (
        await pr_media.claim_media_request(THREAD, record["fingerprint"], actor=ALICE) == "claimed"
    )

    with pytest.raises(pr_media.MediaUploadFailed, match="approver"):
        await pr_media.execute_approved_media_request(
            THREAD, record["fingerprint"], approver_login="mallory", oauth_token="gho_m"
        )

    tampered = base64.b64encode(b"evil").decode("ascii")
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE pr_media_request SET media_base64 = :m WHERE fingerprint = :fp"),
            {"m": tampered, "fp": record["fingerprint"]},
        )
    with pytest.raises(pr_media.MediaUploadFailed, match="digest"):
        await pr_media.execute_approved_media_request(
            THREAD, record["fingerprint"], approver_login=ALICE, oauth_token="gho_alice"
        )


async def test_execute_rejects_expired_and_marks_failed(media_db) -> None:
    from sqlalchemy import text

    from agent.database import postgres

    record = await _create_pending()
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE pr_media_request SET expires_at_epoch = 1 WHERE fingerprint = :fp"),
            {"fp": record["fingerprint"]},
        )
    assert (
        await pr_media.claim_media_request(THREAD, record["fingerprint"], actor=ALICE) == "expired"
    )
    with pytest.raises(pr_media.MediaUploadFailed, match="not approved"):
        await pr_media.execute_approved_media_request(
            THREAD, record["fingerprint"], approver_login=ALICE, oauth_token="gho_alice"
        )


async def test_execute_failure_is_terminal_without_retry(
    media_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = await _create_pending()
    assert (
        await pr_media.claim_media_request(THREAD, record["fingerprint"], actor=ALICE) == "claimed"
    )

    async def fake_upload(token: str, **kwargs: Any) -> str:
        raise pr_media.MediaUploadFailed(500, "server error")

    monkeypatch.setattr(pr_media, "upload_media_asset", fake_upload)

    with pytest.raises(pr_media.MediaUploadFailed, match="server error"):
        await pr_media.execute_approved_media_request(
            THREAD, record["fingerprint"], approver_login=ALICE, oauth_token="gho_alice"
        )
    stored = await pr_media.get_media_request(THREAD, record["fingerprint"])
    assert stored is not None
    assert stored["status"] == pr_media.MEDIA_REQUEST_FAILED
    assert stored["error"] == "server error"

    assert (
        await pr_media.claim_media_request(THREAD, record["fingerprint"], actor=ALICE) == "conflict"
    )


async def test_upload_media_asset_uses_attach_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
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


async def test_upload_media_asset_rejects_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
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
