"""Analytics wiring in ``PrepareAgentRunMiddleware._prepare``.

Covers the identity data the middleware hands to ``record_agent_invocation_usage``
across public and private thread scopes, including the public GitHub profile
lookup hit / null-name / failure / cache paths.
"""

import json
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langgraph.runtime import Runtime

import agent.server as server
from agent.middleware.prepare_run import PrepareRunState
from agent.utils import ttl_cache
from tests.conftest import FakeStore

_INSTALLATION_TOKEN = "ghs_installation-token"


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if self._payload is None:
            raise json.JSONDecodeError("empty", "", 0)
        return self._payload


class _FakeGitHubClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[str] = []

    async def __aenter__(self) -> _FakeGitHubClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.requests.append(url)
        return self._responses.pop(0)


class _ReadyProxy:
    async def ready(self) -> Any:
        return MagicMock()


@pytest.fixture(autouse=True)
def _clear_public_profile_cache() -> None:
    ttl_cache.clear()


@pytest.fixture
def github_client(monkeypatch: pytest.MonkeyPatch) -> _FakeGitHubClient:
    client = _FakeGitHubClient([])
    import agent.utils.authorship as authorship

    monkeypatch.setattr(authorship.httpx2, "AsyncClient", lambda *a, **kw: client)
    return client


def _middleware(config: dict[str, Any], *, credential_login: str | None = None) -> Any:
    return server.PrepareAgentRunMiddleware(
        thread_id="thread-1",
        config=cast(Any, config),
        profile_login=config["configurable"].get("github_login"),
        repo_instructions=None,
        model_id="openai:gpt-5",
        effort=None,
        title_model=MagicMock(),
        source=config["configurable"]["source"],
        user_email="",
        linear_project_id="",
        linear_issue_number="",
        draft_prs=False,
        recent_thread_context_enabled=False,
        admin_workspaces=False,
        credential_login=credential_login,
    )


@pytest.fixture
def prepare_harness(monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore) -> dict[str, Any]:
    # Public threads resolve the bot installation token; private threads resolve
    # the verified owner's OAuth token.
    harness: dict[str, Any] = {"recorded": None, "github_token": _INSTALLATION_TOKEN}

    async def fake_resolve_github_token(config: Any, thread_id: str) -> tuple[str, str | None]:
        return harness["github_token"], None

    monkeypatch.setattr(server, "resolve_github_token", fake_resolve_github_token)

    async def fake_record_agent_invocation_usage(**kwargs: Any) -> None:
        harness["recorded"] = kwargs

    monkeypatch.setattr(server, "record_agent_invocation_usage", fake_record_agent_invocation_usage)
    monkeypatch.setattr(server, "schedule_thread_title_generation", lambda **kwargs: None)

    monkeypatch.setattr(
        server, "get_or_create_sandbox_backend_proxy", lambda thread_id: _ReadyProxy()
    )

    async def fake_work_dir(thread_id: str, backend: Any) -> str:
        return "/workspace"

    monkeypatch.setattr(server, "resolve_thread_work_dir", fake_work_dir)
    monkeypatch.setattr(server, "load_workspace", _async_none)
    monkeypatch.setattr(server, "_resolve_prompt_default_repo", _async_none)
    monkeypatch.setattr(server, "_resolve_user_custom_instructions", _async_none)
    monkeypatch.setattr(server, "_thread_participant_identities", _async_list)
    monkeypatch.setattr(server, "_workspace_admin", _async_false)
    monkeypatch.setattr(server, "construct_system_prompt", lambda *args, **kwargs: "system prompt")

    class _Threads:
        async def get(self, thread_id: str) -> dict[str, Any]:
            return {"thread_id": thread_id, "metadata": harness["thread_metadata"]}

        async def update(self, **kwargs: Any) -> None:
            harness["thread_update"] = kwargs

    monkeypatch.setattr(server, "client", MagicMock(threads=_Threads()))
    return harness


async def _async_none(*args: Any, **kwargs: Any) -> None:
    return None


async def _async_list(*args: Any, **kwargs: Any) -> list[Any]:
    return []


