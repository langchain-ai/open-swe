"""Incidents's dashboard authorization and generic-thread isolation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid7

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agent import completion, incidents
from agent.dashboard import oauth, routes
from agent.threads import handlers, listing, proxy
from tests.conftest import patch_thread_module


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://testserver")
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "incidents-test-signing-secret-32-bytes")
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
        f"/dashboard/api/incidents{path}",
        json=body,
        headers={"Origin": "http://testserver"},
    )

    assert response.status_code == 401


@pytest.mark.parametrize("path", ["/records", "/records/i1"])
def test_any_signed_in_user_can_read_incidents(client: TestClient, service, path: str) -> None:
    _login(client, login="developer", email="developer@example.com")

    response = client.get(f"/dashboard/api/incidents{path}")

    assert response.status_code == 200


@pytest.mark.parametrize("method", ["GET", "PATCH"])
def test_non_admins_cannot_change_settings(client, method) -> None:
    _login(client)

    response = client.request(
        method,
        "/dashboard/api/incidents/settings",
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
        f"/dashboard/api/incidents{path}",
        json=body,
        headers={"Origin": "https://other.example"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF check failed"}


@pytest.mark.parametrize(
    "body",
    [
        {"request_id": "r1", "action": "ask"},
        {"request_id": "r1", "action": "ask", "text": "   "},
        {"request_id": "r1", "action": "ask", "text": "x" * 8001},
        {"request_id": "", "action": "pause"},
        {"request_id": "   ", "action": "pause"},
        {"request_id": "r1", "action": "fix"},
        {"request_id": "r1", "action": "pause", "actor": {"github_login": "admin"}},
        {"request_id": "r1", "action": "pause", "config": {"model": "custom"}},
    ],
)
def test_commands_reject_invalid_input_and_client_supplied_identity(client, body) -> None:
    _login(client)

    response = client.post(
        "/dashboard/api/incidents/records/i1/commands",
        json=body,
        headers={"Origin": "http://testserver"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "view=unknown"])
def test_list_rejects_invalid_filters(client, query) -> None:
    _login(client)

    response = client.get(f"/dashboard/api/incidents/records?{query}")

    assert response.status_code == 422


@pytest.mark.parametrize("view", ["active", "inactive", "all"])
def test_authorized_list_and_detail_use_incidents_projections(client, service, view) -> None:
    _login(client)

    listed = client.get(
        "/dashboard/api/incidents/records",
        params={"view": view, "q": "payments", "limit": 30, "cursor": "c1"},
    )
    detail = client.get("/dashboard/api/incidents/records/i1")

    assert listed.status_code == 200
    assert listed.json() == {"items": [], "next_cursor": None}
    assert detail.status_code == 200
    assert detail.json() == {"incident": {"id": "i1"}, "report": None}
    service.list_incidents.assert_awaited_once_with(
        view=view, q="payments", limit=30, cursor="c1", include_setup=False
    )
    service.get_incident.assert_awaited_once_with("i1", include_setup=False)


def test_question_acceptance_uses_only_server_session_identity(client, service) -> None:
    _login(client)

    response = client.post(
        "/dashboard/api/incidents/records/i1/commands",
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


def test_settings_update_preserves_version_and_admin_identity(client, service) -> None:
    _login(client, login="admin", email="admin@example.com")

    settings = client.get("/dashboard/api/incidents/settings")
    changed = client.patch(
        "/dashboard/api/incidents/settings",
        json={"expected_version": 3, "policy": {"enabled": True, "channel_prefix": "inc-"}},
        headers={"Origin": "http://testserver"},
    )

    assert settings.status_code == 200
    assert settings.json() == {"policy": {"enabled": False}}
    assert changed.status_code == 202
    assert changed.json() == {"command_id": "settings1", "status": "accepted"}
    service.update_settings.assert_awaited_once_with(
        policy={"enabled": True, "channel_prefix": "inc-"},
        expected_version=3,
        actor={
            "id": "github:admin",
            "platform": "github",
            "github_login": "admin",
            "email": "admin@example.com",
        },
    )


@pytest.mark.parametrize("status", [404, 409, 429, 503])
def test_service_errors_remain_actionable_http_errors(client, service, status) -> None:
    _login(client)
    service.submit_command.side_effect = HTTPException(
        status, "Unavailable", headers={"Retry-After": "60"}
    )

    response = client.post(
        "/dashboard/api/incidents/records/i1/commands",
        json={"request_id": "r1", "action": "pause"},
        headers={"Origin": "http://testserver"},
    )

    assert response.status_code == status
    assert response.json() == {"detail": "Unavailable"}
    assert response.headers["Retry-After"] == "60"


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
        f"/dashboard/api/threads/i1{path}",
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
        await proxy.proxy_dashboard_thread_stream_events("i1", "admin", b"{}")

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

    page = await listing.list_dashboard_threads_page("admin", include_all=include_all)
    repos = await listing.list_dashboard_thread_repos("admin", include_all=include_all)

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
        await handlers.admin_cancel_dashboard_thread("i1")

    assert exc.value.status_code == 404
    threads.update.assert_not_awaited()


@pytest.mark.parametrize("status", ["success", "error"])
async def test_incidents_completion_is_delegated_without_generic_slack_output(
    monkeypatch, status
) -> None:
    from agent.incidents import turns

    metadata = {
        "source": "incidents_agent",
        "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "0"}},
    }
    threads = SimpleNamespace(
        get=AsyncMock(return_value={"thread_id": "i1", "metadata": metadata}),
        update=AsyncMock(),
    )
    monkeypatch.setattr(completion, "langgraph_client", lambda: SimpleNamespace(threads=threads))
    post = AsyncMock(return_value=True)
    costs = AsyncMock(return_value=True)
    settle = AsyncMock(return_value={"status": "ok", "reason": "incident turn complete"})
    monkeypatch.setattr(completion, "post_slack_thread_reply", post)
    monkeypatch.setattr(completion, "schedule_session_cost_refresh", costs)
    monkeypatch.setattr(turns, "handle_run_completion", settle)

    result = await completion.handle_run_completion(
        {
            "thread_id": "i1",
            "run_id": "r1",
            "status": status,
            "metadata": {"prepare_run_id": "prepare-1"},
        }
    )

    assert result == {"status": "ok", "reason": "incident turn complete"}
    settle.assert_awaited_once_with("i1", "r1", status)
    post.assert_not_awaited()
    costs.assert_not_awaited()
    threads.update.assert_not_awaited()
