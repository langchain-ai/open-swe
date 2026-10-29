"""``PrepareAgentRunMiddleware._prepare`` with the services it reads faked.

Covers the identity data the middleware hands to ``record_agent_invocation_usage``
across public and private thread scopes, including the public GitHub profile
lookup hit / null-name / failure / cache paths, and the system prompt a thread
keeps from one turn to the next.
"""

import asyncio
import json
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime

import openswe.server as server
from openswe.middleware.prepare_run import PrepareRunState
from openswe.prompt import SystemPrompt, construct_system_prompt
from openswe.sandboxes.providers.langsmith import TimeoutLangSmithSandbox
from openswe.utils import ttl_cache

_INSTALLATION_TOKEN = "installation-token"


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
    def follow(self, _sandbox_id: object) -> None:
        pass

    async def ready(self) -> Any:
        return MagicMock()


@pytest.fixture(autouse=True)
def _clear_public_profile_cache() -> None:
    ttl_cache.clear()


@pytest.fixture
def github_client(monkeypatch: pytest.MonkeyPatch) -> _FakeGitHubClient:
    client = _FakeGitHubClient([])
    import openswe.utils.authorship as authorship

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
        draft_prs=False,
        admin_workspaces=False,
        credential_login=credential_login,
    )


@pytest.fixture
def prepare_harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
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
    monkeypatch.setattr(server, "get_sandbox_metadata", AsyncMock(return_value={}))

    async def fake_work_dir(backend: Any) -> str:
        return "/workspace"

    monkeypatch.setattr(server, "resolve_sandbox_work_dir", fake_work_dir)
    monkeypatch.setattr(server, "load_workspace", _async_none)
    monkeypatch.setattr(server, "_resolve_prompt_default_repo", _async_none)
    monkeypatch.setattr(server, "_resolve_user_custom_instructions", _async_none)
    monkeypatch.setattr(server, "_thread_participant_identities", _async_list)
    monkeypatch.setattr(
        server, "construct_system_prompt", lambda *args, **kwargs: SystemPrompt("system prompt", "")
    )

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


