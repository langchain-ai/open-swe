from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from langsmith.sandbox import RunConfig

from openswe.threads import environment


@pytest.mark.asyncio
async def test_environment_owner_only_and_write_only(monkeypatch):
    app = FastAPI()
    app.include_router(environment.router)
    app.dependency_overrides[environment.SESSION_DEP.dependency] = lambda: {"sub": "bob"}
    threads = SimpleNamespace(
        get=AsyncMock(return_value={"metadata": {"owner_login": "alice", "sandbox_id": "box"}})
    )
    monkeypatch.setattr(environment, "langgraph_client", lambda: SimpleNamespace(threads=threads))
    sandbox_client = SimpleNamespace(
        get_sandbox=AsyncMock(
            return_value=SimpleNamespace(
                run_config=RunConfig(user="dev", env_vars={"EXISTING": "keep"})
            )
        ),
        update_sandbox=AsyncMock(),
        aclose=AsyncMock(),
    )
    monkeypatch.setattr(environment, "get_async_sandbox_client", lambda: sandbox_client)
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")

    class Transaction:
        async def __aenter__(self):
            return SimpleNamespace(execute=AsyncMock())

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(environment, "engine", lambda: SimpleNamespace(begin=Transaction))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        payload = {
            "name": "SERVICE_KEY",
            "value": "private-value",
            "acknowledge_shared_access": True,
        }
        assert (
            await client.post("/threads/t/sandbox/environment", json=payload)
        ).status_code == 404
        sandbox_client.update_sandbox.assert_not_called()
        app.dependency_overrides[environment.SESSION_DEP.dependency] = lambda: {"sub": "alice"}
        for invalid in [
            {**payload, "name": "GH_TOKEN"},
            {**payload, "acknowledge_shared_access": False},
        ]:
            response = await client.post("/threads/t/sandbox/environment", json=invalid)
            assert response.status_code == 400
            assert "private-value" not in response.text
        response = await client.post(
            "/threads/t/sandbox/environment", json={**payload, "name": "bad name"}
        )
        assert response.status_code == 422
        assert "private-value" not in response.text
        response = await client.post("/threads/t/sandbox/environment", json=payload)
        assert response.status_code == 204
        assert not response.content
        config = sandbox_client.update_sandbox.call_args.kwargs["run_config"]
        assert config.env_vars == {"EXISTING": "keep", "SERVICE_KEY": "private-value"}
        assert config.user == "dev"
