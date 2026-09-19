import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.mcp_server import auth, reviews, server
from agent.mcp_server.server import router
from agent.thread_ids import reviewer_thread_id

PR = "https://github.com/acme/widgets/pull/42"
THREAD = reviewer_thread_id("acme", "widgets", 42)
FULL_FINDING = {
    "id": "f1",
    "severity": "high",
    "confidence": "high",
    "category": "bug",
    "title": "Off by one",
    "file": "src/a.py",
    "start_line": 3,
    "end_line": 4,
    "side": "RIGHT",
    "in_diff": True,
    "description": "Loop skips the last item.",
    "suggestion": "use <=",
    "status": "open",
    "github_review_id": 99,
    "github_review_comment_ids": [1],
    "last_human_reply_body": None,
}


def _http_error(status: int) -> Exception:
    exc = RuntimeError(f"http {status}")
    exc.response = SimpleNamespace(status_code=status)
    return exc


class FakeLangGraph:
    """Minimal stand-in for the LangGraph SDK client."""

    def __init__(self, statuses=("success",), thread_kind: str | None = None, has_run=False):
        self.statuses = list(statuses)
        self.thread_kind = thread_kind
        self.has_run = has_run
        self.threads = SimpleNamespace(get=self._thread_get)
        self.runs = SimpleNamespace(get=self._run_get, list=self._run_list)

    async def _thread_get(self, thread_id):
        if self.thread_kind is None:
            raise _http_error(404)
        return {"thread_id": thread_id, "metadata": {"kind": self.thread_kind}}

    async def _run_get(self, thread_id, run_id):
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return {"run_id": run_id, "status": status}

    async def _run_list(self, thread_id, limit):
        if not self.has_run:
            raise _http_error(404)
        return [{"run_id": "run-1", "status": self.statuses[0]}]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("MCP_SERVER_ENABLED", "1")
    monkeypatch.setenv("MCP_TOKEN_SECRET", "s" * 40)
    monkeypatch.setenv("MCP_SKIP_USER_ACCESS_CHECK", "1")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://swe.example.com")
    monkeypatch.delenv("ALLOWED_GITHUB_ORGS", raising=False)
    monkeypatch.delenv("ALLOWED_GITHUB_REPOS", raising=False)
    monkeypatch.setattr(reviews, "POLL_SECONDS", 0)


@pytest.fixture
def client(env):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def fake(monkeypatch):
    """A fake LangGraph client plus a fake trigger that records its calls."""

    def install(**kwargs):
        lg = FakeLangGraph(**kwargs)
        calls: list[dict] = []

        async def fake_trigger(ref, caller):
            calls.append({"ref": ref, "caller": caller})
            lg.thread_kind, lg.has_run = "reviewer", True
            return {"success": True, "queued": False, "thread_id": THREAD, "pr_url": ref.url}

        async def fake_findings(thread_id):
            assert thread_id == THREAD
            return [FULL_FINDING]

        monkeypatch.setattr(reviews, "get_langgraph_client", lambda: lg)
        monkeypatch.setattr(reviews, "_trigger", fake_trigger)
        monkeypatch.setattr(reviews, "list_findings", fake_findings)
        lg.trigger_calls = calls
        return lg

    return install


def headers(login="dev-user"):
    return {"Authorization": f"Bearer {auth.mint_token(login)}"}


def rpc(client, method, params=None, id_=1, hdrs=None):
    body = {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}
    return client.post(
        "/integrations/mcp", json=body, headers=hdrs if hdrs is not None else headers()
    )


def call(client, name, arguments):
    r = rpc(client, "tools/call", {"name": name, "arguments": arguments})
    assert r.status_code == 200
    return r.json()["result"]


# --- auth / transport -------------------------------------------------------


def test_disabled_returns_404(client, monkeypatch):
    monkeypatch.delenv("MCP_SERVER_ENABLED")
    assert rpc(client, "ping").status_code == 404


def test_requires_bearer_token(client):
    r = rpc(client, "ping", hdrs={})
    assert r.status_code == 401 and "Bearer" in r.headers["www-authenticate"]


