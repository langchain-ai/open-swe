import asyncio
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx2
import langgraph_sdk
import pytest

import openswe.tools.open_pull_request  # noqa: F401

opr = sys.modules["openswe.tools.open_pull_request"]


@pytest.fixture(autouse=True)
def _consent_not_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opr, "require_consent", AsyncMock(return_value=None))


class _FakeRequest:
    def __init__(self, method: str, url: str) -> None:
        self.method = method
        self.url = url


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: Any = None,
        text: str = "",
        headers: dict[str, str] | None = None,
        request: _FakeRequest | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}
        self.request = request

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
        if url.endswith("/installation/repositories"):
            return _FakeResponse(200, {"repositories": [{"full_name": "langchain-ai/open-swe"}]})
        if self._get is not None:
            return self._get
        return _FakeResponse(200, {"name": "ok"})


class _RoutingClient:
    """Fake httpx2 client that routes GETs by URL substring."""

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
    monkeypatch.setattr(opr.httpx2, "AsyncClient", lambda **_kwargs: client)


def _set_config(
    monkeypatch: pytest.MonkeyPatch,
    configurable: dict[str, Any],
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    configurable.setdefault("thread_id", "pr-thread")
    metadata = (
        metadata
        if metadata is not None
        else (
            {"visibility": "public"}
            if configurable.get("source") == "github"
            else {"visibility": "private", "owner_login": configurable.get("github_login")}
        )
    )
    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(get=AsyncMock(return_value={"metadata": metadata}))
        ),
    )
    monkeypatch.setattr("openswe.run_config.get_config", lambda: {"configurable": configurable})
    monkeypatch.setattr(opr, "get_config", lambda: {"configurable": configurable}, raising=False)


def _open(base: str = "main") -> dict[str, Any]:
    return asyncio.run(
        opr._open_pull_request(
            owner="langchain-ai",
            repo="open-swe",
            head="open-swe/feature",
            base=base,
            title="feat: x",
            body="body",
            draft=True,
        )
    )


@pytest.mark.parametrize(
    "workspace_access", ["allowed", "other_account", "unavailable", "no_token"]
)
@pytest.mark.parametrize("requester_access", [True, False])
def test_public_pr_cannot_use_requester_authority_outside_workspace(
    monkeypatch: pytest.MonkeyPatch, workspace_access: str, requester_access: bool
) -> None:
    _set_config(
        monkeypatch,
        {"source": "slack", "github_login": "bob"},
        metadata={"visibility": "public", "owner_type": "user", "owner_login": "Alice"},
    )
    monkeypatch.setattr(
        "openswe.dashboard.profiles.get_valid_access_token",
        AsyncMock(side_effect={"Alice": "alice-token", "bob": "bob-token"}.get),
    )
    monkeypatch.setattr(
        opr,
        "get_github_app_installation_token",
        AsyncMock(return_value=None if workspace_access == "no_token" else "workspace-token"),
    )
    monkeypatch.setattr(opr, "_record_pr_telemetry", AsyncMock())
    monkeypatch.setattr(opr, "get_plan_content", AsyncMock(return_value=None))
    requests: list[httpx2.Request] = []

    async def github(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == "/installation/repositories":
            assert request.headers["Authorization"] == "Bearer workspace-token"
            if workspace_access == "unavailable":
                return httpx2.Response(503)
            if request.url.params["page"] == "1":
                return httpx2.Response(
                    200,
                    json={
                        "repositories": [{"full_name": f"workspace/repo-{i}"} for i in range(100)]
                    },
                )
            full_name = (
                "langchain-ai/open-swe" if workspace_access == "allowed" else "other/open-swe"
            )
            return httpx2.Response(200, json={"repositories": [{"full_name": full_name}]})
        # OAuth has broader access, including branches already pushed by someone else.
        assert request.headers["Authorization"] == "Bearer bob-token"
        if not requester_access:
            return httpx2.Response(403, json={"message": "Resource not accessible"})
        if request.method == "POST":
            return httpx2.Response(201, json={"number": 1, "user": {"login": "bob"}})
        return httpx2.Response(200, json={"name": "existing-branch", "private": False})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(github))
    monkeypatch.setattr(opr.httpx2, "AsyncClient", lambda **kwargs: client)
    result = _open()

    if workspace_access == "allowed" and requester_access:
        assert result["success"] is True
        assert result["author"] == "bob"
        assert result["token_kind"] == "user"
        assert any(request.method == "POST" for request in requests)
    else:
        assert result["success"] is False
        assert ("403" if workspace_access == "allowed" else "workspace") in result["error"]
        assert not any(request.method == "POST" for request in requests)
        assert all(request.headers["Authorization"] != "Bearer alice-token" for request in requests)


