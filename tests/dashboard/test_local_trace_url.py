from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from openswe.dashboard import user_preferences
from openswe.dashboard.oauth import require_session
from openswe.dashboard.routes import router
from openswe.utils import langsmith
from tests.conftest import FakeUserRecords

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
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    user_records: FakeUserRecords,
    preference: str | None,
) -> None:
    kind = user_preferences.USER_PREFERENCES.kind
    user_records.seed(kind, "alice", {"local_tracing_project": preference})
    user_records.seed(kind, "bob", {"local_tracing_project": "other-user"})
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
