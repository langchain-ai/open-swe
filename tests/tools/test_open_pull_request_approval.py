"""The open_pull_request approval gate for PRs attributed to someone else."""

import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import agent.tools.open_pull_request  # noqa: F401  (resolves the lazy tools module)
from agent.run_config import RunConfig
from agent.slack import client as slack_client
from agent.threads import pr_approval
from agent.users import User

opr = sys.modules["agent.tools.open_pull_request"]
_CFG = RunConfig.parse({"thread_id": "thread-1", "source": "slack", "github_login": "bob"})


@pytest.fixture
def gate(monkeypatch, fake_store):
    """Bob's run attributes the PR to Alice; thread metadata lives in memory."""
    monkeypatch.setattr(opr, "pr_author_login", AsyncMock(return_value="alice"))
    metadata: dict[str, object] = {}

    async def update(**kwargs: object) -> None:
        patch = kwargs.get("metadata")
        if isinstance(patch, dict):
            metadata.update(patch)

    client = SimpleNamespace(
        threads=SimpleNamespace(get=AsyncMock(return_value={"metadata": metadata}), update=update)
    )
    monkeypatch.setattr(pr_approval, "get_client", lambda: client)
    dm = AsyncMock(return_value=("123.456", None))
    monkeypatch.setattr(slack_client, "post_slack_top_level_message_with_ts", dm)
    monkeypatch.setattr(opr, "_APPROVAL_WAIT_SECONDS", 4.0)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    return SimpleNamespace(dm=dm, metadata=metadata)


def _alice(monkeypatch, slack_id: str) -> None:
    user = SimpleNamespace(slack_user_id=slack_id)
    monkeypatch.setattr(User, "for_login", AsyncMock(return_value=user))


async def _open() -> dict[str, object] | None:
    return await opr._pr_approval(
        _CFG, "token", "user", owner="o", repo="r", head="h", base="b", title="t"
    )


@pytest.mark.asyncio
async def test_card_is_dmed_to_the_author_and_times_out_pending(gate, monkeypatch):
    _alice(monkeypatch, "U-ALICE")

    payload = await _open()

    assert payload is not None and payload["pr_approval"] == "pending"
    gate.dm.assert_awaited_once()
    assert gate.dm.await_args.args[0] == "U-ALICE"
    card = json.loads(gate.dm.await_args.kwargs["blocks"][1]["elements"][0]["value"])
    assert card == {
        "type": "pr_approval",
        "action": "approve",
        "fingerprint": payload["pr_approval_fingerprint"],
        "thread_id": "thread-1",
    }


@pytest.mark.asyncio
async def test_approval_during_the_wait_lets_the_pr_open(gate, monkeypatch):
    _alice(monkeypatch, "U-ALICE")

    async def approve_on_poll(_seconds: float) -> None:
        for record in gate.metadata.get("pr_approvals", {}).values():
            record["status"] = pr_approval.PR_APPROVAL_APPROVED

    monkeypatch.setattr(asyncio, "sleep", approve_on_poll)

    assert await _open() is None


@pytest.mark.asyncio
async def test_author_without_slack_is_never_published_as(gate, monkeypatch):
    _alice(monkeypatch, "")

    payload = await _open()

    assert payload is not None and payload["success"] is False
    gate.dm.assert_not_awaited()


@pytest.mark.asyncio
async def test_always_allow_skips_the_dm(gate, monkeypatch):
    _alice(monkeypatch, "U-ALICE")
    await pr_approval.set_always_allow("alice", allow=True, requester="bob")

    assert await _open() is None
    gate.dm.assert_not_awaited()
