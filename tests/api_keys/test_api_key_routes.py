from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI

from agent.dashboard import oauth, routes
from agent.workspaces.store import WORKSPACES, WorkspaceCreate

_ADMIN_SESSION = {"sub": "admin", "email": "admin@example.com"}
_USER_SESSION = {"sub": "intern", "email": "intern@example.com"}


def _client_for(session: dict[str, str]) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[oauth.require_session] = lambda: session
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    )


@pytest.fixture
async def workspace(monkeypatch: pytest.MonkeyPatch, registry_db: None) -> AsyncIterator[str]:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://test")
    record = await WORKSPACES.create(WorkspaceCreate(name="Core", repos=["acme/api"]), "admin")
    yield record.slug


@pytest.fixture
async def admin_client(workspace: str) -> AsyncIterator[httpx.AsyncClient]:
    async with _client_for(_ADMIN_SESSION) as client:
        yield client


def _expiry(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


async def test_mint_returns_the_secret_once_and_never_again(
    admin_client: httpx.AsyncClient, workspace: str
) -> None:
    response = await admin_client.post(
        "/dashboard/api/admin/api-keys",
        json={"workspace": workspace, "name": "CI", "expires_at": _expiry(30)},
    )

    assert response.status_code == 201
    created = response.json()
    secret = created["secret"]
    assert secret.startswith("osk_")
    assert created["key_suffix"] == secret[-6:]
    assert created["created_by"] == "admin"

    listing = await admin_client.get("/dashboard/api/admin/api-keys")
    assert listing.status_code == 200
    assert secret not in listing.text
    [key] = listing.json()
    assert key["id"] == created["id"]
    assert key["status"] == "active"
    assert key["last_used_at"] is None
    assert key["revoked_at"] is None
    assert "secret" not in key


async def test_listing_filters_by_workspace(
    admin_client: httpx.AsyncClient, workspace: str
) -> None:
    await WORKSPACES.create(WorkspaceCreate(name="OSS", repos=["acme/oss"]), "admin")
    await admin_client.post(
        "/dashboard/api/admin/api-keys",
        json={"workspace": workspace, "name": "CI", "expires_at": _expiry(30)},
    )
    await admin_client.post(
        "/dashboard/api/admin/api-keys",
        json={"workspace": "oss", "name": "CI", "expires_at": _expiry(30)},
    )

    scoped = await admin_client.get("/dashboard/api/admin/api-keys", params={"workspace": "oss"})
    assert [key["workspace"] for key in scoped.json()] == ["oss"]


async def test_revoke_is_idempotent_and_shows_up_in_the_listing(
    admin_client: httpx.AsyncClient, workspace: str
) -> None:
    created = (
        await admin_client.post(
            "/dashboard/api/admin/api-keys",
            json={"workspace": workspace, "name": "CI", "expires_at": _expiry(30)},
        )
    ).json()

    assert (
        await admin_client.delete(f"/dashboard/api/admin/api-keys/{created['id']}")
    ).status_code == 204
    assert (
        await admin_client.delete(f"/dashboard/api/admin/api-keys/{created['id']}")
    ).status_code == 204
    assert (await admin_client.delete("/dashboard/api/admin/api-keys/nope")).status_code == 404

    [key] = (await admin_client.get("/dashboard/api/admin/api-keys")).json()
    assert key["status"] == "revoked"
    assert key["revoked_at"] is not None


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"name": "CI", "expires_at": _expiry(-1)}, 422),
        ({"name": "CI", "expires_at": _expiry(366)}, 422),
        ({"name": "   ", "expires_at": _expiry(30)}, 422),
        ({"name": "CI", "expires_at": _expiry(30), "workspace": "ghost"}, 404),
    ],
)
async def test_creation_rejects_bad_input(
    admin_client: httpx.AsyncClient, workspace: str, body: dict[str, str], expected: int
) -> None:
    response = await admin_client.post(
        "/dashboard/api/admin/api-keys", json={"workspace": workspace, **body}
    )
    assert response.status_code == expected


async def test_non_admin_sessions_cannot_reach_the_admin_routes(workspace: str) -> None:
    async with _client_for(_USER_SESSION) as client:
        assert (await client.get("/dashboard/api/admin/api-keys")).status_code == 403
        created = await client.post(
            "/dashboard/api/admin/api-keys",
            json={"workspace": workspace, "name": "CI", "expires_at": _expiry(30)},
        )
        assert created.status_code == 403
        assert (await client.delete("/dashboard/api/admin/api-keys/whatever")).status_code == 403
