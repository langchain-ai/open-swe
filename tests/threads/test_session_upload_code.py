import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.types import Message

from openswe.dashboard.oauth import issue_upload_ticket
from openswe.threads import session_upload

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


async def test_an_upload_code_fills_its_own_thread_once(reserved: dict[str, object]) -> None:
    code = issue_upload_ticket(login="alice", email=None, thread_id="t1")

    summary = await session_upload.upload_session(session_upload.UploadStream(_request()), code)
    assert summary["id"] == "t1"
    assert reserved["session_upload_pending"] is False
    with pytest.raises(HTTPException) as reused:
        await session_upload.upload_session(session_upload.UploadStream(_request()), code)
    assert reused.value.status_code == 409


@pytest.mark.parametrize(
    ("login", "forged", "status"),
    [("bob", False, 409), ("alice", True, 401)],
)
async def test_another_persons_or_a_forged_code_is_refused(
    reserved: dict[str, object], login: str, forged: bool, status: int
) -> None:
    code = issue_upload_ticket(login=login, email=None, thread_id="t1")
    with pytest.raises(HTTPException) as refused:
        await session_upload.upload_session(
            session_upload.UploadStream(_request()), code + "x" if forged else code
        )
    assert refused.value.status_code == status
    assert reserved["session_upload_pending"] is True
