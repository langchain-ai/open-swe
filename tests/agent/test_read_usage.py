"""Usage tools bind identity and audience independently of supplied query arguments."""

from collections.abc import Callable
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from openswe.analytics.gateway import GatewaySpend
from openswe.analytics.identity import opaque_person
from openswe.database import analytics
from openswe.run_config import RunConfig
from openswe.tools import usage
from openswe.tools.access import Access
from openswe.users import User
from openswe.users.models import UserIdentity


@pytest.fixture
def usage_context(
    grant_tool_access: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> tuple[User, AsyncMock]:
    monkeypatch.setattr(analytics, "_WORKSPACE_ID", uuid4())
    user = User(id=uuid4(), identities=[UserIdentity(provider="github", external_id="123")])
    monkeypatch.setattr(User, "for_login", AsyncMock(return_value=user))
    monkeypatch.setattr(
        usage, "configurable", lambda: RunConfig(github_login="person", github_linked=True)
    )
    monkeypatch.setattr(usage, "actor_is_admin", AsyncMock(return_value=False))
    query = AsyncMock(return_value={"rows": [], "total_cost_usd": 1})
    monkeypatch.setattr(usage, "usage_leaderboard", query)
    monkeypatch.setattr(
        GatewaySpend, "for_person", AsyncMock(return_value=GatewaySpend(status="ready"))
    )
    grant_tool_access(private=True, owner=True)
    return user, query


async def test_nonadmin_query_is_forced_to_own_user_id(
    usage_context: tuple[User, AsyncMock],
) -> None:
    user, query = usage_context
    result = await usage.read_usage(period="all", cursor="cursor")
    assert result["scope"] == "personal"
    assert query.call_args.kwargs["own_user_id"] == opaque_person("github", user.github_id)
    assert query.call_args.kwargs["admin"] is False
    with pytest.raises(ValueError, match="Only admins"):
        await usage.read_usage(user_id=uuid4())
    assert query.await_count == 1


async def test_admin_defaults_to_all_and_can_filter_person(
    usage_context: tuple[User, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, query = usage_context
    monkeypatch.setattr(usage, "actor_is_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(
        usage,
        "resolve_access",
        AsyncMock(return_value=Access(private=True, owner=True, admin=True, admin_surface=True)),
    )
    result = await usage.read_usage()
    assert result["scope"] == "all_users"
    assert result["gateway_spend"]["status"] == "unsupported_scope"
    assert query.call_args.kwargs["own_user_id"] is None
    assert query.call_args.kwargs["admin"] is True
    target = User(identities=[UserIdentity(provider="github", external_id="456")])
    monkeypatch.setattr(User, "get", AsyncMock(return_value=target))
    await usage.read_usage(user_id=target.id)
    assert query.call_args.kwargs["own_user_id"] == opaque_person("github", 456)
    monkeypatch.setattr(User, "get", AsyncMock(return_value=None))
    with pytest.raises(ValueError, match="does not exist"):
        await usage.read_usage(user_id=uuid4())
    assert query.await_count == 2


async def test_admin_without_private_admin_surface_cannot_read_all(
    usage_context: tuple[User, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    user, query = usage_context
    monkeypatch.setattr(usage, "actor_is_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(
        usage,
        "resolve_access",
        AsyncMock(return_value=Access(private=True, owner=True, admin=True)),
    )
    with pytest.raises(ValueError, match="private admin"):
        await usage.read_usage()
    query.assert_not_awaited()
    await usage.read_usage(personal_only=True)
    assert query.call_args.kwargs["own_user_id"] == opaque_person("github", user.github_id)


async def test_missing_immutable_identity_cannot_query_unscoped_usage(
    usage_context: tuple[User, AsyncMock],
) -> None:
    user, query = usage_context
    user.identities.clear()
    with pytest.raises(ValueError, match="verified immutable GitHub identity"):
        await usage.read_usage(include_gateway_spend=False)
    query.assert_not_awaited()


async def test_shared_usage_read_is_refused(
    usage_context: tuple[User, AsyncMock], grant_tool_access: Callable[..., None]
) -> None:
    _, query = usage_context
    grant_tool_access(admin=True, sole=True)
    assert (await usage.read_usage())["ok"] is False
    query.assert_not_awaited()


async def test_month_to_date_uses_existing_gateway_identity_and_policy_windows(
    usage_context: tuple[User, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    user, query = usage_context
    gateway = AsyncMock(return_value=GatewaySpend(status="sign_in_required"))
    monkeypatch.setattr(GatewaySpend, "for_person", gateway)
    result = await usage.read_usage(period="mtd")
    assert query.call_args.kwargs["period"] == "mtd"
    assert result["gateway_spend"] == {"status": "sign_in_required", "limits": []}
    assert gateway.call_args.args[0].user_id == user.id
    assert gateway.call_args.args[0].resolved_user is user
    cost_note = result["cost_note"]
    assert isinstance(cost_note, str) and "external gateway" in cost_note
    await usage.read_usage(include_gateway_spend=False)
    assert gateway.await_count == 1


async def test_mcp_caller_uses_authenticated_identity(
    usage_context: tuple[User, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        usage,
        "configurable",
        lambda: RunConfig(github_login="actual-caller", source="mcp", github_linked=True),
    )
    resolve = AsyncMock(return_value=usage_context[0])
    monkeypatch.setattr(User, "for_login", resolve)
    await usage.read_usage()
    resolve.assert_awaited_once_with("github", "actual-caller")


async def test_unlinked_person_uses_immutable_user_identity(
    usage_context: tuple[User, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    user, _ = usage_context
    monkeypatch.setattr(
        usage,
        "configurable",
        lambda: RunConfig(github_login="stale-owner", user_id=str(user.id), github_linked=False),
    )
    resolve = AsyncMock(return_value=user)
    monkeypatch.setattr(User, "for_login", resolve)
    await usage.read_usage(include_gateway_spend=False)
    resolve.assert_awaited_once_with("github", f"user:{user.id}")
