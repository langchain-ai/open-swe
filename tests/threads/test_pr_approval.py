"""The multiplayer PR approval gate on open_pull_request."""

import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.threads import pr_approval

reply = importlib.import_module("agent.slack.tools.reply")


def _metadata() -> dict[str, object]:
    return {}


def _client(metadata: dict[str, object]) -> tuple[SimpleNamespace, AsyncMock]:
    """A thread client whose ``update`` writes through to ``metadata``."""
    update = AsyncMock()

    async def _update(*args: object, **kwargs: object) -> None:
        await update(*args, **kwargs)
        patch = kwargs.get("metadata")
        if isinstance(patch, dict):
            metadata.update(patch)

    return (
        SimpleNamespace(
            threads=SimpleNamespace(
                get=AsyncMock(return_value={"metadata": metadata}),
                update=_update,
            )
        ),
        update,
    )


def _thread_record(fingerprint: str, **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "fingerprint": fingerprint,
        "status": pr_approval.PR_APPROVAL_PENDING,
        "author_login": "alice",
        "requester_login": "bob",
        "notified": False,
        "requested_at": "2026-01-01T00:00:00+00:00",
    }
    record.update(overrides)
    return record


def test_fingerprint_binds_thread_author_requester_repo_and_branch() -> None:
    kwargs = {
        "thread_id": "t1",
        "author_login": "alice",
        "requester_login": "bob",
        "owner": "langchain-ai",
        "repo": "langchainplus",
        "head": "branch",
    }
    assert pr_approval.pr_approval_fingerprint(**kwargs) == pr_approval.pr_approval_fingerprint(
        **kwargs
    )
    assert pr_approval.pr_approval_fingerprint(
        **{**kwargs, "head": "other"}
    ) != pr_approval.pr_approval_fingerprint(**kwargs)
    assert pr_approval.pr_approval_fingerprint(
        **{**kwargs, "author_login": "carol"}
    ) != pr_approval.pr_approval_fingerprint(**kwargs)


@pytest.mark.asyncio
async def test_pending_record_created_once_and_idempotent(fake_store) -> None:
    metadata: dict[str, object] = _metadata()
    client, update = _client(metadata)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pr_approval, "get_client", lambda: client)
        kwargs = {
            "fingerprint": "fp1",
            "author_login": "alice",
            "requester_login": "bob",
            "owner": "o",
            "repo": "r",
            "head": "h",
            "base": "b",
            "title": "t",
            "draft": True,
        }
        record = await pr_approval.ensure_pr_approval_pending("thread-1", **kwargs)
        assert record["status"] == pr_approval.PR_APPROVAL_PENDING
        again = await pr_approval.ensure_pr_approval_pending("thread-1", **kwargs)
        assert again["requested_at"] == record["requested_at"]
        assert update.await_count == 1

        metadata["pr_approvals"] = {
            "fp1": _thread_record("fp1", status=pr_approval.PR_APPROVAL_REJECTED)
        }
        decided = await pr_approval.ensure_pr_approval_pending("thread-1", **kwargs)
        assert decided["status"] == pr_approval.PR_APPROVAL_REJECTED


@pytest.mark.asyncio
async def test_always_allow_scopes(fake_store) -> None:
    assert await pr_approval.always_allow_for("alice", "bob") == "none"

    await pr_approval.set_always_allow("alice", allow=True, requester="bob")
    assert await pr_approval.always_allow_for("alice", "bob") == "requester"
    assert await pr_approval.always_allow_for("alice", "carol") == "none"

    await pr_approval.set_always_allow("alice", allow=True)
    assert await pr_approval.always_allow_for("alice", "carol") == "all"

    # Clearing stores an explicit none, which reads back as no preference.
    await pr_approval.set_always_allow("alice", allow=False)
    assert await pr_approval.always_allow_for("alice", "bob") == "none"


@pytest.mark.asyncio
async def test_decision_records_actor_and_always_allow(fake_store) -> None:
    metadata: dict[str, object] = {"pr_approvals": {"fp1": _thread_record("fp1")}}
    client, _update = _client(metadata)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pr_approval, "get_client", lambda: client)

        record = await pr_approval.decide_pr_approval(
            "thread-1", "fp1", approved=True, actor="alice", always_allow=True
        )
        assert record is not None
        assert record["status"] == pr_approval.PR_APPROVAL_APPROVED
        assert record["decided_by"] == "alice"
        preference = await pr_approval.get_always_allow("alice")
        assert preference is not None
        assert preference.scope == "requester"
        assert preference.requester == "bob"

        assert (
            await pr_approval.decide_pr_approval("thread-1", "missing", approved=True, actor="a")
            is None
        )


def test_approval_card_has_three_actions() -> None:
    blocks = reply.build_pr_approval_blocks("Approve?", "fp1", "thread-1")
    actions = [json.loads(element["value"]) for element in blocks[1]["elements"]]
    assert [a["action"] for a in actions] == ["approve", "always_allow", "reject"]
    assert {(a["fingerprint"], a["thread_id"]) for a in actions} == {("fp1", "thread-1")}
