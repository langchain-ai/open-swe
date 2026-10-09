import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel

from openswe.analytics import routes as analytics_routes
from openswe.analytics import segment
from openswe.api_keys.models import ApiKey
from openswe.audit_logs import middleware, store
from openswe.audit_logs.context import current_audit_log
from openswe.audit_logs.middleware import AuditLogMiddleware, audit_endpoint
from openswe.audit_logs.models import AuditLog, AuditLogEnrichments, AuditLogsCursor
from openswe.audit_logs.routes import router
from openswe.bridge import routes as bridge_routes
from openswe.bridge.store import BridgeStore
from openswe.database import postgres
from openswe.threads.principals import PrincipalDep
from openswe.web import deps, oauth, workspace_settings
from openswe.workspaces.store import WORKSPACES, WorkspaceCreate
from tests.conftest import FakeStore


class Settings(BaseModel):
    token: str


def app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(AuditLogMiddleware)
    application.include_router(router, prefix="/api")

    settings_router = APIRouter()

    @settings_router.put("/settings/{resource_id}", dependencies=[Depends(oauth.require_session)])
    @audit_endpoint
    async def save(resource_id: str, body: Settings) -> dict[str, bool]:
        await asyncio.sleep(0)
        if body.token == "fail":
            raise HTTPException(403, "forbidden")
        if body.token == "exception":
            raise RuntimeError("secret exception")
        return {"ok": True}

    @application.post("/api/machine")
    @audit_endpoint
    async def machine(principal: PrincipalDep) -> dict[str, bool]:
        return {"machine": principal.machine}

    @application.post("/api/untracked")
    async def untracked(principal: PrincipalDep) -> dict[str, bool]:
        return {"machine": principal.machine}

    application.include_router(settings_router, prefix="/api")
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
    monkeypatch.setenv("WEB_JWT_SECRET", "audit-test-secret-that-is-at-least-32-bytes")
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
                f"/prefix/api/settings/{resource_id}?token=query-secret",
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
        assert entry.enrichments.request_path == "/api/settings/{resource_id}"
        assert entry.enrichments.resource_ids == [str(resource_id)]
        assert "secret" not in entry.model_dump_json()
        assert "forged-identity" not in entry.model_dump_json()

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (
            await client.put(f"/prefix/api/settings/{resource_id}", json={"token": "x"})
        ).status_code == 401
    assert len(entries) == 3


@pytest.mark.parametrize("prefix", ["", "/prefix"])
async def test_telemetry_keeps_its_effects_without_audit_entries(
    monkeypatch: pytest.MonkeyPatch, prefix: str
) -> None:
    monkeypatch.setenv("WEB_JWT_SECRET", "audit-test-secret-that-is-at-least-32-bytes")
    entries: list[AuditLog] = []
    page_views: list[dict[str, object]] = []
    heartbeats: list[tuple[str, str]] = []

    async def append(entry: AuditLog) -> None:
        entries.append(entry)

    async def record_usage(**kwargs: object) -> None:
        page_views.append(kwargs)

    async def heartbeat(bridge_id: str, *, owner_id: str) -> bool:
        heartbeats.append((bridge_id, owner_id))
        return True

    monkeypatch.setattr(middleware, "append_safely", append)
    monkeypatch.setattr(segment, "record_usage", record_usage)
    monkeypatch.setattr(postgres, "configured", lambda: True)
    monkeypatch.setattr(BridgeStore, "heartbeat", heartbeat)
    application = app()
    application.include_router(analytics_routes.router, prefix="/api")
    application.include_router(bridge_routes.router, prefix="/api")
    root = FastAPI()
    root.mount(prefix or "/", application)
    bridge_id = str(uuid4())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=root),
        base_url="http://test",
        cookies=cookie("alice", str(uuid4())),
    ) as client:
        assert (
            await client.post(f"{prefix}/api/analytics/page", json={"page_name": "agents"})
        ).status_code == 204
        assert (await client.post(f"{prefix}/api/bridges/{bridge_id}/heartbeat")).status_code == 204
        assert (await client.post(f"{prefix}/api/untracked")).status_code == 200
        assert entries == []
        assert (
            await client.put(f"{prefix}/api/settings/item", json={"token": "x"})
        ).status_code == 200
    assert page_views == [
        {
            "login": "alice",
            "email": None,
            "event_type": "page",
            "name": "agents",
            "properties": {"page_name": "agents", "surface": "dashboard"},
        }
    ]
    assert heartbeats == [(bridge_id, "github:alice")]
    assert [entry.operation_name for entry in entries] == ["save"]


