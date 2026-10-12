import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.types import Message

from openswe.dashboard.oauth import decode_upload_ticket, issue_upload_ticket
from openswe.threads import session_upload
from openswe.threads.summary import assert_thread_postable

_TRANSCRIPT = (
    json.dumps({"type": "user", "uuid": "u1", "parentUuid": None, "message": {"content": "hi"}})
    + "\n"
).encode()


def _request() -> Request:
    messages: list[Message] = [{"type": "http.request", "body": _TRANSCRIPT, "more_body": False}]

    async def receive() -> Message:
        return messages.pop(0)

    return Request({"type": "http", "method": "POST", "headers": []}, receive)


@pytest.fixture
def reserved(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret")
    metadata: dict[str, object] = {
        "source": "dashboard",
        "owner_login": "alice",
        "repo_owner": "acme",
        "repo_name": "app",
        "branch_name": "feature",
        "session_upload_pending": True,
    }

    async def update(*, thread_id: str, metadata: dict[str, object]) -> None:
        reserved_metadata.update(metadata)

    reserved_metadata = metadata
    threads = SimpleNamespace(
        get=AsyncMock(side_effect=lambda _id: {"thread_id": _id, "metadata": metadata}),
        update_state=AsyncMock(),
        update=update,
    )
    monkeypatch.setattr(
        session_upload, "langgraph_client", lambda: SimpleNamespace(threads=threads)
    )
    monkeypatch.setattr(session_upload, "mirror_thread_metadata", AsyncMock())
    return metadata


async def _upload(code: str) -> dict[str, object] | None:
    ticket = decode_upload_ticket(code)
    return await session_upload.upload_session(session_upload.UploadStream(_request()), ticket)


def _code(login: str) -> str:
    return issue_upload_ticket(login=login, email=None, user_id=None, thread_id="t1")


async def test_an_upload_code_fills_its_own_thread_once(reserved: dict[str, object]) -> None:
    with pytest.raises(HTTPException) as early:
        assert_thread_postable(reserved, "alice")
    assert early.value.status_code == 409

    uploaded = await _upload(_code("alice"))
    assert uploaded is not None
    assert uploaded["id"] == "t1"
    assert reserved["session_upload_pending"] is False
    assert_thread_postable(reserved, "alice")
    seeded = session_upload.langgraph_client().threads.update_state
    seeds = seeded.await_count
    assert await _upload(_code("alice")) is None
    assert seeded.await_count == seeds


@pytest.mark.parametrize(("login", "suffix", "status"), [("bob", "", 409), ("alice", "x", 401)])
async def test_another_persons_or_a_forged_code_is_refused(
    reserved: dict[str, object], login: str, suffix: str, status: int
) -> None:
    with pytest.raises(HTTPException) as refused:
        await _upload(_code(login) + suffix)
    assert refused.value.status_code == status
    assert reserved["session_upload_pending"] is True
