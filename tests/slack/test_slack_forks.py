"""A `/btw` question or a breakout forks its conversation's agent thread, or seeds from Slack."""

import json
from datetime import UTC, datetime
from types import MappingProxyType
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import JsonValue

from openswe.slack import forks
from openswe.threads import runtimes
from openswe.threads.index import IndexedThread

pytestmark = [pytest.mark.real_forks, pytest.mark.asyncio]

_SOURCE = "11111111-1111-4111-8111-111111111111"
_FORK = "22222222-2222-4222-8222-222222222222"
_KEY = forks.branch_key("C1", "171.5")
_INGRESS = {"x-mda-ingress-secret": "secret", "x-mda-source-thread-id": "slack:v1:A1:T1:C1:171.1"}


def _indexed(metadata: dict[str, JsonValue]) -> IndexedThread:
    now = datetime.now(UTC)
    return IndexedThread(
        thread_id=_SOURCE,
        runtime="mda",
        metadata=metadata,
        status="busy",
        created_at=now,
        updated_at=now,
    )


class _Mda:
    """MDA's Agent Server, as the fork reads and writes it."""

    def __init__(self, *, found: list[dict[str, JsonValue]] | None = None) -> None:
        self.found = found or []
        self.requests: list[httpx.Request] = []
        self.state: dict[str, JsonValue] | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/threads/search":
            return httpx.Response(200, json=self.found)
        if path == f"/threads/{_SOURCE}/copy":
            inherited = {
                "owner": "mda:source-thread:slack:slack:v1:A1:T1:C1:171.1",
                "ls_mda_thread": {"origin": "slack"},
                "ls_mda_conversation": "slack:v1:A1:T1:C1:171.1",
                "graph_id": "agent",
                "sandbox_id": "box-1",
                "visibility": "public",
                "owner_login": "ada",
            }
            return httpx.Response(200, json={"thread_id": _FORK, "metadata": inherited})
        if path == f"/threads/{_FORK}" and request.method == "PATCH":
            return httpx.Response(200, json={"thread_id": _FORK})
        if path.endswith("/state"):
            if self.state is None:
                return httpx.Response(404, json={"detail": "not found"})
            return httpx.Response(200, json=self.state)
        return httpx.Response(500)


@pytest.fixture
def mda(monkeypatch: pytest.MonkeyPatch) -> _Mda:
    server = _Mda()
    runtime = runtimes.MdaRuntime("https://mda.test")
    transport = httpx.MockTransport(server.handle)
    runtime._http = httpx.AsyncClient(base_url="https://mda.test", transport=transport)  # noqa: SLF001
    runtime._read_http = httpx.AsyncClient(  # noqa: SLF001
        base_url="https://mda.test", transport=transport
    )
    monkeypatch.setattr(
        runtime,
        "channel_write",
        AsyncMock(return_value=runtimes.ChannelWrite(MappingProxyType(_INGRESS))),
    )
    monkeypatch.setenv("MDA_RUNTIME_URL", "https://mda.test")
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "test-signing-key-0123456789abcdef")
    monkeypatch.setattr(runtimes.RUNTIMES, "_mda", runtime)
    monkeypatch.setattr(
        forks.index,
        "get",
        AsyncMock(return_value=_indexed({"channel": "slack", "source": "slack"})),
    )
    return server


async def test_a_branch_forks_its_conversations_thread_and_starts_clean(mda: _Mda) -> None:
    assert await forks.fork_thread(_SOURCE, key=_KEY, login="ada") == _FORK

    search, copy, patch = mda.requests
    assert json.loads(search.content)["metadata"] == {
        forks.FORK_KEY: _KEY,
        forks.FORK_OF_KEY: _SOURCE,
    }
    # Copied as the conversation that owns the thread, through MDA's trusted-backend ingress.
    assert copy.headers["x-mda-ingress-secret"] == "secret"
    assert patch.headers["x-mda-source-thread-id"] == _INGRESS["x-mda-source-thread-id"]
    # It keeps MDA's owner and thread record, and nothing else of the original's: no
    # sandbox, no conversation that would find it, no visibility or owner.
    assert json.loads(patch.content)["metadata"] == {
        "ls_mda_conversation": None,
        "sandbox_id": None,
        "visibility": None,
        "owner_login": None,
        forks.FORK_KEY: _KEY,
        forks.FORK_OF_KEY: _SOURCE,
    }


async def test_a_call_sent_again_finds_the_fork_it_made(mda: _Mda) -> None:
    mda.found = [{"thread_id": _FORK}]

    assert await forks.fork_thread(_SOURCE, key=_KEY, login="ada") == _FORK
    assert [request.url.path for request in mda.requests] == ["/threads/search"]


@pytest.mark.parametrize(
    "metadata",
    [
        # A thread the backend holds runs on its own Agent Server, not MDA's.
        {"source": "slack"},
        # A private thread forks only for its owner.
        {"channel": "slack", "source": "slack", "visibility": "private", "owner_login": "grace"},
    ],
    ids=["backend-thread", "someone-elses-private-thread"],
)
async def test_a_branch_seeds_from_slack_where_it_may_not_fork(
    mda: _Mda, monkeypatch: pytest.MonkeyPatch, metadata: dict[str, JsonValue]
) -> None:
    monkeypatch.setattr(forks.index, "get", AsyncMock(return_value=_indexed(metadata)))

    assert await forks.fork_thread(_SOURCE, key=_KEY, login="ada") is None
    assert mda.requests == []


async def test_a_branch_with_no_conversation_thread_seeds_from_slack(mda: _Mda) -> None:
    assert await forks.fork_thread(None, key=_KEY, login="ada") is None
    assert mda.requests == []


async def test_a_fork_that_fails_seeds_the_branch_from_slack(
    mda: _Mda, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/threads/search":
            return httpx.Response(200, json=[])
        return httpx.Response(403, json={"detail": "forbidden"})

    transport = httpx.MockTransport(refuse)
    runtime = runtimes.RUNTIMES.mda_runtime
    runtime._http = httpx.AsyncClient(base_url="https://mda.test", transport=transport)  # noqa: SLF001

    assert await forks.fork_thread(_SOURCE, key=_KEY, login="ada") is None


async def test_held_timestamps_are_the_slack_messages_the_thread_got(mda: _Mda) -> None:
    assert await forks.held_timestamps(_FORK) == set()

    envelope = (
        '<message sender="user:1" channel="slack:C1" surface="slack" kind="human">'
        '<data timestamp="171.2" explicit_bot_mention="true"/>help</message>'
    )
    mda.state = {"values": {"messages": [{"type": "human", "content": envelope}]}}
    held = await forks.held_timestamps(_FORK)

    assert held == forks.input_message_timestamps(envelope)


async def test_find_fork_answers_none_when_mda_cant_say(
    mda: _Mda, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    runtime = runtimes.RUNTIMES.mda_runtime
    runtime._read_http = httpx.AsyncClient(  # noqa: SLF001
        base_url="https://mda.test", transport=httpx.MockTransport(fail)
    )

    assert await forks.find_fork(_SOURCE, _KEY) is None
