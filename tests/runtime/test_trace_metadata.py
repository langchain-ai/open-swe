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
    monkeypatch.setattr(runtimes, "remote_run_context", AsyncMock(return_value={"graph": "agent"}))
    rewritten = await runtimes.MdaRuntime("http://mda.test").run_payload(
        "original-thread",
        {
            "assistant_id": "agent",
            "metadata": {"owner_ls_user_id": "caller", "user_id": str(uuid4())},
            "config": {"metadata": {"owner_ls_user_id": "caller"}},
        },
        thread_metadata={},
    )
    expected = str(ls_user_id) if state == "linked" else None
    assert rewritten["metadata"]["owner_ls_user_id"] == expected
    assert rewritten["config"]["metadata"]["owner_ls_user_id"] == expected
    assert rewritten["context"]["owner_ls_user_id"] == expected
    handle_holder.assert_not_awaited()
    if state in {"linked", "unlinked", "ambiguous"}:
        get_owner.assert_awaited_once_with(owner_id)
    else:
        get_owner.assert_not_awaited()
