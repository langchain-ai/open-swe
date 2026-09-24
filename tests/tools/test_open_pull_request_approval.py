"""The open_pull_request HITL approval gate for cross-person attribution."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import langgraph_sdk
import pytest

import agent.tools.open_pull_request  # noqa: F401  (resolves the lazy tools module)
from agent import credential_scope
from agent.run_config import RunConfig

opr = sys.modules["agent.tools.open_pull_request"]


def config(source="slack", login="bob", thread_id="thread-1"):
    return {
        "configurable": {
            "thread_id": thread_id,
            "source": source,
            "github_login": login,
            "visibility": "private",
            "owner_type": "user",
            "owner_login": login,
        }
    }


@pytest.fixture
def thread_metadata(monkeypatch):
    """Route ``credential_scope`` thread reads at an in-memory thread record."""
    metadata = {"visibility": "public", "owner_type": "user", "owner_login": "alice"}
    get_thread = AsyncMock(side_effect=lambda _id: {"metadata": metadata})
    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda **_: SimpleNamespace(threads=SimpleNamespace(get=get_thread)),
    )
    return metadata


@pytest.mark.asyncio
async def test_same_person_attribution_never_asks(thread_metadata, monkeypatch):
    """Bob triggering a run that publishes as Bob skips the approval entirely."""
    monkeypatch.setattr("agent.run_config.get_config", lambda: config(login="bob"))
    assert await credential_scope.pr_author_login() == "bob"


@pytest.mark.asyncio
async def test_preauthorized_attribution_skips_the_card(fake_store):
    from agent.threads import pr_approval

    await pr_approval.set_always_allow("alice", allow=True, requester="bob")
    assert await pr_approval.always_allow_for("alice", "bob") == "requester"
    assert await pr_approval.always_allow_for("alice", "carol") == "none"


@pytest.mark.asyncio
async def test_gate_returns_pending_payload_without_slack(thread_metadata, fake_store, monkeypatch):
    """No Slack location to ask in: the gate stays closed rather than publishing."""
    monkeypatch.setattr("agent.run_config.get_config", lambda: config(login="bob"))
    monkeypatch.setattr(opr, "pr_author_login", AsyncMock(return_value="alice"))
    from agent.slack import client as slack_client

    monkeypatch.setattr(
        slack_client,
        "get_active_slack_thread",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(opr, "get_client", lambda **_: SimpleNamespace())

    from agent.threads import pr_approval as pa

    meta: dict[str, object] = {}
    thread_client = SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(return_value={"metadata": meta}),
            update=AsyncMock(),
        )
    )
    monkeypatch.setattr(pa, "get_client", lambda: thread_client)
    payload = await opr._pr_approval(
        RunConfig.parse(config(login="bob")["configurable"]),
        "token",
        "user",
        owner="o",
        repo="r",
        head="h",
        base="b",
        title="t",
    )
    assert payload is not None
    assert payload["success"] is False
    assert payload["pr_approval"] == "pending"
    assert payload["pr_approval_author"] == "alice"
    assert payload["branch_pushed"] is False
    assert payload["pr_approval_fingerprint"]


@pytest.mark.asyncio
async def test_gate_times_out_after_sixty_seconds_with_pending(
    thread_metadata, fake_store, monkeypatch
):
    """The wait polls the approval record and returns pending on timeout."""
    monkeypatch.setattr("agent.run_config.get_config", lambda: config(login="bob"))
    monkeypatch.setattr(opr, "pr_author_login", AsyncMock(return_value="alice"))
    from agent.slack import client as slack_client

    calls = {"active": 0}

    async def active_thread(_client, _thread_id):
        calls["active"] += 1
        return {"channel_id": "C1", "thread_ts": "1.1"}

    monkeypatch.setattr(slack_client, "get_active_slack_thread", active_thread)
    posted = AsyncMock(return_value=("123.456", None))
    monkeypatch.setattr(slack_client, "post_slack_thread_reply_with_ts", posted)
    monkeypatch.setattr(opr, "get_client", lambda **_: SimpleNamespace())

    from agent.threads import pr_approval as pa

    meta: dict[str, object] = {}
    thread_client = SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(return_value={"metadata": meta}),
            update=AsyncMock(),
        )
    )
    monkeypatch.setattr(pa, "get_client", lambda: thread_client)

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(opr, "_APPROVAL_WAIT_SECONDS", 4.0)
    import asyncio

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    payload = await opr._pr_approval(
        RunConfig.parse(config(login="bob")["configurable"]),
        "token",
        "user",
        owner="o",
        repo="r",
        head="h",
        base="b",
        title="t",
    )
    assert payload is not None
    assert payload["pr_approval"] == "pending"
    assert posted.await_count == 1
    card = json.loads(posted.await_args.kwargs["blocks"][1]["elements"][0]["value"])
    assert card["type"] == "pr_approval"
    assert card["action"] == "approve"
    assert payload["pr_approval_fingerprint"] == card["fingerprint"]