@pytest.mark.parametrize("workspace", [None, "preview"])
async def test_settings_audit_changes_are_redacted_and_track_resets(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore, workspace: str | None
) -> None:
    monkeypatch.setenv("WEB_JWT_SECRET", "audit-test-secret-that-is-at-least-32-bytes")
    monkeypatch.setattr(deps, "is_admin", lambda email, *, login: login == "admin")
    entries: list[AuditLog] = []

    async def append(entry: AuditLog) -> None:
        entries.append(entry)

    async def workspace_exists(slug: str) -> dict[str, str]:
        return {"slug": slug}

    monkeypatch.setattr(middleware, "append_safely", append)
    monkeypatch.setattr(WORKSPACES, "get", workspace_exists)
    application = app()
    application.include_router(workspace_settings.router, prefix="/api")
    namespace = ["team_settings"] if workspace is None else ["workspace_settings"]
    fake_store.seed(
        namespace,
        workspace or "default",
        {"model_routing_enabled": False, "org_guidelines": "old-secret", "unknown": "secret"},
    )
    path = "/api/settings" if workspace is None else f"/api/workspaces/{workspace}/settings"
    user_id = uuid4()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
        cookies=cookie("admin", str(user_id)),
    ) as client:
        for payload in (
            {"model_routing_enabled": True, "org_guidelines": "new-secret"},
            {"model_routing_enabled": True, "org_guidelines": "new-secret"},
            {},
        ):
            assert (await client.put(path, json=payload)).status_code == 200
    changed, noop, reset = entries
    assert changed.user_id == user_id
    assert changed.operation_succeeded is True
    assert changed.operation_name == (
        "put_instance_settings" if workspace is None else "put_workspace_settings"
    )
    assert changed.enrichments.settings_scope == ("instance" if workspace is None else "workspace")
    assert changed.enrichments.workspace == workspace
    assert changed.enrichments.settings_changes == {
        "model_routing_enabled": {"before": False, "after": True},
        "org_guidelines": {"before": "[REDACTED]", "after": "[REDACTED]"},
    }
    assert noop.enrichments.settings_changes == {}
    assert reset.enrichments.model_dump(mode="json", exclude_none=True)["settings_changes"] == {
        "model_routing_enabled": {"before": True, "after": None},
        "org_guidelines": {"before": "[REDACTED]", "after": None},
    }
    assert all("secret" not in entry.model_dump_json() for entry in entries)
    assert current_audit_log.get() is None


@pytest.mark.parametrize("failure", ["read", "write"])
async def test_settings_audit_does_not_invent_changes_on_store_failure(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore, failure: str
) -> None:
    monkeypatch.setenv("WEB_JWT_SECRET", "audit-test-secret-that-is-at-least-32-bytes")
    monkeypatch.setattr(deps, "is_admin", lambda email, *, login: True)
    entries: list[AuditLog] = []

    async def append(entry: AuditLog) -> None:
        entries.append(entry)

    async def fail(*args: object) -> None:
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(middleware, "append_safely", append)
    monkeypatch.setattr(
        workspace_settings, "_instance_record" if failure == "read" else "put_value", fail
    )
    application = app()
    application.include_router(workspace_settings.router, prefix="/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://test",
        cookies=cookie("admin", str(uuid4())),
    ) as client:
        response = await client.put("/api/settings", json={"model_routing_enabled": True})
    assert response.status_code == (200 if failure == "read" else 500)
    (entry,) = entries
    assert entry.operation_succeeded is (failure == "read")
    assert entry.enrichments.settings_changes is None
    assert current_audit_log.get() is None
    if failure == "read":
        assert fake_store.values(["team_settings"])["default"]["model_routing_enabled"] is True
    else:
        assert not fake_store.items


async def test_audit_failure_preserves_response(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("WEB_JWT_SECRET", "audit-test-secret-that-is-at-least-32-bytes")
    monkeypatch.setattr(store.postgres, "configured", lambda: True)

    async def fail(entry: AuditLog) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(store, "append", fail)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app()),
        base_url="http://test",
        cookies=cookie("alice", str(uuid4())),
    ) as client:
        response = await client.put("/api/settings/item", json={"token": "x"})
    assert response.status_code == 200
    assert "Could not persist audit log" in caplog.text


async def test_key_actor_wins_over_cookie_and_survives_workspace_deletion(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEB_JWT_SECRET", "audit-test-secret-that-is-at-least-32-bytes")
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
        response = await client.post("/api/machine", headers={"Authorization": f"Bearer {secret}"})
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
    monkeypatch.setenv("WEB_JWT_SECRET", "audit-test-secret-that-is-at-least-32-bytes")
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
        assert (await client.get("/api/audit-logs", params=params)).status_code == 401
        client.cookies.update(cookie("ordinary", str(uuid4())))
        assert (await client.get("/api/audit-logs", params=params)).status_code == 403
        client.cookies.update(cookie("admin", str(uuid4())))
        first = await client.get("/api/audit-logs", params=params)
        assert first.status_code == 200
        data = first.json()
        assert len(data["items"]) == 2
        after = AuditLogsCursor.model_validate_json(data["cursor"])
        assert after.request_time == now
        second = await client.get("/api/audit-logs", params={**params, "cursor": data["cursor"]})
        assert second.status_code == 200
        last = second.json()
        assert last["cursor"] is None
        assert [item["id"] for item in data["items"] + last["items"]] == [
            str(e.id) for e in sorted(entries, key=lambda e: e.id, reverse=True)
        ]
        assert (
            await client.get("/api/audit-logs", params={**params, "cursor": "invalid"})
        ).status_code == 400
