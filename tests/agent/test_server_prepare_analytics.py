"""Analytics wiring in ``PrepareAgentRunMiddleware._prepare``.

Covers the identity data the middleware hands to ``record_agent_invocation_usage``
across public and private thread scopes, including the public GitHub profile
lookup hit / null-name / failure / cache paths.
"""

import json
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.runtime import Runtime

import agent.server as server
from agent.middleware.prepare_run import PrepareRunState
from agent.utils import ttl_cache

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

    async def fake_work_dir(backend: Any) -> str:
        return "/workspace"

    monkeypatch.setattr(server, "resolve_sandbox_work_dir", fake_work_dir)
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


async def test_routed_run_exposes_selected_model_and_effort_to_tools(
    prepare_harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare_harness["thread_metadata"] = {"visibility": "public"}
    monkeypatch.setattr(server, "resolve_triggering_user_identity", _async_none)
    config = _slack_config()
    middleware = _middleware(config)
    middleware._effort = "medium"
    middleware._model_selection = MagicMock()
    middleware._model_selection.select_route = AsyncMock(return_value="performance")
    middleware._routing_defaults = {"performance": ("openai:routed", "high")}

    prepared = await _prepare(middleware)

    assert prepared["selected_model_id"] == "openai:routed"
    assert prepared["selected_effort"] == "high"
    assert config["configurable"]["resolved_agent_model_id"] == "openai:routed"
    assert config["configurable"]["resolved_agent_effort"] == "high"
    assert prepare_harness["thread_update"]["metadata"]["effort"] == "high"


async def test_router_failure_attributes_default_model(
    prepare_harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare_harness["thread_metadata"] = {"visibility": "public"}
    monkeypatch.setattr(server, "resolve_triggering_user_identity", _async_none)
    config = _slack_config()
    middleware = _middleware(config)
    middleware._effort = "medium"
    middleware._model_selection = MagicMock()
    middleware._model_selection.select_route = AsyncMock(return_value="default")
    middleware._routing_defaults = {"balanced": ("openai:routed", "high")}

    prepared = await _prepare(middleware)

    assert prepared["model_route"] == "default"
    assert prepared["selected_model_id"] == "openai:gpt-5"
    assert prepared["selected_effort"] == "medium"
    assert config["configurable"]["resolved_agent_model_id"] == "openai:gpt-5"
    assert prepare_harness["thread_update"]["metadata"]["model"] == "openai:gpt-5"


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
    github_client._responses.extend(
        [
            _FakeResponse(401),
            _FakeResponse(status, {"id": expected_id, "login": "mason-gh", "name": None}),
        ]
    )
    config = _slack_config(github_user_id=4321)
    config["configurable"]["slack_thread"]["triggering_user_name"] = slack_name
    await _prepare(_middleware(config))

    recorded = prepare_harness["recorded"]
    assert recorded["github_user_id"] == expected_id
    assert recorded["display_name"] == expected_name
    assert recorded["display_name_source"] == expected_source


@pytest.mark.parametrize("requested", ["anthropic:claude-opus-5-5", None])
async def test_initial_handoff_persists_before_work_and_attributes_selected_model(
    prepare_harness: dict[str, object], monkeypatch: pytest.MonkeyPatch, requested: str | None
) -> None:
    from agent.dashboard.options import available_requested_models
    from agent.middleware.model_selection import ModelSelectionMiddleware
    from agent.thread_title import ThreadHandoff
    from agent.utils.thread_settings import ThreadSettings

    prepare_harness["thread_metadata"] = {"visibility": "public"}
    monkeypatch.setattr(server, "resolve_triggering_user_identity", _async_none)
    settings: ThreadSettings = {"model_id": "openai:gpt-6-sol", "repo_instructions": "retain"}
    monkeypatch.setattr(server, "load_thread_settings", AsyncMock(return_value=settings))

    async def persist(
        client: object, thread_id: str, value: ThreadSettings, *, strict: bool
    ) -> None:
        settings.update(value)

    store = AsyncMock(side_effect=persist)
    monkeypatch.setattr(server, "store_thread_settings", store)
    handoff = AsyncMock(return_value=ThreadHandoff(title="A title", requested_model=requested))
    monkeypatch.setattr(server, "initial_thread_handoff", handoff)
    middleware = _middleware(_slack_config())
    chosen = MagicMock()
    router = ModelSelectionMiddleware(
        {"fast": MagicMock()},
        MagicMock(),
        routing_mode="fast",
        requested_model_factory=lambda _: chosen,
    )
    middleware._model_selection = router
    middleware._requested_models = available_requested_models(fable_enabled=False)
    middleware._routing_defaults = {"fast": ("openai:gpt-6-luna", "low")}
    prepared = await _prepare(middleware)
    assert settings["model_handoff_complete"] is True
    assert settings["repo_instructions"] == "retain"
    assert prepared["selected_model_id"] == (requested or "openai:gpt-6-luna")
    assert prepared["selected_effort"] == ("high" if requested else "low")
    assert prepared["requested_model"] == requested
    if requested:
        assert settings["model_id"] == requested
        assert settings["model_routing_enabled"] is False
    store.assert_awaited_once_with(server.client, "thread-1", settings, strict=True)
    prepared_again = await _prepare(middleware)
    assert prepared_again["selected_model_id"] == prepared["selected_model_id"]
    handoff.assert_awaited_once()


@pytest.mark.parametrize("failure", ["unavailable", "persistence"])
async def test_handoff_does_not_proceed_with_unavailable_or_unpersisted_choice(
    prepare_harness: dict[str, object], monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from agent.dashboard.options import available_requested_models
    from agent.thread_title import ThreadHandoff

    prepare_harness["thread_metadata"] = {"visibility": "public"}
    monkeypatch.setattr(server, "load_thread_settings", AsyncMock(return_value={}))
    model = "anthropic:claude-fable-5-1" if failure == "unavailable" else "openai:gpt-6-sol"
    monkeypatch.setattr(
        server,
        "initial_thread_handoff",
        AsyncMock(return_value=ThreadHandoff(title="A title", requested_model=model)),
    )
    store = AsyncMock(side_effect=RuntimeError("write failed"))
    monkeypatch.setattr(server, "store_thread_settings", store)
    middleware = _middleware(_slack_config())
    middleware._requested_models = available_requested_models(fable_enabled=False)
    middleware._model_selection = MagicMock()
    with pytest.raises((ValueError, RuntimeError)):
        await _prepare(middleware)
    middleware._model_selection.use_requested_model.assert_not_called()
    assert prepare_harness["recorded"] is None


@pytest.mark.parametrize("image_type", ["image", "image_url", None])
@pytest.mark.parametrize(
    "model", ["fireworks:accounts/fireworks/models/kimi-k3", "anthropic:claude-opus-5-5"]
)
async def test_requested_model_checks_image_support_before_persisting(
    prepare_harness: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    image_type: str | None,
    model: str,
) -> None:
    from langchain_core.messages import HumanMessage

    from agent.dashboard.options import available_requested_models
    from agent.thread_title import ThreadHandoff
    from agent.utils.thread_settings import ThreadSettings

    prepare_harness["thread_metadata"] = {"visibility": "public"}
    monkeypatch.setattr(server, "resolve_triggering_user_identity", _async_none)
    settings: ThreadSettings = {"model_id": "openai:gpt-6-sol"}
    monkeypatch.setattr(server, "load_thread_settings", AsyncMock(return_value=settings))
    store = AsyncMock()
    monkeypatch.setattr(server, "store_thread_settings", store)
    monkeypatch.setattr(
        server,
        "initial_thread_handoff",
        AsyncMock(return_value=ThreadHandoff(title="Inspect screenshot", requested_model=model)),
    )
    content: list[str | dict[str, object]] = [{"type": "text", "text": "Use this model to inspect"}]
    if image_type == "image":
        content.append({"type": "image", "base64": "aGVsbG8=", "mime_type": "image/png"})
    elif image_type == "image_url":
        content.append(
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}
        )
    state: PrepareRunState = {"messages": [HumanMessage(content=content)]}
    middleware = _middleware(_slack_config())
    middleware._requested_models = available_requested_models(fable_enabled=False)
    middleware._model_selection = MagicMock()
    if image_type and model.endswith("kimi-k3"):
        with pytest.raises(ValueError, match="does not support image input"):
            await middleware._prepare(state, MagicMock())
        store.assert_not_awaited()
        middleware._model_selection.use_requested_model.assert_not_called()
        assert settings == {"model_id": "openai:gpt-6-sol"}
        assert prepare_harness["recorded"] is None
    else:
        prepared = await middleware._prepare(state, MagicMock())
        assert prepared["selected_model_id"] == model
        store.assert_awaited_once()
        middleware._model_selection.use_requested_model.assert_called_once_with(model)
