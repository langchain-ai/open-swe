from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from agent.dashboard import user_preferences
from agent.dashboard.oauth import require_session
from agent.dashboard.routes import router
from agent.utils import langsmith

THREAD_ID = "a743f4f9-7a4b-4f02-aef8-55297febfc30"
URL = f"/dashboard/api/me/local-trace-url/{THREAD_ID}"


@pytest.fixture
async def app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


@pytest.fixture
async def authenticated(app: FastAPI) -> None:
    async def session() -> dict[str, str]:
        return {"sub": "alice"}

    app.dependency_overrides[require_session] = session


@pytest.mark.parametrize("preference", ["alice-local", None, "", "   "])
@pytest.mark.usefixtures("authenticated")
async def test_local_trace_url_resolves_callers_project(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, preference: str | None
) -> None:
    async def get_value(namespace: list[str], login: str) -> dict[str, object]:
        assert namespace == user_preferences.USER_PREFERENCES_NAMESPACE
        return {"local_tracing_project": preference if login == "alice" else "other-user"}

    monkeypatch.setattr(user_preferences, "get_value", get_value)
    monkeypatch.setenv("LANGSMITH_PROJECT", "deployment-local")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://smith.example/api")
    monkeypatch.setattr(langsmith, "resolve_tenant_id", AsyncMock(return_value="tenant-id"))
    resolve = AsyncMock(return_value="project-id")
    monkeypatch.setattr(langsmith, "_resolve_project_id_by_name", resolve)

    response = await client.get(URL)

    assert response.status_code == 200
    assert response.json() == {
        "trace_url": f"https://smith.example/o/tenant-id/projects/p/project-id/t/{THREAD_ID}"
    }
    resolve.assert_awaited_once_with(
        preference if preference and preference.strip() else "deployment-local"
    )


async def test_local_trace_url_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.get(URL)

    assert response.status_code == 401


@pytest.mark.usefixtures("authenticated")
async def test_local_trace_url_rejects_invalid_thread_id(client: httpx.AsyncClient) -> None:
    response = await client.get("/dashboard/api/me/local-trace-url/not-a-uuid")

    assert response.status_code == 422


@pytest.mark.parametrize("tenant_id", ["tenant-id", None])
@pytest.mark.usefixtures("authenticated")
async def test_local_trace_url_is_null_when_unavailable(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tenant_id: str | None
) -> None:
    monkeypatch.setattr(user_preferences, "get_value", AsyncMock(return_value=None))
    monkeypatch.setattr(langsmith, "resolve_tenant_id", AsyncMock(return_value=tenant_id))
    monkeypatch.setattr(langsmith, "_resolve_project_id_by_name", AsyncMock(return_value=None))

    response = await client.get(URL)

    assert response.status_code == 200
    assert response.json() == {"trace_url": None}
