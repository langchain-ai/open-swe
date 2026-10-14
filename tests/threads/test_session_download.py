import gzip
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openswe.dashboard.oauth import DownloadTicket
from openswe.threads import access, session_download


@pytest.fixture(autouse=True)
def private_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret")
    metadata = {
        "source": "dashboard",
        "visibility": "private",
        "owner_login": "alice",
        "title": "Fix the bug",
        "repo_owner": "acme",
        "repo_name": "app",
        "branch_name": "feature",
    }
    threads = SimpleNamespace(
        get=AsyncMock(return_value={"thread_id": "t1", "metadata": metadata}),
        get_state=AsyncMock(
            return_value={"values": {"messages": [{"type": "human", "content": "fix the bug"}]}}
        ),
    )
    client = SimpleNamespace(threads=threads)
    for module in (access, session_download):
        monkeypatch.setattr(module, "langgraph_client", lambda: client)


async def _download(token: str) -> list[dict[str, object]]:
    body = b"".join(await session_download.download_session(DownloadTicket.decode(token)))
    return [json.loads(line) for line in gzip.decompress(body).decode().splitlines()]


def _token(login: str) -> str:
    return DownloadTicket(sub=login, thread_id="t1", session_id="s1", cwd="/work/app").issue()


async def test_the_owner_downloads_a_private_thread_as_a_claude_code_session() -> None:
    records = await _download(_token("alice"))

    assert records[0]["message"] == {"role": "user", "content": "fix the bug"}
    assert {record.get("sessionId") for record in records} == {"s1"}
    assert records[-1] == {"type": "custom-title", "customTitle": "Fix the bug", "sessionId": "s1"}


@pytest.mark.parametrize(("login", "suffix", "status"), [("bob", "", 404), ("alice", "x", 401)])
async def test_another_persons_or_a_forged_token_is_refused(
    login: str, suffix: str, status: int
) -> None:
    with pytest.raises(HTTPException) as refused:
        await _download(_token(login) + suffix)
    assert refused.value.status_code == status