def test_rejects_tampered_and_expired_tokens(client):
    token = auth.mint_token("dev-user")
    assert rpc(client, "ping", hdrs={"Authorization": f"Bearer {token}x"}).status_code == 401
    expired = auth.mint_token("dev-user", ttl_seconds=-5)
    assert rpc(client, "ping", hdrs={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_mint_rejects_invalid_logins(env):
    for bad in ("", "a b", "-lead", "x" * 40, "../etc", "a@b.com"):
        with pytest.raises(ValueError):
            auth.mint_token(bad)


def test_rejects_foreign_origin(client):
    assert (
        rpc(client, "ping", hdrs={**headers(), "Origin": "https://evil.example"}).status_code == 403
    )


def test_initialize_negotiates_version(client):
    r = rpc(client, "initialize", {"protocolVersion": "2025-03-26"}).json()["result"]
    assert r["protocolVersion"] == "2025-03-26"
    r = rpc(client, "initialize", {"protocolVersion": "1999-01-01"}).json()["result"]
    assert r["protocolVersion"] == "2025-06-18"


def test_notification_gets_202(client):
    body = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert client.post("/integrations/mcp", json=body, headers=headers()).status_code == 202


def test_get_is_405(client):
    assert client.get("/integrations/mcp", headers=headers()).status_code == 405


def test_tools_list_and_unknown_method(client):
    names = [t["name"] for t in rpc(client, "tools/list").json()["result"]["tools"]]
    assert names == ["request_review", "get_review"]
    assert rpc(client, "nope").json()["error"]["code"] == -32601


# --- request_review ---------------------------------------------------------


def test_sync_review_returns_findings(client, fake):
    lg = fake(statuses=["running", "success"])
    result = call(client, "request_review", {"pr_url": PR})
    data = result["structuredContent"]
    assert not result["isError"]
    assert data["status"] == "completed"
    assert data["thread_id"] == THREAD
    assert data["web_url"] == "https://swe.example.com/agents/reviews/acme/widgets/42"
    # Only the public fields survive; GitHub bookkeeping is dropped.
    assert set(data["findings"][0]) == set(reviews._FINDING_FIELDS)
    assert data["findings"][0]["file"] == "src/a.py"
    assert "github_review_id" not in data["findings"][0]
    # Dispatched once, as the requesting GitHub user, via the shared trigger.
    (trigger,) = lg.trigger_calls
    assert trigger["caller"].github_login == "dev-user"
    assert trigger["ref"].full_name == "acme/widgets" and trigger["ref"].number == 42


def test_async_mode_does_not_wait(client, fake):
    fake(statuses=["running"])
    data = call(client, "request_review", {"pr_url": PR, "wait": False})["structuredContent"]
    assert data["status"] == "running" and "findings" not in data


def test_attaches_to_active_run_instead_of_interrupting(client, fake):
    lg = fake(statuses=["running", "success"], thread_kind="reviewer", has_run=True)
    data = call(client, "request_review", {"pr_url": PR})["structuredContent"]
    assert lg.trigger_calls == []  # would have interrupted the running review
    assert data["status"] == "completed" and "already in progress" in data["note"]


def test_new_review_when_previous_run_finished(client, fake):
    lg = fake(statuses=["success"], thread_kind="reviewer", has_run=True)
    call(client, "request_review", {"pr_url": PR})
    assert len(lg.trigger_calls) == 1


def test_timeout_reports_running(client, fake, monkeypatch):
    fake(statuses=["running"])

    async def instant_timeout(client, thread_id, run_id, *, timeout):
        return "running"

    monkeypatch.setattr(reviews, "wait_for_run", instant_timeout)
    assert (
        call(client, "request_review", {"pr_url": PR})["structuredContent"]["status"] == "running"
    )


def test_failed_run(client, fake):
    fake(statuses=["error"])
    data = call(client, "request_review", {"pr_url": PR})["structuredContent"]
    assert data["status"] == "failed" and "error" in data and "findings" not in data


def test_trigger_failure_is_reported(client, fake, monkeypatch):
    fake()

    async def failing(ref, caller):
        return {"success": False, "error": "No GitHub App token available"}

    monkeypatch.setattr(reviews, "_trigger", failing)
    result = call(client, "request_review", {"pr_url": PR})
    assert result["isError"] and result["structuredContent"]["error"] == "dispatch_failed"


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
def test_invalid_pr_urls(client, fake, url):
    lg = fake()
    result = call(client, "request_review", {"pr_url": url})
    assert result["structuredContent"]["error"] == "invalid_pr_url"
    assert lg.trigger_calls == []


def test_pr_url_variants_parse():
    assert (
        reviews.parse_pr_url("https://github.com/acme/widgets/pull/42/files?diff=split").number
        == 42
    )


def test_repo_allowlist_blocks_before_dispatch(client, fake, monkeypatch):
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "other-org")
    lg = fake()
    assert (
        call(client, "request_review", {"pr_url": PR})["structuredContent"]["error"]
        == "repo_not_allowed"
    )
    assert lg.trigger_calls == []


def test_user_access_check_is_enforced(client, fake, monkeypatch):
    lg = fake()

    async def deny(caller, ref):
        raise reviews.ReviewError("forbidden", "no access")

    monkeypatch.setattr(reviews, "assert_user_access", deny)
    assert (
        call(client, "request_review", {"pr_url": PR})["structuredContent"]["error"] == "forbidden"
    )
    assert lg.trigger_calls == []


def test_fails_closed_without_user_access_check(client, fake, monkeypatch):
    monkeypatch.delenv("MCP_SKIP_USER_ACCESS_CHECK")
    lg = fake()
    for tool in ("request_review", "get_review"):
        result = call(client, tool, {"pr_url": PR})
        assert result["structuredContent"]["error"] == "forbidden"
    assert lg.trigger_calls == []


def test_bad_arguments(client, fake):
    fake()
    assert call(client, "request_review", {})["structuredContent"]["error"] == "invalid_arguments"
    assert call(client, "request_review", {"pr_url": PR, "wait": "yes"})["isError"]


# --- get_review -------------------------------------------------------------


def test_get_review_completed(client, fake):
    fake(statuses=["success"], thread_kind="reviewer", has_run=True)
    data = call(client, "get_review", {"pr_url": PR})["structuredContent"]
    assert data["status"] == "completed" and data["findings"][0]["id"] == "f1"


def test_get_review_running_has_no_findings(client, fake):
    fake(statuses=["running"], thread_kind="reviewer", has_run=True)
    data = call(client, "get_review", {"pr_url": PR})["structuredContent"]
    assert data["status"] == "running" and "findings" not in data


def test_get_review_unknown_pr(client, fake):
    fake()
    assert call(client, "get_review", {"pr_url": PR})["structuredContent"]["error"] == "not_found"


def test_get_review_ignores_non_reviewer_threads(client, fake):
    fake(thread_kind="agent", has_run=True)
    assert call(client, "get_review", {"pr_url": PR})["structuredContent"]["error"] == "not_found"


def test_get_review_validates_input(client, fake):
    fake()
    assert call(client, "get_review", {"pr_url": "https://evil.com/a/b/pull/1"})["isError"]
    assert call(client, "get_review", {})["structuredContent"]["error"] == "invalid_arguments"


# --- streaming --------------------------------------------------------------


def test_sse_stream_emits_progress_then_result(client, fake, monkeypatch):
    monkeypatch.setattr(server, "KEEPALIVE_SECONDS", 0.05)
    monkeypatch.setattr(reviews, "POLL_SECONDS", 0.06)
    fake(statuses=["running", "running", "running", "success"])
    body = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "request_review",
            "arguments": {"pr_url": PR},
            "_meta": {"progressToken": "t1"},
        },
    }
    r = client.post(
        "/integrations/mcp",
        json=body,
        headers={**headers(), "Accept": "application/json, text/event-stream"},
    )
    assert r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    assert any(
        e.get("method") == "notifications/progress" and e["params"]["progressToken"] == "t1"
        for e in events
    )
    assert events[-1]["id"] == 7
    assert events[-1]["result"]["structuredContent"]["status"] == "completed"
