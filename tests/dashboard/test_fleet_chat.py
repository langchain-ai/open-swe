import httpx
import pytest
from fastapi import FastAPI

from openswe.fleet_chat import routes

WORKSPACE = "018d50d4-3d52-7000-8000-000000000002"
THREAD = "018d50d4-3d52-7000-8000-000000000001"


@pytest.mark.parametrize("member", [True, False])
async def test_proxy_uses_personal_auth_and_upstream_authorization(monkeypatch, member):
    forwarded = []

    async def upstream(request):
        if request.url.path == "/workspaces":
            return httpx.Response(200, json=[{"id": WORKSPACE}] if member else [])
        forwarded.append(request)
        assert request.headers["authorization"] == "Bearer personal-token"
        assert request.headers["x-tenant-id"] == WORKSPACE
        return httpx.Response(403, text="private upstream details")

    async def client(request):
        return httpx.AsyncClient(
            base_url="https://api.smith.langchain.com",
            headers={"Authorization": "Bearer personal-token"},
            transport=httpx.MockTransport(upstream),
        )

    monkeypatch.setattr(routes, "_client", client)
    app = FastAPI()
    app.include_router(routes.router)
    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.ASGITransport(app=app)
    ) as http:
        response = await http.get(f"/fleet-chat/{WORKSPACE}/threads/{THREAD}")
    assert response.status_code == 403
    assert "private upstream" not in response.text
    assert len(forwarded) == int(member)


async def test_proxy_rejects_arbitrary_paths_before_obtaining_credentials(monkeypatch):
    async def client(request):
        pytest.fail("Unsupported paths must not obtain credentials")

    monkeypatch.setattr(routes, "_client", client)
    app = FastAPI()
    app.include_router(routes.router)
    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.ASGITransport(app=app)
    ) as http:
        response = await http.post(f"/fleet-chat/{WORKSPACE}/assistants/search", json={})
    assert response.status_code == 404


@pytest.mark.parametrize("operation", ["search", f"{THREAD}/runs/stream"])
async def test_proxy_preserves_sdk_payload_and_existing_fleet_threads(monkeypatch, operation):
    payload = {"metadata": {"assistant_id": "builtin-chat"}, "limit": 50}

    async def upstream(request):
        if request.url.path == "/workspaces":
            return httpx.Response(200, json=[{"id": WORKSPACE}])
        import json

        assert json.loads(request.content) == payload
        return httpx.Response(
            200,
            json=[{"thread_id": THREAD, "metadata": {"assistant_id": "builtin-chat"}}],
            headers={"Content-Location": f"/threads/{THREAD}/runs/{THREAD}"},
        )

    async def client(request):
        return httpx.AsyncClient(
            base_url="https://api.smith.langchain.com", transport=httpx.MockTransport(upstream)
        )

    monkeypatch.setattr(routes, "_client", client)
    app = FastAPI()
    app.include_router(routes.router)
    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.ASGITransport(app=app)
    ) as http:
        response = await http.post(f"/fleet-chat/{WORKSPACE}/threads/{operation}", json=payload)
    assert response.status_code == 200
    assert response.json()[0]["thread_id"] == THREAD
    assert response.headers["content-location"] == f"/threads/{THREAD}/runs/{THREAD}"
