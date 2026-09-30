import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.run_config import RunConfig
from agent.source_context import SlackThreadRef
from agent.threads import admin_approval as approvals
from agent.tools import access as tool_access
from agent.tools.access import Policy, access, ack, resolve_access
from tests.conftest import FakeStore

_THREAD = "owner-thread"
_SLACK = SlackThreadRef.model_validate(
    {
        "channel_id": "C1",
        "thread_ts": "1.0",
        "triggering_user_id": "UA",
        "channel_context": {
            "is_im": False,
            "is_mpim": False,
            "is_ext_shared": False,
            "is_pending_ext_shared": False,
        },
    }
)
_WRITE = Policy(trusted="admin_thread", actor="admin", sole=ack("id"))
_READ = Policy(trusted="admin_thread", actor="admin")


@pytest.fixture
def owner_run(monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore) -> RunConfig:
    metadata: dict[str, object] = {
        "owner_type": "user",
        "owner_login": "alice",
        "visibility": "public",
        "participant_logins": ["alice", "bob", "carol"],
        "source_context": {"slack_thread": _SLACK.model_dump(exclude={"channel_context"})},
    }
    monkeypatch.setattr(
        approvals.SlackChannel, "context_for", AsyncMock(return_value=_SLACK.channel_context)
    )
    monkeypatch.setenv("CONFIGURED_ADMINS", "alice,bob")
    monkeypatch.setattr(
        approvals.langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(get=AsyncMock(return_value={"metadata": metadata}))
        ),
    )
    identities = {"UA": "alice", "UB": "bob", "UC": "carol"}
    monkeypatch.setattr(approvals.User, "login_for_slack", AsyncMock(side_effect=identities.get))
    monkeypatch.setattr(approvals.User, "email_for_login", AsyncMock(return_value=None))
    monkeypatch.setattr(approvals, "post_slack_ephemeral_message", AsyncMock(return_value=True))
    lock = asyncio.Lock()

    @asynccontextmanager
    async def locked(_thread_id: str) -> AsyncIterator[None]:
        async with lock:
            yield

    monkeypatch.setattr(approvals, "_lock", locked)
    cfg = RunConfig(
        thread_id=_THREAD,
        source="slack",
        github_login="alice",
        slack_thread=_SLACK,
    )
    monkeypatch.setattr(tool_access, "configurable", lambda: cfg)
    return cfg


async def _pending(cfg: RunConfig) -> approvals.Approval:
    assert await approvals.authorize_admin_write(cfg, "write", {"name": "old"}) == {
        "ok": False,
        "status": "approval_pending",
    }
    record = await approvals.APPROVALS.get(_THREAD)
    assert record is not None
    return record


async def _approve(request_id: str, user: str = "UA") -> bool:
    return await approvals.decide_admin_approval(
        _THREAD,
        request_id,
        slack_user_id=user,
        channel_id="C1",
        thread_ts="1.0",
        approved=True,
    )


async def test_mixed_thread_exposes_only_owner_write_approval(owner_run: RunConfig) -> None:
    resolved = await resolve_access(owner_run)
    assert resolved.mode(_WRITE) == "approval"
    assert resolved.mode(_READ) is None
    assert not resolved.sole
    other_admin = owner_run.model_copy(
        update={
            "github_login": "bob",
            "slack_thread": _SLACK.model_copy(update={"triggering_user_id": "UB"}),
        }
    )
    assert (await resolve_access(other_admin)).mode(_WRITE) is None
    spoofed_owner = owner_run.model_copy(
        update={"slack_thread": _SLACK.model_copy(update={"triggering_user_id": "UB"})}
    )
    assert await approvals.approval_owner(spoofed_owner) is None


