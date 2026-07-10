from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

import agent.tools.open_pull_request  # noqa: F401

opr = sys.modules["agent.tools.open_pull_request"]


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    def __init__(self, *, post: _FakeResponse, get: _FakeResponse | None = None) -> None:
        self._post = post
        self._get = get
        self.post_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def post(
        self, url: str, *, headers: dict[str, str], json: dict[str, Any]
    ) -> _FakeResponse:
        self.post_calls.append({"url": url, "headers": headers, "json": json})
        return self._post

    async def get(
        self, url: str, *, headers: dict[str, str], params: dict[str, str] | None = None
    ) -> _FakeResponse:
        self.get_calls.append({"url": url, "headers": headers, "params": params})
        if self._get is not None:
            return self._get
        return _FakeResponse(200, {"name": "ok"})


class _RoutingClient:
    """Fake httpx client that routes GETs by URL substring."""

    def __init__(self, *, post: _FakeResponse, get_routes: dict[str, _FakeResponse]) -> None:
        self._post = post
        self._get_routes = get_routes
        self.post_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> _RoutingClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def post(
        self, url: str, *, headers: dict[str, str], json: dict[str, Any]
    ) -> _FakeResponse:
        self.post_calls.append({"url": url, "headers": headers, "json": json})
        return self._post

    async def get(
        self, url: str, *, headers: dict[str, str], params: dict[str, str] | None = None
    ) -> _FakeResponse:
        self.get_calls.append({"url": url, "headers": headers, "params": params})
        for needle, resp in self._get_routes.items():
            if needle in url:
                return resp
        return _FakeResponse(200, {"name": "ok"})


def _install_client(monkeypatch: pytest.MonkeyPatch, client: _FakeClient | _RoutingClient) -> None:
    monkeypatch.setattr(opr.httpx, "AsyncClient", lambda **_kwargs: client)


def _set_config(monkeypatch: pytest.MonkeyPatch, configurable: dict[str, Any]) -> None:
    monkeypatch.setattr(opr, "get_config", lambda: {"configurable": configurable})


def _open() -> dict[str, Any]:
    return asyncio.run(
        opr._open_pull_request(
            owner="langchain-ai",
            repo="open-swe",
            head="open-swe/feature",
            base="main",
            title="feat: x",
            body="body",
            draft=True,
        )
    )