def test_profile_draft_preference_overrides_tool_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(monkeypatch, {"source": "slack", "github_login": "johannes117", "draft_prs": False})
    _stub_token(monkeypatch)
    client = _FakeClient(
        post=_FakeResponse(
            201,
            {"html_url": "https://x/pull/1", "number": 1, "user": {"login": "johannes117"}},
        )
    )
    _install_client(monkeypatch, client)

    result = _open()

    assert result["success"] is True
    assert client.post_calls[0]["json"]["draft"] is False


def test_private_pr_requires_user_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_config(monkeypatch, {"source": "slack", "github_login": "johannes117"})

    from openswe.dashboard import profiles

    async def no_user_token(login: str, **_kw: Any) -> str | None:
        return None

    monkeypatch.setattr(profiles, "get_valid_access_token", no_user_token)

    async def fake_bot() -> str | None:
        return "bot-tok"

    monkeypatch.setattr(opr, "get_github_app_installation_token", fake_bot)

    client = _FakeClient(post=_FakeResponse(201, {"html_url": "u", "number": 3, "user": {}}))
    _install_client(monkeypatch, client)

    with pytest.raises(opr.GitHubUserAuthRequired):
        _open()


def test_returns_existing_pr_on_422(monkeypatch: pytest.MonkeyPatch, fake_store) -> None:
    _set_config(
        monkeypatch,
        {"source": "slack", "github_login": "bob"},
        metadata={"visibility": "public", "owner_type": "user", "owner_login": "alice"},
    )

    from openswe.dashboard import profiles

    monkeypatch.setattr(
        profiles,
        "get_valid_access_token",
        AsyncMock(side_effect={"alice": "alice-token", "bob": "bob-token"}.get),
    )
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
    assert result["author"] == "johannes117"
    assert client.post_calls[0]["headers"]["Authorization"] == "Bearer bob-token"
    pr_lookup = [call for call in client.get_calls if call["url"].endswith("/pulls")]
    assert pr_lookup[0]["params"] == {
        "head": "langchain-ai:open-swe/feature",
        "state": "open",
    }