@pytest.mark.parametrize("user", ["UB", "UC", "unknown"])
async def test_only_owner_can_approve(owner_run: RunConfig, user: str) -> None:
    record = await _pending(owner_run)
    assert not await _approve(record.request_id, user)
    assert await approvals.authorize_admin_write(owner_run, "write", {"name": "old"}) == {
        "ok": False,
        "status": "approval_pending",
    }
    assert await _approve(record.request_id)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [("different_tool", {"name": "old"}), ("write", {"name": "changed"})],
)
async def test_changed_call_invalidates_previous_approval(
    owner_run: RunConfig, tool: str, arguments: dict[str, object]
) -> None:
    original = await _pending(owner_run)
    assert await _approve(original.request_id)
    assert await approvals.authorize_admin_write(owner_run, tool, arguments) == {
        "ok": False,
        "status": "approval_pending",
    }
    replacement = await approvals.APPROVALS.get(_THREAD)
    assert replacement is not None
    assert replacement.request_id != original.request_id
    assert not await _approve(original.request_id)
    assert await approvals.authorize_admin_write(owner_run, "write", {"name": "old"}) is not None


@pytest.mark.parametrize("approve_first", [False, True])
async def test_revoked_admin_cannot_approve_or_execute(
    owner_run: RunConfig, monkeypatch: pytest.MonkeyPatch, approve_first: bool
) -> None:
    record = await _pending(owner_run)
    if approve_first:
        assert await _approve(record.request_id)
    monkeypatch.setenv("CONFIGURED_ADMINS", "bob")
    assert not await _approve(record.request_id)
    blocked = await approvals.authorize_admin_write(owner_run, "write", {"name": "old"})
    assert blocked is not None and blocked["ok"] is False
    assert (await resolve_access(owner_run)).mode(_WRITE) is None


async def test_channel_becoming_external_blocks_approved_action(
    owner_run: RunConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = await _pending(owner_run)
    assert await _approve(record.request_id)
    assert _SLACK.channel_context is not None
    monkeypatch.setattr(
        approvals.SlackChannel,
        "context_for",
        AsyncMock(return_value=_SLACK.channel_context.model_copy(update={"is_ext_shared": True})),
    )
    assert not await _approve(record.request_id)
    assert await approvals.authorize_admin_write(owner_run, "write", {"name": "old"}) is not None


async def test_expired_approval_requires_a_new_decision(owner_run: RunConfig) -> None:
    record = await _pending(owner_run)
    assert await _approve(record.request_id)
    record.status = "approved"
    record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await approvals.APPROVALS.put(_THREAD, record)
    assert not await _approve(record.request_id)
    assert await approvals.authorize_admin_write(owner_run, "write", {"name": "old"}) == {
        "ok": False,
        "status": "approval_pending",
    }


async def test_concurrent_retries_consume_approval_once(owner_run: RunConfig) -> None:
    original = await _pending(owner_run)
    assert await _approve(original.request_id)
    results = await asyncio.gather(
        approvals.authorize_admin_write(owner_run, "write", {"name": "old"}),
        approvals.authorize_admin_write(owner_run, "write", {"name": "old"}),
    )
    assert sum(result is None for result in results) == 1
    assert not await _approve(original.request_id)


async def test_decorator_blocks_reads_and_projects_approved_write(owner_run: RunConfig) -> None:
    calls: list[str] = []

    @access(_WRITE, per_call=lambda arguments: _READ if arguments["action"] == "read" else _WRITE)
    async def tool(action: str = "write") -> dict[str, object]:
        calls.append(action)
        return {"ok": True, "id": "created", "settings": {"secret": "withheld"}}

    assert (await tool("read"))["ok"] is False
    assert await tool() == {"ok": False, "status": "approval_pending"}
    assert calls == []
    record = await approvals.APPROVALS.get(_THREAD)
    assert record is not None
    assert await _approve(record.request_id)
    assert (await tool("read"))["ok"] is False
    assert await tool(action="write") == {"ok": True, "id": "created"}
    assert calls == ["write"]
    assert (await tool())["status"] == "approval_pending"
    assert calls == ["write"]
