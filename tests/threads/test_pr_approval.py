"""The multiplayer PR approval gate on open_pull_request."""

import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.threads import pr_approval
from agent.users import User, UserPreferences, UserPreferencesPatch

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
        "notified": False,
        "requested_at": "2026-01-01T00:00:00+00:00",
    }
    record.update(overrides)
    return record


def test_fingerprint_is_per_thread_and_author() -> None:
    fingerprint = pr_approval.pr_approval_fingerprint(thread_id="t1", author_login="alice")
    assert pr_approval.pr_approval_fingerprint(thread_id="t1", author_login="Alice") == fingerprint
    assert pr_approval.pr_approval_fingerprint(thread_id="t2", author_login="alice") != fingerprint
    assert pr_approval.pr_approval_fingerprint(thread_id="t1", author_login="carol") != fingerprint


@pytest.mark.asyncio
async def test_pending_record_created_once_and_idempotent(fake_store) -> None:
    metadata: dict[str, object] = _metadata()
    client, update = _client(metadata)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pr_approval, "get_client", lambda: client)
        kwargs = {
            "fingerprint": "fp1",
            "author_login": "alice",
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
async def test_decision_records_actor_and_always_allow(fake_store) -> None:
    metadata: dict[str, object] = {"pr_approvals": {"fp1": _thread_record("fp1")}}
    client, _update = _client(metadata)
    allow = AsyncMock(return_value=UserPreferences(pr_attribution_always_allowed=True))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pr_approval, "get_client", lambda: client)
        mp.setattr(User, "update_preferences", allow)

        record = await pr_approval.decide_pr_approval(
            "thread-1", "fp1", approved=True, actor="alice", always_allow=True
        )
        assert record is not None
        assert record["status"] == pr_approval.PR_APPROVAL_APPROVED
        assert record["decided_by"] == "alice"
        allow.assert_awaited_once_with(
            "alice", UserPreferencesPatch(pr_attribution_always_allowed=True)
        )

        assert (
            await pr_approval.decide_pr_approval("thread-1", "missing", approved=True, actor="a")
            is None
        )


def test_approval_card_has_three_actions() -> None:
    blocks = reply.build_pr_approval_blocks("Approve?", "fp1", "thread-1")
    actions = [json.loads(element["value"]) for element in blocks[1]["elements"]]
    assert [a["action"] for a in actions] == ["approve", "always_allow", "reject"]
    assert {(a["fingerprint"], a["thread_id"]) for a in actions} == {("fp1", "thread-1")}