def test_404_create_returns_actionable_access_diagnostic(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _set_config(monkeypatch, {"source": "slack", "github_login": "johannes117", "thread_id": "t1"})
    _stub_token(monkeypatch)
    client = _FakeClient(post=_FakeResponse(404, {"message": "Not Found"}))
    _install_client(monkeypatch, client)

    result = _open()

    assert result["success"] is False
    assert "Branch pushed: langchain-ai/open-swe:open-swe/feature (yes)" in result["error"]
    assert "PR created: no" in result["error"]
    assert "not installed on, granted access" in result["error"]
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
    assert "head branch `open-swe/feature`" in result["error"]
    assert "Branch pushed: langchain-ai/open-swe:open-swe/feature (no)" in result["error"]
    assert client.post_calls == []


def test_preflight_base_branch_redirect_surfaces_raw_github_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_config(monkeypatch, {"source": "slack", "github_login": "johannes117"})
    _stub_token(monkeypatch)
    redirect = _FakeResponse(
        301,
        {"message": "Moved Permanently"},
        text='{"message":"Moved Permanently"}',
        headers={"location": "https://api.github.com/repos/langchain-ai/open-swe/branches/main"},
        request=_FakeRequest(
            "GET", "https://api.github.com/repos/langchain-ai/open-swe/branches/master"
        ),
    )
    client = _RoutingClient(
        post=_FakeResponse(201, {"html_url": "u", "number": 1, "user": {}}),
        get_routes={
            "/repos/langchain-ai/open-swe/branches/master": redirect,
            "/repos/langchain-ai/open-swe": _FakeResponse(200, {"private": True}),
        },
    )
    _install_client(monkeypatch, client)

    result = _open(base="master")

    assert result["success"] is False
    error = result["error"]
    assert (
        "GitHub responded to GET "
        "https://api.github.com/repos/langchain-ai/open-swe/branches/master with 301" in error
    )
    assert "location: https://api.github.com/repos/langchain-ai/open-swe/branches/main" in error
    assert 'response body: {"message":"Moved Permanently"}' in error
    assert client.post_calls == []


async def _coro(value: Any) -> Any:
    return value


def _open_with_body(body: str, state: dict[str, Any] | None = None) -> dict[str, Any]:
    return asyncio.run(
        opr._open_pull_request(
            owner="langchain-ai",
            repo="open-swe",
            head="open-swe/feature",
            base="main",
            title="feat: x",
            body=body,
            draft=True,
            state=state,
        )
    )


async def _passthrough_body(body: str, _state: dict[str, Any] | None = None) -> str:
    return body


def _stub_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opr, "private_credential_login", AsyncMock(return_value="test-owner"))
    monkeypatch.setattr(opr, "_resolve_pr_author_token", lambda *_a, **_k: _coro(("tok", "user")))
    # Footer stamping has its own test; here the body under assertion stays the caller's.
    monkeypatch.setattr(opr, "_stamp_attribution_footer", _passthrough_body)


