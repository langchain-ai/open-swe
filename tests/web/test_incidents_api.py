"""Incidents's web authorization and generic-thread isolation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid7

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from openswe import incidents
from openswe.threads import handlers, listing, proxy
from openswe.web import oauth, routes
from tests.conftest import patch_thread_module


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("WEB_BASE_URL", "http://testserver")
    monkeypatch.setenv("WEB_JWT_SECRET", "incidents-test-signing-secret-32-bytes")
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def _login(client: TestClient, *, login: str = "sre", email: str = "sre@example.com") -> None:
    client.cookies.set(
        oauth.COOKIE_NAME,
        oauth.issue_session(login=login, email=email, avatar_url=None, user_id=str(uuid7())),
    )


@pytest.fixture
def service(monkeypatch):
    service = SimpleNamespace(
        list_incidents=AsyncMock(return_value={"items": [], "next_cursor": None}),
        get_incident=AsyncMock(return_value={"incident": {"id": "i1"}, "report": None}),
        get_settings=AsyncMock(return_value={"policy": {"enabled": False}}),
        update_settings=AsyncMock(return_value={"command_id": "settings1", "status": "accepted"}),
        submit_command=AsyncMock(return_value={"command_id": "command1", "status": "accepted"}),
    )
    monkeypatch.setattr(incidents, "service", service, raising=False)
    return service


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/records", None),
        ("GET", "/records/i1", None),
        ("GET", "/settings", None),
        ("PATCH", "/settings", {"expected_version": 0, "policy": {}}),
        ("POST", "/records/i1/commands", {"request_id": "r1", "action": "pause"}),
    ],
)
def test_all_incidents_routes_require_a_session(client, method, path, body) -> None:
    response = client.request(
        method,
        f"/api/incidents{path}",
        json=body,
        headers={"Origin": "http://testserver"},
    )

    assert response.status_code == 401


@pytest.mark.parametrize("method", ["GET", "PATCH"])
def test_non_admins_cannot_change_settings(client, method) -> None:
    _login(client)

    response = client.request(
        method,
        "/api/incidents/settings",
        json={"expected_version": 0, "policy": {}} if method == "PATCH" else None,
        headers={"Origin": "http://testserver"},
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/settings", {"expected_version": 0, "policy": {}}),
        ("/records/i1/commands", {"request_id": "r1", "action": "pause"}),
    ],
)
def test_incidents_mutations_keep_parent_router_csrf(client, path, body) -> None:
    _login(client, login="admin")

    response = client.request(
        "PATCH" if path == "/settings" else "POST",
        f"/api/incidents{path}",
        json=body,
        headers={"Origin": "https://other.example"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF check failed"}


def test_question_acceptance_uses_only_server_session_identity(client, service) -> None:
    _login(client)

    response = client.post(
        "/api/incidents/records/i1/commands",
        json={"request_id": "r1", "action": "ask", "text": "What changed?"},
        headers={"Origin": "http://testserver"},
    )

    assert response.status_code == 202
    assert response.json() == {"command_id": "command1", "status": "accepted"}
    service.submit_command.assert_awaited_once_with(
        "i1",
        action="ask",
        text="What changed?",
        request_id="r1",
        actor={
            "id": "github:sre",
            "platform": "github",
            "github_login": "sre",
            "email": "sre@example.com",
        },
    )


@pytest.mark.parametrize("source", ["incidents_agent"])
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "", None),
        ("GET", "/state", None),
        ("POST", "/history", {}),
        ("POST", "/stream/events", {}),
        ("POST", "/commands", {"method": "run.start", "params": {}}),
        ("POST", "/commands", {"method": "input.respond", "params": {}}),
        ("POST", "/messages", {"content": "hello"}),
        ("POST", "/resolve", {"resolved": True}),
        ("POST", "/cancel", None),
        ("POST", "/runs/r1/cancel", None),
        ("DELETE", "", None),
        ("POST", "/pin", None),
        ("POST", "/terminal/connect", None),
        ("GET", "/recovery.patch", None),
        ("GET", "/working-tree-diff", None),
        ("GET", "/branch-diff", None),
    ],
)
def test_generic_thread_routes_reject_incidents_even_for_admin_owners(
    client, monkeypatch, source, method, path, body
) -> None:
    _login(client, login="admin")
    threads = SimpleNamespace(
        get=AsyncMock(
            return_value={
                "thread_id": "i1",
                "metadata": {"source": source, "github_login": "admin"},
            }
        ),
        update=AsyncMock(),
    )
    patch_thread_module(monkeypatch, "langgraph_client", lambda: SimpleNamespace(threads=threads))

    response = client.request(
        method,
        f"/api/threads/i1{path}",
        json=body,
        headers={"Origin": "http://testserver"},
    )

    assert response.status_code == 404
    threads.update.assert_not_awaited()


@pytest.mark.parametrize("source", ["incidents_agent"])
async def test_thread_stream_preflight_rejects_incidents_before_opening_stream(
    monkeypatch, source
) -> None:
    threads = SimpleNamespace(
        get=AsyncMock(return_value={"thread_id": "i1", "metadata": {"source": source}}),
        join_stream=AsyncMock(side_effect=AssertionError("stream must not be opened")),
    )
    patch_thread_module(monkeypatch, "langgraph_client", lambda: SimpleNamespace(threads=threads))

    with pytest.raises(HTTPException) as exc:
        await proxy.proxy_web_thread_stream_events("i1", "admin", b"{}")

    assert exc.value.status_code == 404
    threads.join_stream.assert_not_awaited()


@pytest.mark.parametrize("source", ["incidents_agent"])
@pytest.mark.parametrize("include_all", [False, True])
async def test_generic_thread_lists_exclude_incidents_for_owners_and_admins(
    monkeypatch, source, include_all
) -> None:
    thread = {
        "thread_id": "i1",
        "status": "idle",
        "metadata": {
            "source": source,
            "github_login": "admin",
            "title": "Restricted incident finding",
            "latest_run_status": "success",
            "repo_owner": "secret",
            "repo_name": "service",
        },
    }
    client = SimpleNamespace(threads=SimpleNamespace(search=AsyncMock(return_value=[thread])))
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)

    page = await listing.list_web_threads_page("admin", include_all=include_all)
    repos = await listing.list_web_thread_repos("admin", include_all=include_all)

    assert page["items"] == []
    assert repos == []


@pytest.mark.parametrize("source", ["incidents_agent"])
async def test_admin_cancel_cannot_mutate_an_incidents_thread(monkeypatch, source) -> None:
    threads = SimpleNamespace(
        get=AsyncMock(return_value={"thread_id": "i1", "metadata": {"source": source}}),
        update=AsyncMock(),
    )
    client = SimpleNamespace(threads=threads, runs=SimpleNamespace(list=AsyncMock(return_value=[])))
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)

    with pytest.raises(HTTPException) as exc:
        await handlers.admin_cancel_web_thread("i1")

    assert exc.value.status_code == 404
    threads.update.assert_not_awaited()
