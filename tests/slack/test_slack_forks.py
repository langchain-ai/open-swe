"""A `/btw` question or a breakout branches off its conversation's agent thread, or seeds from Slack."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import JsonValue

from openswe.slack import forks, webhook
from openswe.slack.request import SlackRequest
from openswe.threads import runtimes
from openswe.threads.index import IndexedThread

pytestmark = [pytest.mark.real_forks, pytest.mark.asyncio]

_SOURCE = "11111111-1111-4111-8111-111111111111"


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


async def test_a_branch_starts_with_what_an_mda_conversation_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        forks.index,
        "get",
        AsyncMock(return_value=_indexed({"channel": "slack", "source": "slack"})),
    )

    assert await forks.branches_from(_SOURCE, login="ada") is True


@pytest.mark.parametrize(
    "metadata",
    [
        # A thread the backend holds runs on its own Agent Server, not MDA's.
        {"source": "slack"},
        # A private thread is branched off only for its owner.
        {"channel": "slack", "source": "slack", "visibility": "private", "owner_login": "grace"},
    ],
    ids=["backend-thread", "someone-elses-private-thread"],
)
async def test_a_branch_seeds_from_slack_where_it_may_not_read_the_thread(
    monkeypatch: pytest.MonkeyPatch, metadata: dict[str, JsonValue]
) -> None:
    monkeypatch.setattr(forks.index, "get", AsyncMock(return_value=_indexed(metadata)))

    assert await forks.branches_from(_SOURCE, login="ada") is False


async def test_a_branch_with_no_conversation_thread_seeds_from_slack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index_get = AsyncMock(return_value=None)
    monkeypatch.setattr(forks.index, "get", index_get)

    assert await forks.branches_from(None, login="ada") is False
    assert await forks.branches_from(_SOURCE, login="ada") is False


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch) -> dict[str, JsonValue | None]:
    held: dict[str, JsonValue | None] = {"state": None}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/threads/{_SOURCE}/state":
            if held["state"] is None:
                return httpx.Response(404, json={"detail": "not found"})
            return httpx.Response(200, json=held["state"])
        return httpx.Response(500)

    runtime = runtimes.MdaRuntime("https://mda.test")
    runtime._read_http = httpx.AsyncClient(  # noqa: SLF001
        base_url="https://mda.test", transport=httpx.MockTransport(handle)
    )
    monkeypatch.setenv("MDA_RUNTIME_URL", "https://mda.test")
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "test-signing-key-0123456789abcdef")
    monkeypatch.setattr(runtimes.RUNTIMES, "_mda", runtime)
    return held


async def test_held_timestamps_are_the_slack_messages_the_thread_got(
    state: dict[str, JsonValue | None],
) -> None:
    assert await forks.held_timestamps(_SOURCE) == set()

    envelope = (
        '<message sender="user:1" channel="slack:C1" surface="slack" kind="human">'
        '<data timestamp="171.2" explicit_bot_mention="true"/>help</message>'
    )
    state["state"] = {"values": {"messages": [{"type": "human", "content": envelope}]}}
    held = await forks.held_timestamps(_SOURCE)

    assert held == forks.input_message_timestamps(envelope)


@pytest.mark.parametrize(
    ("held_thread_id", "exists", "holder"),
    [
        ("", True, "run-thread"),
        ("", False, None),
        # A branch's new thread doesn't exist yet, but starts with its conversation's.
        ("conversation-thread", False, "conversation-thread"),
    ],
    ids=["follow-up", "new-thread", "branch"],
)
def test_the_prompt_leaves_out_what_the_runs_thread_starts_with(
    held_thread_id: str, exists: bool, holder: str | None
) -> None:
    request = SlackRequest(channel_id="C1", thread_ts="1.0", held_thread_id=held_thread_id)

    assert webhook.held_context_thread(request, "run-thread", exists=exists) == holder
