import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from agent.api_keys.models import ApiKey
from agent.audit_logs import middleware, store
from agent.audit_logs.middleware import AuditLogMiddleware
from agent.audit_logs.models import AuditLog, AuditLogEnrichments, AuditLogsCursor
from agent.audit_logs.routes import router
from agent.dashboard import deps, oauth
from agent.threads.principals import PrincipalDep
from agent.workspaces.store import WORKSPACES, WorkspaceCreate


class Settings(BaseModel):
    token: str


def app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(AuditLogMiddleware)
    application.include_router(router, prefix="/dashboard/api")

    @application.put(
        "/dashboard/api/settings/{resource_id}", dependencies=[Depends(oauth.require_session)]
    )
    async def save(resource_id: str, body: Settings) -> dict[str, bool]:
        await asyncio.sleep(0)
        if body.token == "fail":
            raise HTTPException(403, "forbidden")
        if body.token == "exception":
            raise RuntimeError("secret exception")
        return {"ok": True}

    @application.post("/dashboard/api/machine")
    async def machine(principal: PrincipalDep) -> dict[str, bool]:
        return {"machine": principal.machine}

    return application


def cookie(login: str, user_id: str) -> dict[str, str]:
    return {
        oauth.COOKIE_NAME: oauth.issue_session(
            login=login, email=None, avatar_url=None, user_id=user_id
        )
    }


async def test_authenticated_activity_is_isolated_and_secret_free_when_mounted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "audit-test-secret-that-is-at-least-32-bytes")
    entries: list[AuditLog] = []

    async def append(entry: AuditLog) -> None:
        entries.append(entry)

    monkeypatch.setattr(middleware, "append_safely", append)
    root = FastAPI()
    root.mount("/prefix", app())
    transport = httpx.ASGITransport(app=root, raise_app_exceptions=False)
    ids = [uuid4(), uuid4(), uuid4()]
    resource_id = uuid4()

    async def mutate(index: int, token: str) -> int:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies=cookie(f"user-{index}", str(ids[index])),
        ) as client:
            response = await client.put(
                f"/prefix/dashboard/api/settings/{resource_id}?token=query-secret",
                json={"token": token},
                headers={"X-User-ID": "forged-identity"},
            )
            return response.status_code

    assert await asyncio.gather(
        mutate(0, "payload-secret"), mutate(1, "fail"), mutate(2, "exception")
    ) == [200, 403, 500]
    by_user = {entry.user_id: entry for entry in entries}
    assert set(by_user) == set(ids)
    assert [by_user[user_id].operation_succeeded for user_id in ids] == [True, False, False]
    for index, user_id in enumerate(ids):
        entry = by_user[user_id]
        assert entry.enrichments.actor_login == f"user-{index}"
        assert entry.enrichments.request_path == "/dashboard/api/settings/{resource_id}"
        assert entry.enrichments.resource_ids == [str(resource_id)]
        assert "secret" not in entry.model_dump_json()
        assert "forged-identity" not in entry.model_dump_json()

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (
            await client.put(f"/prefix/dashboard/api/settings/{resource_id}", json={"token": "x"})
        ).status_code == 401
    assert len(entries) == 3


async def test_audit_failure_preserves_response(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "audit-test-secret-that-is-at-least-32-bytes")
    monkeypatch.setattr(store.postgres, "configured", lambda: True)

    async def fail(entry: AuditLog) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(store, "append", fail)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app()),
        base_url="http://test",
        cookies=cookie("alice", str(uuid4())),
    ) as client:
        response = await client.put("/dashboard/api/settings/item", json={"token": "x"})
    assert response.status_code == 200
    assert "Could not persist audit log" in caplog.text


async def test_key_actor_wins_over_cookie_and_survives_workspace_deletion(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "audit-test-secret-that-is-at-least-32-bytes")
    await WORKSPACES.create(WorkspaceCreate(name="Audit scope"), "admin")
    workspace_id = await WORKSPACES.id_for_slug("audit-scope")
    assert workspace_id is not None
    key, secret = await ApiKey.create(
        workspace_id=workspace_id,
        workspace="audit-scope",
        name="automation",
        expires_at=datetime.now(UTC) + timedelta(days=1),
        created_by="admin",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app()),
        base_url="http://test",
        cookies=cookie("alice", str(uuid4())),
    ) as client:
        response = await client.post(
            "/dashboard/api/machine", headers={"Authorization": f"Bearer {secret}"}
        )
    assert response.status_code == 200
    await WORKSPACES.delete("audit-scope")
    now = datetime.now(UTC)
    page = await store.list_logs(
        start_time=now - timedelta(minutes=1), end_time=now, limit=10, workspace_id=workspace_id
    )
    (entry,) = page.items
    assert entry.api_key_id == key.id
    assert entry.user_id is None
    assert entry.enrichments.actor_kind == "api_key"
    assert secret not in entry.model_dump_json()


async def test_admin_query_paginates_equal_timestamps_and_filters(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "audit-test-secret-that-is-at-least-32-bytes")
    monkeypatch.setattr(deps, "is_admin", lambda email, *, login: login == "admin")
    now = datetime.now(UTC)
    workspace_id, user_id = uuid4(), uuid4()
    entries = [
        AuditLog(
            request_time=now,
            operation_name="update_workspace",
            workspace_id=workspace_id,
            user_id=user_id,
            enrichments=AuditLogEnrichments(actor_kind="person"),
        )
        for _ in range(3)
    ]
    for entry in entries:
        await store.append(entry)
    await store.append(entries[0])
    await store.append(AuditLog(operation_name="other", request_time=now, workspace_id=uuid4()))
    params = {
        "start_time": now.isoformat(),
        "end_time": now.isoformat(),
        "workspace_id": str(workspace_id),
        "user_id": str(user_id),
        "operation_name": "update_workspace",
        "limit": "2",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app()), base_url="http://test"
    ) as client:
        assert (await client.get("/dashboard/api/audit-logs", params=params)).status_code == 401
        client.cookies.update(cookie("ordinary", str(uuid4())))
        assert (await client.get("/dashboard/api/audit-logs", params=params)).status_code == 403
        client.cookies.update(cookie("admin", str(uuid4())))
        first = await client.get("/dashboard/api/audit-logs", params=params)
        assert first.status_code == 200
        data = first.json()
        assert len(data["items"]) == 2
        after = AuditLogsCursor.model_validate_json(data["cursor"])
        assert after.request_time == now
        second = await client.get(
            "/dashboard/api/audit-logs", params={**params, "cursor": data["cursor"]}
        )
        assert second.status_code == 200
        last = second.json()
        assert last["cursor"] is None
        assert [item["id"] for item in data["items"] + last["items"]] == [
            str(e.id) for e in sorted(entries, key=lambda e: e.id, reverse=True)
        ]
        assert (
            await client.get("/dashboard/api/audit-logs", params={**params, "cursor": "invalid"})
        ).status_code == 400
