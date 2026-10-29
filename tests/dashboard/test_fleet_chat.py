import json

import httpx
import pytest
from fastapi import FastAPI

from openswe.dashboard.oauth import require_session
from openswe.fleet_chat import routes
from openswe.users import UserPreferences

THREAD = "018d50d4-3d52-7000-8000-000000000001"


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_session] = lambda: {"sub": "alice"}
    return app


def _http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="http://test", transport=httpx.ASGITransport(app=app))


async def test_requires_the_fleet_experiment_before_reading_the_token(monkeypatch):
    async def preferences(login):
        return UserPreferences()

    async def token(login):
        pytest.fail("People without the Fleet experiment must not use their LangSmith token")

    monkeypatch.setattr(routes.User, "preferences_for_login", preferences)
    monkeypatch.setattr(routes, "langsmith_access_token", token)
    async with _http(_app()) as http:
        response = await http.post("/fleet-chat/threads/search", json={})
    assert response.status_code == 403
    assert "Experiments" in response.json()["detail"]


async def test_unsupported_paths_never_obtain_credentials(monkeypatch):
    async def client(login):
        pytest.fail("Unsupported paths must not obtain credentials")

    monkeypatch.setattr(routes, "_client", client)
    async with _http(_app()) as http:
        assistants = await http.post("/fleet-chat/assistants/search", json={})
        state = await http.patch(f"/fleet-chat/threads/{THREAD}/state", json={})
    assert assistants.status_code == 404
    assert state.status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "threads/search"),
        ("PATCH", f"threads/{THREAD}"),
        ("POST", f"threads/{THREAD}/runs/stream"),
    ],
)
async def test_forwards_as_caller(monkeypatch, method, path):
    payload = {"metadata": {"is_pinned": True}}
    forwarded: list[httpx.Request] = []

    async def upstream(request):
        forwarded.append(request)
        return httpx.Response(
            200,
            json={"thread_id": THREAD},
            headers={"Content-Location": f"/threads/{THREAD}/runs/{THREAD}"},
        )

    async def client(login):
        assert login == "alice"
        return httpx.AsyncClient(
            base_url="https://api.smith.langchain.com",
            headers={"Authorization": "Bearer personal-token"},
            transport=httpx.MockTransport(upstream),
        )

    monkeypatch.setattr(routes, "_client", client)
    async with _http(_app()) as http:
        response = await http.request(method, f"/fleet-chat/{path}", json=payload)
    assert response.status_code == 200
    assert response.headers["content-location"] == f"/threads/{THREAD}/runs/{THREAD}"
    [request] = forwarded
    assert request.url.path == f"/v1/fleet/lg/{path}"
    assert request.headers["authorization"] == "Bearer personal-token"
    assert "x-tenant-id" not in request.headers
    assert json.loads(request.content) == payload


async def test_upstream_errors_are_not_echoed(monkeypatch):
    async def client(login):
        return httpx.AsyncClient(
            base_url="https://api.smith.langchain.com",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(403, text="private upstream details")
            ),
        )

    monkeypatch.setattr(routes, "_client", client)
    async with _http(_app()) as http:
        response = await http.get(f"/fleet-chat/threads/{THREAD}")
    assert response.status_code == 403
    assert "private upstream" not in response.text