def _stub_plan(monkeypatch: pytest.MonkeyPatch, plan: dict[str, Any] | None) -> None:
    monkeypatch.setattr(opr, "get_plan_content", lambda *_a, **_k: _coro(plan))


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
    _stub_plan(
        monkeypatch,
        {
            "html": "<html><head><title>Plan</title></head><body>step 1</body></html>",
            "status": "ready",
        },
    )

    async def fail_permalink(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("slack failed")

    monkeypatch.setattr(opr, "get_slack_permalink", fail_permalink)
    client = _FakeClient(post=_FakeResponse(201, {"html_url": "u", "number": 1, "user": {}}))
    _install_client(monkeypatch, client)

    _open_with_body("body")

    sent_body = client.post_calls[0]["json"]["body"]
    assert "- [Plan](https://dashboard.example/agents/thread-1/plan)" in sent_body
    assert client.post_calls


def test_public_repo_appends_plan_and_slack_reference(
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
    _stub_plan(
        monkeypatch,
        {
            "html": "<html><head><title>Plan</title></head><body>step 1</body></html>",
            "status": "ready",
        },
    )
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
    assert "- [Plan](https://dashboard.example/agents/thread-1/plan)" in sent_body
    assert "- [Slack thread](https://slack.example/p1)" in sent_body


@pytest.mark.parametrize("source", ["linear", "github_issue"])
@pytest.mark.parametrize("private", [False, True])
def test_issue_references_require_private_repo(
    monkeypatch: pytest.MonkeyPatch, source: str, private: bool
) -> None:
    _set_config(
        monkeypatch,
        {
            "source": source,
            "linear_issue": {"identifier": "ENG-123", "url": "https://linear.app/issue/ENG-123"},
            "github_issue": {"number": 123, "url": "https://github.com/org/private/issues/123"},
        },
    )
    _stub_token(monkeypatch)
    _stub_plan(monkeypatch, None)
    client = _RoutingClient(
        post=_FakeResponse(201, {"html_url": "u", "number": 1, "user": {}}),
        get_routes={"/repos/langchain-ai/open-swe": _FakeResponse(200, {"private": private})},
    )
    _install_client(monkeypatch, client)

    _open_with_body("body")

    sent_body = client.post_calls[0]["json"]["body"]
    reference = (
        "- [Linear ticket ENG-123](https://linear.app/issue/ENG-123)"
        if source == "linear"
        else "- [GitHub issue #123](https://github.com/org/private/issues/123)"
    )
    assert sent_body == (f"body\n\n## References\n{reference}" if private else "body")


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


def test_existing_pr_does_not_record_later_run_as_opening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_config(monkeypatch, {"source": "github", "github_login": "johannes117"})
    monkeypatch.setattr(opr, "get_github_app_installation_token", AsyncMock(return_value="bot-tok"))
    record_telemetry = AsyncMock()
    monkeypatch.setattr(opr, "_record_pr_telemetry", record_telemetry)
    existing = {"html_url": "https://x/pull/4", "number": 4, "user": {"login": "octo"}}
    _install_client(
        monkeypatch,
        _RoutingClient(
            post=_FakeResponse(422, {"message": "already exists"}),
            get_routes={"/pulls": _FakeResponse(200, [existing])},
        ),
    )

    result = _open()

    assert result["created"] is False
    assert record_telemetry.await_args is not None
    assert record_telemetry.await_args.kwargs["record_opening"] is False


@pytest.mark.parametrize(
    "retitle_thread,record_opening", [(True, True), (False, True), (True, False)]
)
async def test_record_pr_telemetry_retitles_only_new_prs_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    retitle_thread: bool,
    record_opening: bool,
) -> None:
    _set_config(
        monkeypatch,
        {
            "source": "slack",
            "thread_id": "t1",
            "github_login": "octo",
            "resolved_agent_model_id": "openai:gpt-5.6-sol",
            "run_id": "run-1",
            "slack_thread": {"channel_id": "C1", "thread_ts": "1.0"},
        },
    )
    monkeypatch.setattr(opr, "record_agent_pr_usage", AsyncMock())
    monkeypatch.setattr(opr, "get_active_slack_thread", AsyncMock(return_value=None))
    langgraph = MagicMock()
    langgraph.threads.get = AsyncMock(return_value={"metadata": {}})
    langgraph.threads.update = AsyncMock()
    monkeypatch.setattr(opr, "get_client", lambda: langgraph)
    mirror_metadata = AsyncMock()
    monkeypatch.setattr(opr, "mirror_thread_metadata", mirror_metadata)
    details = {
        "html_url": "https://github.com/langchain-ai/open-swe/pull/3",
        "number": 3,
        "state": "open",
        "draft": True,
        "merged": False,
        "title": "feat: x",
        "user": {"login": "octo"},
    }
    client = _FakeClient(post=_FakeResponse(201, {}), get=_FakeResponse(200, details))

    await opr._record_pr_telemetry(
        client=client,  # type: ignore[arg-type]
        token="tok",
        owner="langchain-ai",
        repo="open-swe",
        head="open-swe/feature",
        base="main",
        pr=details,
        retitle_thread=retitle_thread,
        record_opening=record_opening,
    )

    assert langgraph.threads.update.await_args is not None
    metadata = langgraph.threads.update.await_args.kwargs["metadata"]
    if retitle_thread and record_opening:
        assert metadata["title"] == "feat: x"
        assert metadata["title_seed"] is None
        mirror_metadata.assert_awaited_once_with("t1", {"title": "feat: x", "title_seed": None})
    else:
        assert "title" not in metadata
        mirror_metadata.assert_not_awaited()


def test_preflight_401_revokes_user_token(monkeypatch: pytest.MonkeyPatch, fake_store) -> None:
    from cryptography.fernet import Fernet

    from openswe.dashboard import profiles

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    _set_config(monkeypatch, {"source": "slack", "github_login": "johannes117"})
    _stub_token(monkeypatch)
    monkeypatch.setattr(opr, "pr_author_login", AsyncMock(return_value="johannes117"))
    asyncio.run(profiles.upsert_access_token("johannes117", "j@x.dev", "tok"))
    _install_client(monkeypatch, _FakeClient(post=_FakeResponse(201), get=_FakeResponse(401)))

    result = _open()

    assert "sign in with GitHub again" in result["error"]
    assert asyncio.run(profiles.get_valid_access_token("johannes117")) is None
    assert asyncio.run(profiles.has_access_token_record("johannes117")) is True
