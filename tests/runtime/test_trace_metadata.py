from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from openswe.threads import runtimes
from openswe.threads.store import THREADS
from openswe.users import User
from openswe.users.models import UserIdentity


@pytest.mark.parametrize(
    "state", ["linked", "unlinked", "system", "unavailable", "legacy", "ambiguous"]
)
async def test_run_starts_with_stored_owners_langsmith_identity(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    owner_id, ls_user_id = uuid4(), uuid4()
    owner = User(
        id=owner_id,
        identities=[UserIdentity(provider="langsmith", external_id=str(ls_user_id))]
        if state in {"linked", "legacy"}
        else [
            UserIdentity(provider="langsmith", external_id=str(uuid4())),
            UserIdentity(provider="langsmith", external_id=str(uuid4())),
        ]
        if state == "ambiguous"
        else [],
    )
    get_owner = AsyncMock(return_value=owner)
    monkeypatch.setattr(User, "get", get_owner)
    handle_holder = AsyncMock(return_value=owner)
    monkeypatch.setattr(User, "for_login", handle_holder)
    get_thread = AsyncMock(
        return_value=SimpleNamespace(
            metadata={
                "owner_type": "system" if state == "system" else "user",
                "owner_user_id": str(owner_id) if state != "legacy" else None,
                "owner_login": "reassigned-handle",
            }
        ),
        side_effect=RuntimeError("unavailable") if state == "unavailable" else None,
    )
    monkeypatch.setattr(THREADS, "get", get_thread)
    start = AsyncMock(return_value={"graph": "agent"})
    monkeypatch.setattr(runtimes, "remote_run_context", start)
    rewritten = await runtimes.MdaRuntime("http://mda.test").run_payload(
        "original-thread",
        {
            "assistant_id": "agent",
            "metadata": {"owner_ls_user_id": "caller", "user_id": str(uuid4()), "origin": "github"},
            "config": {"configurable": {"source": "slack", "origin": "slack"}},
        },
        thread_metadata={"source": "slack", "origin": "slack"},
    )
    assert rewritten["metadata"]["origin"] == "github"
    assert rewritten["config"]["metadata"]["origin"] == "github"
    assert start.await_args.args[0]["origin"] == "github"
    assert start.await_args.kwargs["metadata"]["origin"] == "github"
    expected = str(ls_user_id) if state == "linked" else None
    assert rewritten["metadata"]["owner_ls_user_id"] == expected
    assert rewritten["config"]["metadata"]["owner_ls_user_id"] == expected
    assert rewritten["context"]["owner_ls_user_id"] == expected
    handle_holder.assert_not_awaited()
    if state in {"linked", "unlinked", "ambiguous"}:
        get_owner.assert_awaited_once_with(owner_id)
    else:
        get_owner.assert_not_awaited()


@pytest.mark.parametrize(
    ("assistant_id", "run_kind", "config_kind", "thread_kind", "expected"),
    [
        ("agent", None, None, None, "agent"),
        ("agent", "thread_wakeup", None, None, "thread_wakeup"),
        ("reviewer", None, None, "reviewer", "reviewer"),
        ("reviewer", None, None, "reviewer-shadow", "reviewer-shadow"),
        ("agent", "task_message", "follow_up", "review_chat", "task_message"),
        ("agent", None, "follow_up", "review_chat", "follow_up"),
    ],
)
async def test_run_is_stamped_with_its_kind(
    monkeypatch: pytest.MonkeyPatch,
    assistant_id: str,
    run_kind: str | None,
    config_kind: str | None,
    thread_kind: str | None,
    expected: str,
) -> None:
    monkeypatch.setattr(THREADS, "get", AsyncMock(return_value=None))
    start = AsyncMock(return_value={"graph": assistant_id})
    monkeypatch.setattr(runtimes, "remote_run_context", start)
    rewritten = await runtimes.MdaRuntime("http://mda.test").run_payload(
        "thread",
        {
            "assistant_id": assistant_id,
            "metadata": {"kind": run_kind} if run_kind else {},
            "config": {
                "configurable": {"source": "slack"},
                "metadata": {"kind": config_kind} if config_kind else {},
            },
        },
        thread_metadata={"kind": thread_kind} if thread_kind else {},
    )
    assert rewritten["metadata"]["kind"] == expected
    assert rewritten["config"]["metadata"]["kind"] == expected