def test_uses_user_token_for_slack_with_login(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(monkeypatch, {"source": "slack", "github_login": "johannes117"})

    from agent.dashboard import profiles

    async def fake_user_token(login: str, **_kw: Any) -> str | None:
        assert login == "johannes117"
        return "user-tok"

    monkeypatch.setattr(profiles, "get_valid_access_token", fake_user_token)

    async def fail_bot() -> str | None:
        raise AssertionError("bot token should not be used when a user token exists")

    monkeypatch.setattr(opr, "get_github_app_installation_token", fail_bot)

    client = _FakeClient(
        post=_FakeResponse(
            201,
            {"html_url": "https://x/pull/1", "number": 1, "user": {"login": "johannes117"}},
        )
    )
    _install_client(monkeypatch, client)

    result = _open()

    assert result["success"] is True
    assert result["created"] is True
    assert result["url"] == "https://x/pull/1"
    assert result["author"] == "johannes117"
    assert result["token_kind"] == "user"
    assert client.post_calls[0]["headers"]["Authorization"] == "Bearer user-tok"
    assert client.post_calls[0]["json"] == {
        "title": "feat: x",
        "head": "open-swe/feature",
        "base": "main",
        "body": "body",
        "draft": True,
    }


def test_uses_user_token_for_linear_with_login(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(monkeypatch, {"source": "linear", "github_login": "johannes117"})

    from agent.dashboard import profiles

    async def fake_user_token(login: str, **_kw: Any) -> str | None:
        assert login == "johannes117"
        return "user-tok"

    monkeypatch.setattr(profiles, "get_valid_access_token", fake_user_token)

    async def fail_bot() -> str | None:
        raise AssertionError("bot token should not be used when a user token exists")

    monkeypatch.setattr(opr, "get_github_app_installation_token", fail_bot)

    client = _FakeClient(
        post=_FakeResponse(
            201,
            {"html_url": "https://x/pull/1", "number": 1, "user": {"login": "johannes117"}},
        )
    )
    _install_client(monkeypatch, client)

    result = _open()

    assert result["success"] is True
    assert result["created"] is True
    assert result["url"] == "https://x/pull/1"
    assert result["author"] == "johannes117"
    assert result["token_kind"] == "user"
    assert client.post_calls[0]["headers"]["Authorization"] == "Bearer user-tok"


def test_falls_back_to_bot_for_github_source(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(monkeypatch, {"source": "github", "github_login": "johannes117"})

    from agent.dashboard import profiles

    async def fail_user_token(login: str, **_kw: Any) -> str | None:
        raise AssertionError("user token should not be resolved for github source")

    monkeypatch.setattr(profiles, "get_valid_access_token", fail_user_token)

    async def fake_bot() -> str | None:
        return "bot-tok"

    monkeypatch.setattr(opr, "get_github_app_installation_token", fake_bot)

    client = _FakeClient(
        post=_FakeResponse(
            201, {"html_url": "https://x/pull/2", "number": 2, "user": {"login": "open-swe[bot]"}}
        )
    )
    _install_client(monkeypatch, client)

    result = _open()

    assert result["token_kind"] == "bot"
    assert client.post_calls[0]["headers"]["Authorization"] == "Bearer bot-tok"


def test_falls_back_to_bot_when_user_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(monkeypatch, {"source": "slack", "github_login": "johannes117"})

    from agent.dashboard import profiles

    async def no_user_token(login: str, **_kw: Any) -> str | None:
        return None

    monkeypatch.setattr(profiles, "get_valid_access_token", no_user_token)

    async def fake_bot() -> str | None:
        return "bot-tok"

    monkeypatch.setattr(opr, "get_github_app_installation_token", fake_bot)

    client = _FakeClient(post=_FakeResponse(201, {"html_url": "u", "number": 3, "user": {}}))
    _install_client(monkeypatch, client)

    assert _open()["token_kind"] == "bot"


def test_returns_existing_pr_on_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(monkeypatch, {"source": "slack", "github_login": "johannes117"})

    from agent.dashboard import profiles

    monkeypatch.setattr(profiles, "get_valid_access_token", lambda *_a, **_k: _coro("user-tok"))
    monkeypatch.setattr(opr, "get_github_app_installation_token", lambda: _coro("bot"))

    client = _FakeClient(
        post=_FakeResponse(422, text="A pull request already exists"),
        get=_FakeResponse(
            200, [{"html_url": "https://x/pull/9", "number": 9, "user": {"login": "johannes117"}}]
        ),
    )
    _install_client(monkeypatch, client)

    result = _open()

    assert result["success"] is True
    assert result["created"] is False
    assert result["number"] == 9
    pr_lookup = [call for call in client.get_calls if call["params"]]
    assert pr_lookup[0]["params"] == {
        "head": "langchain-ai:open-swe/feature",
        "state": "open",
    }


def test_error_surfaced_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(monkeypatch, {"source": "slack", "github_login": "johannes117"})

    from agent.dashboard import profiles

    monkeypatch.setattr(profiles, "get_valid_access_token", lambda *_a, **_k: _coro("user-tok"))
    monkeypatch.setattr(opr, "get_github_app_installation_token", lambda: _coro("bot"))

    client = _FakeClient(post=_FakeResponse(403, {"message": "Resource not accessible"}))
    _install_client(monkeypatch, client)

    result = _open()

    assert result["success"] is False
    assert result["code"] == "github_pr_create_failed"
    assert result["recoverable_by_agent"] is False
    assert result["pr_created"] is False
    assert "403" in result["error"]


def test_404_create_returns_actionable_access_diagnostic(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _set_config(monkeypatch, {"source": "slack", "github_login": "johannes117", "thread_id": "t1"})
    _stub_token(monkeypatch)
    client = _FakeClient(post=_FakeResponse(404, {"message": "Not Found"}))
    _install_client(monkeypatch, client)

    result = _open()

    assert result["success"] is False
    assert result["code"] == "github_app_access_missing_or_repo_not_found"
    assert result["recoverable_by_agent"] is False
    assert result["owner"] == "langchain-ai"
    assert result["repo"] == "open-swe"
    assert result["head"] == "open-swe/feature"
    assert result["base"] == "main"
    assert result["branch_pushed"] is True
    assert result["pr_created"] is False
    assert "install or grant" in result["suggested_action"]
    assert "PR created: no" in result["error"]
    assert (
        "open_pull_request_failed code=github_app_access_missing_or_repo_not_found" in caplog.text
    )


def test_preflight_head_branch_404_reports_branch_not_pushed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_config(monkeypatch, {"source": "slack", "github_login": "johannes117"})
    _stub_token(monkeypatch)
    client = _RoutingClient(
        post=_FakeResponse(201, {"html_url": "u", "number": 1, "user": {}}),
        get_routes={
            "/repos/langchain-ai/open-swe/branches/main": _FakeResponse(200, {"name": "main"}),
            "/repos/langchain-ai/open-swe/branches/open-swe%2Ffeature": _FakeResponse(
                404, {"message": "Branch not found"}
            ),
            "/repos/langchain-ai/open-swe": _FakeResponse(200, {"private": True}),
        },
    )
    _install_client(monkeypatch, client)

    result = _open()

    assert result["success"] is False
    assert result["code"] == "github_pr_branch_not_visible"
    assert result["branch_pushed"] is False
    assert result["head_branch_visible"] is False
    assert result["failed_step"] == "preflight_head_branch"
    assert client.post_calls == []


async def _coro(value: Any) -> Any:
    return value


def _open_with_body(body: str) -> dict[str, Any]:
    return asyncio.run(
        opr._open_pull_request(
            owner="langchain-ai",
            repo="open-swe",
            head="open-swe/feature",
            base="main",
            title="feat: x",
            body=body,
            draft=True,
        )
    )


def _stub_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opr, "_resolve_pr_author_token", lambda: _coro(("tok", "user")))


def _stub_plan(monkeypatch: pytest.MonkeyPatch, plan: dict[str, Any] | None) -> None:
    monkeypatch.setattr(opr, "get_plan_content", lambda *_a, **_k: _coro(plan))


def test_appends_slack_reference_for_private_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(
        monkeypatch,
        {
            "source": "slack",
            "slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.000100"},
        },
    )
    _stub_token(monkeypatch)
    monkeypatch.setattr(
        opr, "get_slack_permalink", lambda *_a, **_k: _coro("https://slack.example/p1")
    )

    client = _RoutingClient(
        post=_FakeResponse(201, {"html_url": "u", "number": 1, "user": {}}),
        get_routes={"/repos/langchain-ai/open-swe": _FakeResponse(200, {"private": True})},
    )
    _install_client(monkeypatch, client)

    _open_with_body("original body")

    sent_body = client.post_calls[0]["json"]["body"]
    assert sent_body.startswith("original body")
    assert "## References" in sent_body
    assert "- Slack thread: https://slack.example/p1" in sent_body


def test_appends_plan_reference_from_thread_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    _set_config(monkeypatch, {"source": "dashboard", "thread_id": "thread-1"})
    _stub_token(monkeypatch)
    _stub_plan(monkeypatch, {"markdown": "# Plan\n- step 1", "status": "ready"})

    client = _FakeClient(post=_FakeResponse(201, {"html_url": "u", "number": 1, "user": {}}))
    _install_client(monkeypatch, client)

    _open_with_body("body")

    assert client.post_calls[0]["json"]["body"] == (
        "body\n\n## References\n- Plan: https://dashboard.example/agents/thread-1/plan"
    )
    assert client.post_calls


def test_omits_plan_reference_when_no_plan_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    _set_config(monkeypatch, {"source": "dashboard", "thread_id": "thread-1"})
    _stub_token(monkeypatch)
    _stub_plan(monkeypatch, None)

    client = _FakeClient(post=_FakeResponse(201, {"html_url": "u", "number": 1, "user": {}}))
    _install_client(monkeypatch, client)

    _open_with_body("body")

    assert client.post_calls[0]["json"]["body"] == "body"
    assert client.post_calls


def test_omits_plan_reference_when_plan_markdown_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    _set_config(monkeypatch, {"source": "dashboard", "thread_id": "thread-1"})
    _stub_token(monkeypatch)
    _stub_plan(monkeypatch, {"markdown": "   \n  ", "status": "ready"})

    client = _FakeClient(post=_FakeResponse(201, {"html_url": "u", "number": 1, "user": {}}))
    _install_client(monkeypatch, client)

    _open_with_body("body")

    assert client.post_calls[0]["json"]["body"] == "body"
    assert client.post_calls


def test_omits_plan_reference_when_store_lookup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    _set_config(monkeypatch, {"source": "dashboard", "thread_id": "thread-1"})
    _stub_token(monkeypatch)

    async def fail_plan(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("store down")

    monkeypatch.setattr(opr, "get_plan_content", fail_plan)

    client = _FakeClient(post=_FakeResponse(201, {"html_url": "u", "number": 1, "user": {}}))
    _install_client(monkeypatch, client)

    _open_with_body("body")

    assert client.post_calls[0]["json"]["body"] == "body"
    assert client.post_calls


def test_plan_reference_survives_source_reference_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    _set_config(
        monkeypatch,
        {
            "source": "slack",
            "thread_id": "thread-1",
            "slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.000100"},
        },
    )
    _stub_token(monkeypatch)
    _stub_plan(monkeypatch, {"markdown": "# Plan\n- step 1", "status": "ready"})

    async def fail_permalink(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("slack failed")

    monkeypatch.setattr(opr, "get_slack_permalink", fail_permalink)
    client = _FakeClient(post=_FakeResponse(201, {"html_url": "u", "number": 1, "user": {}}))
    _install_client(monkeypatch, client)

    _open_with_body("body")

    sent_body = client.post_calls[0]["json"]["body"]
    assert "- Plan: https://dashboard.example/agents/thread-1/plan" in sent_body
    assert client.post_calls


def test_no_reference_for_public_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(
        monkeypatch,
        {
            "source": "slack",
            "slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.000100"},
        },
    )
    _stub_token(monkeypatch)
    monkeypatch.setattr(
        opr, "get_slack_permalink", lambda *_a, **_k: _coro("https://slack.example/p1")
    )

    client = _RoutingClient(
        post=_FakeResponse(201, {"html_url": "u", "number": 1, "user": {}}),
        get_routes={"/repos/langchain-ai/open-swe": _FakeResponse(200, {"private": False})},
    )
    _install_client(monkeypatch, client)

    _open_with_body("original body")

    assert client.post_calls[0]["json"]["body"] == "original body"


def test_public_repo_appends_plan_but_not_source_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    _set_config(
        monkeypatch,
        {
            "source": "slack",
            "thread_id": "thread-1",
            "slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.000100"},
        },
    )
    _stub_token(monkeypatch)
    _stub_plan(monkeypatch, {"markdown": "# Plan\n- step 1", "status": "ready"})
    monkeypatch.setattr(
        opr, "get_slack_permalink", lambda *_a, **_k: _coro("https://slack.example/p1")
    )

    client = _RoutingClient(
        post=_FakeResponse(201, {"html_url": "u", "number": 1, "user": {}}),
        get_routes={"/repos/langchain-ai/open-swe": _FakeResponse(200, {"private": False})},
    )
    _install_client(monkeypatch, client)

    _open_with_body("body")

    sent_body = client.post_calls[0]["json"]["body"]
    assert "- Plan: https://dashboard.example/agents/thread-1/plan" in sent_body
    assert "Slack thread" not in sent_body


def test_appends_linear_reference_for_private_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(
        monkeypatch,
        {
            "source": "linear",
            "linear_issue": {"url": "https://linear.app/x/AB-12", "identifier": "AB-12"},
        },
    )
    _stub_token(monkeypatch)

    client = _RoutingClient(
        post=_FakeResponse(201, {"html_url": "u", "number": 1, "user": {}}),
        get_routes={"/repos/langchain-ai/open-swe": _FakeResponse(200, {"private": True})},
    )
    _install_client(monkeypatch, client)

    _open_with_body("body")

    sent_body = client.post_calls[0]["json"]["body"]
    assert "- Linear ticket: [AB-12](https://linear.app/x/AB-12)" in sent_body


def test_appends_github_issue_reference_for_private_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(
        monkeypatch,
        {
            "source": "github",
            "github_issue": {
                "url": "https://github.com/langchain-ai/open-swe/issues/42",
                "number": 42,
            },
        },
    )
    _stub_token(monkeypatch)

    client = _RoutingClient(
        post=_FakeResponse(201, {"html_url": "u", "number": 1, "user": {}}),
        get_routes={"/repos/langchain-ai/open-swe": _FakeResponse(200, {"private": True})},
    )
    _install_client(monkeypatch, client)

    _open_with_body("body")

    sent_body = client.post_calls[0]["json"]["body"]
    assert "- GitHub issue: [#42](https://github.com/langchain-ai/open-swe/issues/42)" in sent_body


def test_skips_append_when_no_source_context(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(monkeypatch, {"source": "slack"})
    _stub_token(monkeypatch)

    client = _FakeClient(post=_FakeResponse(201, {"html_url": "u", "number": 1, "user": {}}))
    _install_client(monkeypatch, client)

    _open_with_body("body")

    assert client.post_calls[0]["json"]["body"] == "body"
    assert client.post_calls


def test_does_not_duplicate_existing_references(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(
        monkeypatch,
        {
            "source": "slack",
            "slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.000100"},
        },
    )
    _stub_token(monkeypatch)

    client = _FakeClient(post=_FakeResponse(201, {"html_url": "u", "number": 1, "user": {}}))
    _install_client(monkeypatch, client)

    _open_with_body("body\n\n## References\n- existing")

    assert client.post_calls[0]["json"]["body"] == "body\n\n## References\n- existing"
    assert client.post_calls


def test_derive_pr_state_prefers_merged() -> None:
    assert opr.derive_pr_state(state="closed", merged=True, draft=True) == "merged"


def test_derive_pr_state_closed_over_draft() -> None:
    assert opr.derive_pr_state(state="closed", merged=False, draft=True) == "closed"


def test_derive_pr_state_draft() -> None:
    assert opr.derive_pr_state(state="open", merged=False, draft=True) == "draft"


def test_derive_pr_state_open() -> None:
    assert opr.derive_pr_state(state="open", merged=False, draft=False) == "open"
