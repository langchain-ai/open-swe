from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.mcp_server import auth, reviews
from agent.mcp_server.server import router

PR = "https://github.com/acme/widgets/pull/42"


class FakeLangGraph:
    def __init__(self, statuses, findings=None, conflict=False):
        self.statuses = list(statuses)
        self.findings = findings if findings is not None else []
        self.conflict = conflict
        self.threads_meta: dict[str, dict] = {}
        self.threads = SimpleNamespace(
            create=self._threads_create, get=self._threads_get, get_state=self._get_state
        )
        self.runs = SimpleNamespace(create=self._runs_create, get=self._runs_get, list=self._runs_list)

    async def _threads_create(self, thread_id, if_exists, metadata):
        self.threads_meta.setdefault(thread_id, metadata)

    async def _threads_get(self, thread_id):
        if thread_id not in self.threads_meta:
            raise LookupError("missing")
        return {"thread_id": thread_id, "metadata": self.threads_meta[thread_id]}

    async def _runs_create(self, thread_id, graph, **kwargs):
        if self.conflict:
            exc = RuntimeError("conflict")
            exc.response = SimpleNamespace(status_code=409)
            raise exc
        assert graph == "reviewer"
        assert kwargs["multitask_strategy"] == "reject"
        self.created = kwargs
        return {"run_id": "run-1"}

    async def _runs_get(self, thread_id, run_id):
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return {"run_id": run_id, "status": status}

    async def _runs_list(self, thread_id, limit):
        return [{"run_id": "run-1", "status": self.statuses[0]}]

    async def _get_state(self, thread_id):
        return {"values": {"findings": self.findings}}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("MCP_SERVER_ENABLED", "1")
    monkeypatch.setenv("MCP_TOKEN_SECRET", "s" * 40)
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://swe.example.com")
    monkeypatch.delenv("ALLOWED_GITHUB_ORGS", raising=False)
    monkeypatch.delenv("ALLOWED_GITHUB_REPOS", raising=False)
    monkeypatch.setattr(reviews, "POLL_SECONDS", 0)


@pytest.fixture
def client(env):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def headers(email="dev@acme.com"):
    return {"Authorization": f"Bearer {auth.mint_token(email)}"}


def rpc(client, method, params=None, id_=1, hdrs=None):
    body = {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}
    return client.post("/integrations/mcp", json=body, headers=hdrs if hdrs is not None else headers())


def call(client, name, arguments):
    r = rpc(client, "tools/call", {"name": name, "arguments": arguments})
    assert r.status_code == 200
    return r.json()["result"]


def use_fake(monkeypatch, fake):
    monkeypatch.setattr(reviews, "get_langgraph_client", lambda: fake)


# --- auth / transport -------------------------------------------------------


def test_disabled_returns_404(client, monkeypatch):
    monkeypatch.delenv("MCP_SERVER_ENABLED")
    assert rpc(client, "ping").status_code == 404


def test_requires_bearer_token(client):
    r = rpc(client, "ping", hdrs={})
    assert r.status_code == 401 and "Bearer" in r.headers["www-authenticate"]