async def test_prepare_records_start_before_return_even_when_metadata_is_slow_and_fails(
    prepare_harness: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    usage_started = asyncio.Event()
    release_usage = asyncio.Event()
    metadata_started = asyncio.Event()
    release_metadata = asyncio.Event()
    monkeypatch.setattr(server, "resolve_triggering_user_identity", AsyncMock(return_value=None))
    monkeypatch.setattr(server, "cached_workspace_settings", AsyncMock(return_value={}))

    async def record_usage(**kwargs: object) -> None:
        usage_started.set()
        await release_usage.wait()
        prepare_harness["recorded"] = kwargs

    async def update_metadata(**kwargs: object) -> None:
        metadata_started.set()
        await release_metadata.wait()
        raise RuntimeError("metadata update failed")

    monkeypatch.setattr(server, "record_agent_invocation_usage", record_usage)
    monkeypatch.setattr(server.client.threads, "update", update_metadata)
    preparing = asyncio.create_task(_prepare(_middleware(_slack_config())))
    try:
        await asyncio.wait_for(usage_started.wait(), timeout=1)
        assert not preparing.done()
        release_usage.set()
        await asyncio.wait_for(asyncio.shield(preparing), timeout=1)
        assert prepare_harness["recorded"] is not None
        await asyncio.wait_for(metadata_started.wait(), timeout=1)
    finally:
        release_usage.set()
        release_metadata.set()
        await preparing
        await asyncio.gather(*tuple(server._DETACHED))

    assert "metadata update failed" in caplog.text


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

    monkeypatch.setattr("openswe.github.app.get_github_app_installation_token", fake_token)
    github_client._responses.extend(
        [
            _FakeResponse(401, {"message": "Bad credentials"}),
            _FakeResponse(200, {"id": 99, "login": "mason-gh", "name": "Mason Example"}),
        ]
    )

    middleware = _middleware(_slack_config())
    await _prepare(middleware)

    assert github_client.requests == [
        "https://api.github.com/user",
        "https://api.github.com/users/mason-gh",
    ]
    recorded = prepare_harness["recorded"]
    assert recorded["github_user_id"] == 99
    assert recorded["display_name"] == "Mason Example"
    assert recorded["display_name_source"] == "github"

    # A second run on another thread reuses the cached profile lookup.
    middleware = _middleware(_slack_config(thread_id="thread-2", invocation_id="inv-2"))
    middleware._thread_id = "thread-2"
    github_client._responses.append(_FakeResponse(401, {"message": "Bad credentials"}))
    await _prepare(middleware)
    assert github_client.requests == [
        "https://api.github.com/user",
        "https://api.github.com/users/mason-gh",
        "https://api.github.com/user",
    ]
    assert prepare_harness["recorded"]["github_user_id"] == 99


async def test_a_thread_keeps_its_system_prompt_while_each_turn_says_where_it_came_from(
    prepare_harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any change to the system prompt misses the model provider's cache for the whole thread."""
    monkeypatch.setattr(server, "construct_system_prompt", construct_system_prompt)
    monkeypatch.setattr(server, "cached_workspace_settings", AsyncMock(return_value={}))
    prepare_harness["thread_metadata"] = {}
    backend = TimeoutLangSmithSandbox(MagicMock(name="sandbox-test"))
    proxy = _ReadyProxy()
    monkeypatch.setattr(proxy, "ready", AsyncMock(return_value=backend))
    monkeypatch.setattr(server, "get_or_create_sandbox_backend_proxy", lambda _: proxy)
    runtime = cast(Runtime[Any], MagicMock())
    asked = HumanMessage(content="Fix the login", id="human-1")

    from_slack = await _middleware(_slack_config())._prepare(
        cast(PrepareRunState, {"messages": [asked]}), runtime
    )
    first_reset = backend._git_identity_pending
    assert first_reset is not None
    told = [HumanMessage(content=message["content"]) for message in from_slack["messages"]]
    later = cast(
        PrepareRunState,
        {
            **{key: value for key, value in from_slack.items() if key != "messages"},
            "messages": [asked, *told, AIMessage(content="Done."), HumanMessage("And signup?")],
        },
    )
    dashboard = {"thread_id": "thread-1", "source": "dashboard", "invocation_id": "inv-2"}
    from_dashboard = await _middleware({"configurable": dashboard})._prepare(later, runtime)
    assert backend._git_identity_pending is not None
    assert backend._git_identity_pending != first_reset

    system_prompt = from_slack["rendered_system_prompt"]
    assert "triggered from Slack" not in system_prompt and "/workspace" not in system_prompt
    assert [message["content"] for message in from_slack["messages"]] == [
        AnyContext("environment", "/workspace"),
        AnyContext("source", "triggered from Slack"),
    ]
    # Only what changed is told again, and the prompt is not rendered again.
    assert "rendered_system_prompt" not in from_dashboard
    assert [message["content"] for message in from_dashboard["messages"]] == [
        AnyContext("source", "handled in the dashboard")
    ]

    # A thread whose prompt came from before it was fixed for good renders it once more.
    legacy = {**later, "system_prompt_fixed": None}
    rerendered = await _middleware({"configurable": dashboard})._prepare(
        cast(PrepareRunState, legacy), runtime
    )
    assert rerendered["rendered_system_prompt"] == system_prompt


def test_threads_share_their_system_prompt_up_to_what_only_each_says() -> None:
    """A new thread's first call reads the shared part from the cache another wrote."""
    first = construct_system_prompt(
        dashboard_base_url="https://dashboard.invalid",
        artifact_url="https://dashboard.invalid/threads/1/plan",
        default_repo={"owner": "acme", "name": "api"},
        repo_custom_instructions="Run make test.",
        workspace_name="Backend",
        workspace_instructions="Use uv.",
        workspace_repos=["acme/api"],
        admin_workspaces=True,
        sole_writer=True,
        continued_from_collaborative=True,
    )
    second = construct_system_prompt(
        dashboard_base_url="https://dashboard.invalid",
        artifact_url="https://dashboard.invalid/threads/2/plan",
    )

    assert first.shared == second.shared
    assert "https://dashboard.invalid/threads/1/plan" in first.thread


class AnyContext:
    """A ``run`` context block with ``context_id`` that says ``text``."""

    def __init__(self, context_id: str, text: str) -> None:
        self._opening = f'<dynamic-context kind="run" id="{context_id}">'
        self._text = text

    def __eq__(self, other: object) -> bool:
        return isinstance(other, str) and other.startswith(self._opening) and self._text in other

    def __repr__(self) -> str:
        return f"AnyContext({self._opening!r}, {self._text!r})"
