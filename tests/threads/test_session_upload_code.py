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
from tests.conftest import LangSmithUsers

_TRANSCRIPT = (
    json.dumps({"type": "user", "uuid": "u1", "parentUuid": None, "message": {"content": "hi"}})
    + "\n"
).encode()


def _transcript(session_id: str) -> bytes:
    record = {"type": "user", "uuid": "u1", "parentUuid": None, "sessionId": session_id}
    return (json.dumps({**record, "message": {"content": "hi"}}) + "\n").encode()


def _request(body: bytes = _TRANSCRIPT) -> Request:
    messages: list[Message] = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> Message:
        return messages.pop(0)

    return Request({"type": "http", "method": "POST", "headers": []}, receive)


@pytest.fixture
def reserved(monkeypatch: pytest.MonkeyPatch, langsmith_users: LangSmithUsers) -> dict[str, object]:
    langsmith_users.add("alice")
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


async def _upload(code: str, body: bytes = _TRANSCRIPT) -> dict[str, object] | None:
    ticket = decode_upload_ticket(code)
    return await session_upload.upload_session(session_upload.UploadStream(_request(body)), ticket)


def _code(login: str) -> str:
    return issue_upload_ticket(login=login, email=None, user_id=None, thread_id="t1")


async def test_an_upload_code_fills_its_own_thread_once(reserved: dict[str, object]) -> None:
    with pytest.raises(HTTPException) as early:
        await assert_thread_postable(reserved, "alice")
    assert early.value.status_code == 409

    uploaded = await _upload(_code("alice"))
    assert uploaded is not None
    assert uploaded["id"] == "t1"
    assert reserved["session_upload_pending"] is False
    await assert_thread_postable(reserved, "alice")
    seeded = session_upload.langgraph_client().threads.update_state
    seeds = seeded.await_count
    assert await _upload(_code("alice")) is None
    assert seeded.await_count == seeds


async def test_a_retry_after_a_partial_seed_replaces_what_it_seeded(
    reserved: dict[str, object],
) -> None:
    threads = session_upload.langgraph_client().threads
    store_metadata = threads.update
    threads.update = AsyncMock(side_effect=RuntimeError("metadata store down"))
    with pytest.raises(RuntimeError):
        await _upload(_code("alice"))
    threads.update = store_metadata
    assert await _upload(_code("alice")) is not None

    first, _, retry, _ = threads.update_state.await_args_list
    ids = [message.id for message in first.kwargs["values"]["messages"]]
    assert None not in ids
    assert [message.id for message in retry.kwargs["values"]["messages"]] == ids


@pytest.mark.parametrize(("login", "suffix", "status"), [("bob", "", 409), ("alice", "x", 401)])
async def test_another_persons_or_a_forged_code_is_refused(
    reserved: dict[str, object], login: str, suffix: str, status: int
) -> None:
    with pytest.raises(HTTPException) as refused:
        await _upload(_code(login) + suffix)
    assert refused.value.status_code == status
    assert reserved["session_upload_pending"] is True


async def test_one_import_code_imports_each_session_once(
    reserved: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[str] = []

    async def create(_client: object, thread_id: str, *, if_exists: str, **_: object) -> None:
        assert if_exists == "do_nothing"
        created.append(thread_id)
        reserved["session_upload_pending"] = thread_id not in created[:-1]

    monkeypatch.setattr(session_upload, "create_thread", create)
    monkeypatch.setattr(
        session_upload.SessionUploadHeader,
        "thread_metadata",
        AsyncMock(return_value=("Imported", {})),
    )
    code = issue_upload_ticket(
        login="alice", email=None, user_id=None, import_header={"type": "claude"}
    )

    assert await _upload(code, _transcript("s1")) is not None
    assert await _upload(code, _transcript("s2")) is not None
    assert await _upload(code, _transcript("s1")) is None
    assert len(set(created)) == 2


def test_an_id_less_session_keeps_its_key_when_an_ignored_record_is_added() -> None:
    def key(body: bytes) -> str:
        transcript = session_upload._TRANSCRIPTS["claude"]()
        for line in body.decode().splitlines():
            transcript.add(line)
        return transcript.session().key

    snapshot = (json.dumps({"type": "file-history-snapshot", "messageId": "m"}) + "\n").encode()
    assert key(_TRANSCRIPT + snapshot) == key(_TRANSCRIPT)