def test_rejects_tampered_and_expired_tokens(client, monkeypatch):
    token = auth.mint_token("dev@acme.com")
    assert rpc(client, "ping", hdrs={"Authorization": f"Bearer {token}x"}).status_code == 401
    expired = auth.mint_token("dev@acme.com", ttl_seconds=-5)
    assert rpc(client, "ping", hdrs={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_rejects_foreign_origin(client):
    hdrs = {**headers(), "Origin": "https://evil.example"}
    assert rpc(client, "ping", hdrs=hdrs).status_code == 403


def test_initialize_negotiates_version(client):
    r = rpc(client, "initialize", {"protocolVersion": "2025-03-26"}).json()["result"]
    assert r["protocolVersion"] == "2025-03-26"
    r = rpc(client, "initialize", {"protocolVersion": "1999-01-01"}).json()["result"]
    assert r["protocolVersion"] == "2025-06-18"


def test_notification_gets_202(client):
    r = client.post("/integrations/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers())
    assert r.status_code == 202


def test_get_is_405(client):
    r = client.get("/integrations/mcp", headers=headers())
    assert r.status_code == 405


def test_tools_list_and_unknown_method(client):
    names = [t["name"] for t in rpc(client, "tools/list").json()["result"]["tools"]]
    assert names == ["request_review", "get_review"]
    assert rpc(client, "nope").json()["error"]["code"] == -32601


# --- request_review ---------------------------------------------------------


def test_sync_review_returns_findings(client, monkeypatch):
    fake = FakeLangGraph(["running", "success"], findings=[{"id": "f1", "path": "a.py", "line": 3, "severity": "high", "title": "Bug", "description": "x"}])
    use_fake(monkeypatch, fake)
    result = call(client, "request_review", {"pr_url": PR})
    data = result["structuredContent"]
    assert not result["isError"]
    assert data["status"] == "completed"
    assert data["findings"][0] == {"id": "f1", "path": "a.py", "line": 3, "severity": "high", "title": "Bug", "body": "x"}
    assert data["web_url"] == f"https://swe.example.com/agents/{data['thread_id']}"
    assert fake.created["config"]["configurable"]["user_email"] == "dev@acme.com"


def test_async_mode_does_not_wait(client, monkeypatch):
    use_fake(monkeypatch, FakeLangGraph(["running"]))
    data = call(client, "request_review", {"pr_url": PR, "wait": False})["structuredContent"]
    assert data["status"] == "running" and "findings" not in data


def test_timeout_reports_running(client, monkeypatch):
    use_fake(monkeypatch, FakeLangGraph(["running"]))
    monkeypatch.setattr(reviews, "wait_for_run", _instant_timeout)
    data = call(client, "request_review", {"pr_url": PR})["structuredContent"]
    assert data["status"] == "running"


async def _instant_timeout(client, thread_id, run_id, *, timeout):
    return "running"


def test_failed_run(client, monkeypatch):
    use_fake(monkeypatch, FakeLangGraph(["error"]))
    data = call(client, "request_review", {"pr_url": PR})["structuredContent"]
    assert data["status"] == "failed" and "error" in data


def test_joins_active_run_on_conflict(client, monkeypatch):
    use_fake(monkeypatch, FakeLangGraph(["success"], conflict=True))
    data = call(client, "request_review", {"pr_url": PR})["structuredContent"]
    assert data["status"] == "completed" and "already in progress" in data["note"]


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/acme/widgets/pull/1",
        "https://evil.com/acme/widgets/pull/1",
        "https://github.com/acme/widgets/issues/1",
        "https://github.com/acme/widgets/pull/abc",
        "https://github.com.evil.com/acme/widgets/pull/1",
        "not a url",
    ],
)
def test_invalid_pr_urls(client, monkeypatch, url):
    use_fake(monkeypatch, FakeLangGraph(["success"]))
    result = call(client, "request_review", {"pr_url": url})
    assert result["isError"] and result["structuredContent"]["error"] == "invalid_pr_url"


def test_pr_url_variants_parse():
    assert reviews.parse_pr_url("https://github.com/acme/widgets/pull/42/files?diff=split").number == 42


def test_repo_allowlist(client, monkeypatch):
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "other-org")
    use_fake(monkeypatch, FakeLangGraph(["success"]))
    result = call(client, "request_review", {"pr_url": PR})
    assert result["structuredContent"]["error"] == "repo_not_allowed"


def test_bad_arguments(client, monkeypatch):
    use_fake(monkeypatch, FakeLangGraph(["success"]))
    assert call(client, "request_review", {})["structuredContent"]["error"] == "invalid_arguments"
    assert call(client, "request_review", {"pr_url": PR, "wait": "yes"})["isError"]


# --- get_review -------------------------------------------------------------


def test_get_review_only_for_owner(client, monkeypatch):
    fake = FakeLangGraph(["success"], findings=[])
    use_fake(monkeypatch, fake)
    thread_id = call(client, "request_review", {"pr_url": PR, "wait": False})["structuredContent"]["thread_id"]

    mine = call(client, "get_review", {"thread_id": thread_id})
    assert mine["structuredContent"]["status"] == "completed"

    other = rpc(client, "tools/call", {"name": "get_review", "arguments": {"thread_id": thread_id}}, hdrs=headers("mallory@acme.com"))
    assert other.json()["result"]["structuredContent"]["error"] == "not_found"


def test_get_review_rejects_non_uuid(client, monkeypatch):
    use_fake(monkeypatch, FakeLangGraph(["success"]))
    assert call(client, "get_review", {"thread_id": "../../etc"})["structuredContent"]["error"] == "invalid_arguments"


# --- streaming --------------------------------------------------------------


def test_sse_stream_emits_progress_then_result(client, monkeypatch):
    import json
    from agent.mcp_server import server

    monkeypatch.setattr(server, "KEEPALIVE_SECONDS", 0.05)
    monkeypatch.setattr(reviews, "POLL_SECONDS", 0.06)
    use_fake(monkeypatch, FakeLangGraph(["running", "running", "running", "success"]))
    body = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "request_review", "arguments": {"pr_url": PR}, "_meta": {"progressToken": "t1"}}}
    r = client.post("/integrations/mcp", json=body, headers={**headers(), "Accept": "application/json, text/event-stream"})
    assert r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    assert any(e.get("method") == "notifications/progress" and e["params"]["progressToken"] == "t1" for e in events)
    assert events[-1]["id"] == 7 and events[-1]["result"]["structuredContent"]["status"] == "completed"
