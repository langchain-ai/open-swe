import httpx
from fastapi import FastAPI

from openswe.apps.models import AppSpec, SandboxApp
from openswe.dashboard import oauth, routes
from openswe.database import postgres
from openswe.users.models import User, UserIdentity


async def _person(login: str, external_id: str) -> User:
    user = User(identities=[UserIdentity(provider="github", external_id=external_id, login=login)])
    async with postgres.session() as session:
        session.add(user)
    return user


def _client_for(user: User) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[oauth.require_session] = lambda: {
        "sub": user.github_login,
        "user_id": str(user.id),
    }
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    )


def _spec(port: int) -> AppSpec:
    return AppSpec(
        name="todo", port=port, start_command="python3 server.py", workdir="/workspace/apps/todo"
    )


async def test_apps_stay_with_their_owner_and_resave_by_name(registry_db: None) -> None:
    alice = await _person("alice", "1")
    bob = await _person("bob", "2")
    first = await SandboxApp.save(
        alice.id, _spec(3000), thread_id="t-1", sandbox_id="box-1", url="https://one/"
    )
    resaved = await SandboxApp.save(
        alice.id, _spec(4000), thread_id="t-2", sandbox_id="box-2", url="https://two/"
    )

    assert resaved.id == first.id
    async with _client_for(alice) as client:
        [listed] = (await client.get("/dashboard/api/apps")).json()["items"]
    assert (listed["port"], listed["thread_id"], listed["url"]) == (4000, "t-2", "https://two/")

    async with _client_for(bob) as client:
        assert (await client.get("/dashboard/api/apps")).json()["items"] == []
        assert (await client.post(f"/dashboard/api/apps/{first.id}/launch")).status_code == 404
        assert (await client.delete(f"/dashboard/api/apps/{first.id}")).status_code == 404

    async with _client_for(alice) as client:
        assert (await client.delete(f"/dashboard/api/apps/{first.id}")).status_code == 204
        assert (await client.get("/dashboard/api/apps")).json()["items"] == []
