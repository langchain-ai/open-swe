from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import Depends, FastAPI
from langchain_core.runnables import RunnableConfig
from sqlalchemy import select

from openswe.dashboard import deps
from openswe.dashboard.oauth import issue_session, require_same_origin_for_mutations
from openswe.database import postgres
from openswe.routing.routes import router
from openswe.routing.store import INTEGRATIONS, RepositoryBinding
from openswe.routing.tools import unbind_repository
from openswe.slack.channels import ChannelLookupUnavailable, SlackChannel
from tests.conftest import LangSmithUsers, serve_threads_from


@pytest.fixture
async def client(
    monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://test")
    app = FastAPI()
    app.include_router(
        router, prefix="/dashboard/api", dependencies=[Depends(require_same_origin_for_mutations)]
    )
    app.dependency_overrides[deps.admin_session] = lambda: {"sub": "admin"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    ) as client:
        yield client


async def test_repository_writes_are_strict_and_separate_binding_from_workflow_grant(
    client: httpx.AsyncClient,
) -> None:
    path = "/dashboard/api/routing/repositories/acme/widgets"
    refused = await client.put(path, json={"may_start_threads": "true"})
    assert refused.status_code == 422
    bound = await client.put(path, json={"may_start_threads": False})
    assert bound.status_code == 200
    assert bound.json() == {"repo": "acme/widgets", "may_start_threads": False}
    assert not await INTEGRATIONS.repository_may_start_threads("acme/widgets")
    assert (await client.put(path, json={"may_start_threads": True})).status_code == 200
    assert await INTEGRATIONS.repository_may_start_threads("acme/widgets")
    assert (await client.delete(path)).status_code == 204
    assert (await client.delete(path)).status_code == 204


async def test_queue_configuration_prevents_unbinding_through_http_and_tool(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = "/dashboard/api/routing/repositories/acme/widgets"
    assert (await client.put(path, json={"may_start_threads": True})).status_code == 200
    async with postgres.session() as session:
        binding = (await session.scalars(select(RepositoryBinding))).one()
        binding.merge_queue_config = {}
        binding_id = binding.id

    refused = await client.delete(path)
    assert refused.status_code == 400
    assert "Cannot unbind" in refused.json()["detail"]
    assert "explicit operator handoff" in refused.json()["detail"]

    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    serve_threads_from(
        monkeypatch,
        AsyncMock(return_value={"metadata": {"visibility": "private", "owner_type": "user"}}),
    )
    config: RunnableConfig = {
        "configurable": {"thread_id": "queue-unbind", "admin_thread": True, "github_login": "admin"}
    }
    with patch("openswe.run_config.get_config", return_value=config):
        assert await unbind_repository("acme/widgets") == {
            "ok": False,
            "error": refused.json()["detail"],
        }

    async with postgres.session() as session:
        retained = (await session.scalars(select(RepositoryBinding))).one()
        assert retained.id == binding_id
        assert retained.merge_queue_config == {}
        assert retained.may_start_threads is True


async def test_invalid_channel_and_unavailable_slack_do_not_create_bindings(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        await client.put(
            "/dashboard/api/routing/slack-channels/D0DM", json={"prompt": "Investigate reports"}
        )
    ).status_code == 400
    assert (
        await client.put("/dashboard/api/routing/slack-channels/C0TEST", json={"mode": "mentions"})
    ).status_code == 422
    for invalid in (None, 42, "x" * 32_001):
        rejected = await client.put(
            "/dashboard/api/routing/slack-channels/C0TEST", json={"prompt": invalid}
        )
        assert rejected.status_code == 422
    monkeypatch.setattr(SlackChannel, "load", AsyncMock(side_effect=ChannelLookupUnavailable(17)))
    retry = await client.put("/dashboard/api/routing/slack-channels/C0TEST", json={"prompt": ""})
    assert retry.status_code == 503
    assert retry.headers["Retry-After"] == "17"
    assert (await INTEGRATIONS.settings()).slack_channels == []


async def test_routing_read_and_write_require_authentication() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/dashboard/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/dashboard/api/routing")).status_code == 401
        assert (
            await client.put(
                "/dashboard/api/routing/repositories/acme/widgets", json={"may_start_threads": True}
            )
        ).status_code == 401


async def test_member_can_read_bindings_but_only_admin_can_change_them(
    registry_db: None, monkeypatch: pytest.MonkeyPatch, langsmith_users: LangSmithUsers
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://test")
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "routing-session-secret-for-auth-tests")
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    member = langsmith_users.add("member")
    admin = langsmith_users.add("admin")
    async with postgres.session() as session:
        session.add_all([member, admin])
    app = FastAPI()
    app.include_router(
        router, prefix="/dashboard/api", dependencies=[Depends(require_same_origin_for_mutations)]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    ) as client:
        client.cookies.set(
            "osw_session",
            issue_session(login="member", email=None, avatar_url=None, user_id=str(member.id)),
        )
        assert (await client.get("/dashboard/api/routing")).json() == {
            "repos": [],
            "slack_channels": [],
        }
        path = "/dashboard/api/routing/repositories/acme/widgets"
        assert (await client.put(path, json={"may_start_threads": True})).status_code == 403
        assert (await client.delete(path)).status_code == 403
        client.cookies.set(
            "osw_session",
            issue_session(login="admin", email=None, avatar_url=None, user_id=str(admin.id)),
        )
        assert (await client.put(path, json={"may_start_threads": False})).status_code == 200
        assert (await client.get("/dashboard/api/routing")).json() == {
            "repos": [{"repo": "acme/widgets", "may_start_threads": False}],
            "slack_channels": [],
        }