async def _async_false(*args: Any, **kwargs: Any) -> bool:
    return False


def _slack_config(**extra: Any) -> dict[str, Any]:
    configurable: dict[str, Any] = {
        "thread_id": "thread-1",
        "source": "slack",
        "github_login": "mason-gh",
        "invocation_id": "inv-1",
        "slack_thread": {
            "channel_id": "C1",
            "thread_ts": "1.2",
            "triggering_user_id": "U1",
            "triggering_user_name": "Mason Slack",
        },
    }
    configurable.update(extra)
    return {"configurable": configurable}


async def _prepare(middleware: Any) -> dict[str, Any]:
    return await middleware._prepare(
        cast(PrepareRunState, {"messages": []}), cast(Runtime[Any], MagicMock())
    )


async def test_private_scope_uses_oauth_identity_and_skips_public_lookup(
    prepare_harness: dict[str, Any], github_client: _FakeGitHubClient
) -> None:
    prepare_harness["github_token"] = "oauth-token"
    prepare_harness["thread_metadata"] = {"visibility": "private", "owner_login": "mason-gh"}
    github_client._responses.append(
        _FakeResponse(
            200, {"id": 7, "login": "mason-gh", "name": "Mason", "email": "m@example.com"}
        )
    )

    middleware = _middleware(_slack_config(), credential_login="mason-gh")
    await _prepare(middleware)

    assert github_client.requests == ["https://api.github.com/user"]
    recorded = prepare_harness["recorded"]
    assert recorded["github_user_id"] == 7
    assert recorded["display_name"] == "Mason"
    assert recorded["display_name_source"] == "github"


async def test_public_scope_resolves_profile_via_installation_token(
    prepare_harness: dict[str, Any], github_client: _FakeGitHubClient, monkeypatch
) -> None:
    prepare_harness["thread_metadata"] = {"visibility": "public"}

    async def fake_token(**kwargs: Any) -> str:
        return _INSTALLATION_TOKEN

    monkeypatch.setattr("agent.github.app.get_github_app_installation_token", fake_token)
    github_client._responses.append(
        _FakeResponse(200, {"id": 99, "login": "mason-gh", "name": "Mason Example"})
    )

    middleware = _middleware(_slack_config())
    await _prepare(middleware)

    assert github_client.requests == ["https://api.github.com/users/mason-gh"]
    recorded = prepare_harness["recorded"]
    assert recorded["github_user_id"] == 99
    assert recorded["display_name"] == "Mason Example"
    assert recorded["display_name_source"] == "github"

    # A second run on another thread reuses the cached profile lookup.
    middleware = _middleware(_slack_config(thread_id="thread-2", invocation_id="inv-2"))
    middleware._thread_id = "thread-2"
    await _prepare(middleware)
    assert github_client.requests == ["https://api.github.com/users/mason-gh"]
    assert prepare_harness["recorded"]["github_user_id"] == 99


@pytest.mark.parametrize(
    ("status", "slack_name", "expected_id", "expected_name", "expected_source"),
    [
        (200, "Mason Slack", 99, "Mason Slack", "slack"),
        (200, "", 4321, None, None),
        (404, "Mason Slack", 4321, "Mason Slack", "slack"),
    ],
)
async def test_public_scope_name_fallback(
    prepare_harness,
    github_client,
    monkeypatch,
    status,
    slack_name,
    expected_id,
    expected_name,
    expected_source,
) -> None:
    prepare_harness["thread_metadata"] = {"visibility": "public"}

    async def fake_token(**kwargs: object) -> str:
        return _INSTALLATION_TOKEN

    monkeypatch.setattr("agent.github.app.get_github_app_installation_token", fake_token)
    github_client._responses.append(
        _FakeResponse(status, {"id": expected_id, "login": "mason-gh", "name": None})
    )
    config = _slack_config(github_user_id=4321)
    config["configurable"]["slack_thread"]["triggering_user_name"] = slack_name
    await _prepare(_middleware(config))

    recorded = prepare_harness["recorded"]
    assert recorded["github_user_id"] == expected_id
    assert recorded["display_name"] == expected_name
    assert recorded["display_name_source"] == expected_source
