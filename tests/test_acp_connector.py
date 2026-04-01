from __future__ import annotations

import json
from typing import Any

import pytest
from acp import RequestError
from acp.schema import TextContentBlock

from agent import acp_connector


class _FakeConnection:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, dict[str, Any]]] = []

    async def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        self.notifications.append((method, params or {}))


class _FakeThreads:
    def __init__(self) -> None:
        self.created_metadata: dict[str, Any] | None = None
        self.updated_metadata: dict[str, Any] | None = None
        self.thread = {
            "thread_id": "thread-1",
            "status": "idle",
            "updated_at": "2026-04-01T00:00:00+00:00",
            "metadata": {},
        }
        self.search_results: list[dict[str, Any]] = []
        self.state = {"values": {"messages": []}}

    async def create(self, *, metadata: dict[str, Any]) -> dict[str, Any]:
        self.created_metadata = metadata
        self.thread = {
            "thread_id": "thread-1",
            "status": "idle",
            "updated_at": "2026-04-01T00:00:00+00:00",
            "metadata": metadata,
        }
        return self.thread

    async def get(self, session_id: str) -> dict[str, Any]:
        return self.thread

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        self.updated_metadata = metadata
        self.thread["metadata"] = metadata
        return self.thread

    async def search(self, **_: Any) -> list[dict[str, Any]]:
        return self.search_results

    async def get_state(self, session_id: str) -> dict[str, Any]:
        return self.state


class _FakeRuns:
    def __init__(self, threads: _FakeThreads) -> None:
        self.threads = threads
        self.created: list[dict[str, Any]] = []
        self.cancelled: list[tuple[str, str]] = []

    async def create(
        self,
        thread_id: str,
        assistant_id: str,
        *,
        input: dict[str, Any],
        config: dict[str, Any],
        multitask_strategy: str,
    ) -> dict[str, Any]:
        self.created.append(
            {
                "thread_id": thread_id,
                "assistant_id": assistant_id,
                "input": input,
                "config": config,
                "multitask_strategy": multitask_strategy,
            }
        )
        return {"run_id": "run-1"}

    async def join(self, thread_id: str, run_id: str) -> dict[str, Any]:
        self.threads.state = {
            "values": {
                "messages": [
                    {"type": "human", "content": "Investigate the failure"},
                    {"type": "ai", "content": "I found the issue and opened a fix."},
                ]
            }
        }
        return {"run_id": run_id}

    async def get(self, thread_id: str, run_id: str) -> dict[str, Any]:
        return {"status": "success"}

    async def cancel(self, thread_id: str, run_id: str, **_: Any) -> None:
        self.cancelled.append((thread_id, run_id))


class _FakeLangGraphClient:
    def __init__(self) -> None:
        self.threads = _FakeThreads()
        self.runs = _FakeRuns(self.threads)


_LANGSMITH_PRINCIPAL = acp_connector.AuthenticatedPrincipal(
    provider="langsmith",
    subject="octocat",
    display_name="The Octocat",
    user_email="octocat@example.com",
    github_token="ghu_test",
    github_login="octocat",
    github_user_id=123,
    github_name="The Octocat",
)

_API_KEY_PRINCIPAL = acp_connector.AuthenticatedPrincipal(
    provider="api_key",
    subject="desktop-user",
    display_name="Desktop User",
    user_email=None,
    github_token=None,
    github_login=None,
    github_user_id=None,
    github_name=None,
    allow_github_app_fallback=True,
)


def _make_agent(client: _FakeLangGraphClient) -> acp_connector.OpenSWEAcpAgent:
    return acp_connector.OpenSWEAcpAgent(
        url="https://langgraph.example.com",
        assistant_id="agent",
        client_factory=lambda **_: client,
    )


def _mock_authenticated_principal(
    monkeypatch: pytest.MonkeyPatch,
    principal: acp_connector.AuthenticatedPrincipal = _LANGSMITH_PRINCIPAL,
) -> None:
    async def fake_require_authenticated_principal(
        self: acp_connector.OpenSWEAcpAgent,
        *,
        force_refresh: bool = False,
    ) -> acp_connector.AuthenticatedPrincipal:
        return principal

    monkeypatch.setattr(
        acp_connector.OpenSWEAcpAgent,
        "_require_authenticated_principal",
        fake_require_authenticated_principal,
    )


@pytest.mark.asyncio
async def test_initialize_advertises_langsmith_and_api_key_auth() -> None:
    agent = _make_agent(_FakeLangGraphClient())

    response = await agent.initialize()

    assert response.auth_methods
    assert [method.id for method in response.auth_methods] == [
        acp_connector.ACP_LANGSMITH_AUTH_METHOD_ID,
        acp_connector.ACP_API_KEY_AUTH_METHOD_ID,
    ]


@pytest.mark.asyncio
async def test_new_session_requires_authentication() -> None:
    agent = _make_agent(_FakeLangGraphClient())

    with pytest.raises(RequestError) as exc_info:
        await agent.new_session("/tmp/open-swe")

    assert str(exc_info.value) == "Authentication required"


@pytest.mark.asyncio
async def test_authenticate_with_langsmith_exchanges_for_github_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _make_agent(_FakeLangGraphClient())
    monkeypatch.setenv(acp_connector.ACP_LANGSMITH_API_KEY_ENV, "lsv2_test")

    async def fake_get_github_token_for_langsmith_api_key(
        api_key: str,
        tenant_id: str | None = None,
    ) -> dict[str, str]:
        assert api_key == "lsv2_test"
        assert tenant_id is None
        return {"token": "ghu_test"}

    async def fake_resolve_authenticated_principal_from_github_token(
        token: str,
        *,
        token_label: str,
    ) -> acp_connector.AuthenticatedPrincipal:
        assert token == "ghu_test"
        assert token_label == f"{acp_connector.ACP_LANGSMITH_API_KEY_ENV} GitHub exchange"
        return acp_connector.AuthenticatedPrincipal(
            provider="github",
            subject="octocat",
            display_name="The Octocat",
            user_email="octocat@example.com",
            github_token="ghu_test",
            github_login="octocat",
            github_user_id=123,
            github_name="The Octocat",
        )

    monkeypatch.setattr(
        acp_connector,
        "get_github_token_for_langsmith_api_key",
        fake_get_github_token_for_langsmith_api_key,
    )
    monkeypatch.setattr(
        acp_connector,
        "resolve_authenticated_principal_from_github_token",
        fake_resolve_authenticated_principal_from_github_token,
    )

    response = await agent.authenticate(acp_connector.ACP_LANGSMITH_AUTH_METHOD_ID)

    assert isinstance(response, object)
    assert agent._authenticated_principal == _LANGSMITH_PRINCIPAL


@pytest.mark.asyncio
async def test_new_session_persists_api_key_identity_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeLangGraphClient()
    agent = _make_agent(client)
    conn = _FakeConnection()
    agent.on_connect(conn)
    monkeypatch.setenv(acp_connector.ACP_API_KEY_ENV, "secret-1")
    monkeypatch.setenv(
        acp_connector.ACP_API_KEYS_ENV,
        json.dumps(
            [
                {
                    "subject": "desktop-user",
                    "display_name": "Desktop User",
                    "key": "secret-1",
                    "allow_github_app_fallback": True,
                }
            ]
        ),
    )

    monkeypatch.setattr(
        acp_connector,
        "_get_git_origin_url",
        lambda cwd: "git@github.com:langchain-ai/open-swe.git",
    )

    response = await agent.new_session("/tmp/open-swe")

    assert response.session_id == "thread-1"
    assert client.threads.created_metadata == {
        "repo": {"owner": "langchain-ai", "name": "open-swe"},
        "cwd": "/tmp/open-swe",
        "title": "langchain-ai/open-swe",
        "source": "acp",
        "acp_auth": {
            "provider": "api_key",
            "subject": "desktop-user",
            "display_name": "Desktop User",
        },
    }
    assert conn.notifications[-1][1]["update"]["sessionUpdate"] == "session_info_update"


@pytest.mark.asyncio
async def test_list_sessions_filters_by_repo_and_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeLangGraphClient()
    client.threads.search_results = [
        {
            "thread_id": "thread-1",
            "updated_at": "2026-04-01T00:00:00+00:00",
            "metadata": {
                "repo": {"owner": "langchain-ai", "name": "open-swe"},
                "title": "One",
                "acp_auth": {"provider": "langsmith", "subject": "octocat", "display_name": "The Octocat"},
            },
        },
        {
            "thread_id": "thread-2",
            "updated_at": "2026-04-01T00:00:00+00:00",
            "metadata": {
                "repo": {"owner": "langchain-ai", "name": "open-swe"},
                "title": "Two",
                "acp_auth": {"provider": "langsmith", "subject": "someone-else", "display_name": "Other User"},
            },
        },
    ]
    agent = _make_agent(client)
    _mock_authenticated_principal(monkeypatch)

    monkeypatch.setattr(
        acp_connector,
        "_get_git_origin_url",
        lambda cwd: "https://github.com/langchain-ai/open-swe.git",
    )

    response = await agent.list_sessions(cwd="/tmp/open-swe")

    assert [session.session_id for session in response.sessions] == ["thread-1"]
    assert response.sessions[0].title == "One"


@pytest.mark.asyncio
async def test_load_session_replays_existing_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeLangGraphClient()
    client.threads.thread = {
        "thread_id": "thread-1",
        "status": "idle",
        "updated_at": "2026-04-01T00:00:00+00:00",
        "metadata": {
            "repo": {"owner": "langchain-ai", "name": "open-swe"},
            "title": "Existing thread",
        },
    }
    client.threads.state = {
        "values": {
            "messages": [
                {"type": "human", "content": "Please investigate"},
                {"type": "ai", "content": "I am looking into it."},
            ]
        }
    }
    agent = _make_agent(client)
    conn = _FakeConnection()
    agent.on_connect(conn)
    _mock_authenticated_principal(monkeypatch)

    await agent.load_session("/tmp/open-swe", "thread-1")

    updates = [payload["update"]["sessionUpdate"] for _, payload in conn.notifications]
    assert updates == ["session_info_update", "user_message_chunk", "agent_message_chunk"]


@pytest.mark.asyncio
async def test_load_session_rejects_thread_owned_by_different_acp_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeLangGraphClient()
    client.threads.thread = {
        "thread_id": "thread-1",
        "status": "idle",
        "updated_at": "2026-04-01T00:00:00+00:00",
        "metadata": {
            "repo": {"owner": "langchain-ai", "name": "open-swe"},
            "title": "Existing thread",
            "acp_auth": {"provider": "langsmith", "subject": "someone-else", "display_name": "Other User"},
        },
    }
    agent = _make_agent(client)
    _mock_authenticated_principal(monkeypatch)

    with pytest.raises(RequestError) as exc_info:
        await agent.load_session("/tmp/open-swe", "thread-1")

    assert str(exc_info.value) == "Authentication required"


@pytest.mark.asyncio
async def test_prompt_forwards_langsmith_identity_to_langgraph_and_replays_new_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeLangGraphClient()
    client.threads.thread = {
        "thread_id": "thread-1",
        "status": "idle",
        "updated_at": "2026-04-01T00:00:00+00:00",
        "metadata": {
            "repo": {"owner": "langchain-ai", "name": "open-swe"},
            "cwd": "/tmp/open-swe",
            "title": "Existing thread",
        },
    }
    agent = _make_agent(client)
    conn = _FakeConnection()
    agent.on_connect(conn)
    _mock_authenticated_principal(monkeypatch, _LANGSMITH_PRINCIPAL)

    await agent.load_session("/tmp/open-swe", "thread-1")
    conn.notifications.clear()

    response = await agent.prompt(
        [TextContentBlock(type="text", text="Investigate the failure")],
        "thread-1",
        message_id="msg-1",
    )

    assert response.stop_reason == "end_turn"
    assert response.user_message_id == "msg-1"
    assert client.runs.created[0]["config"]["configurable"]["repo"] == {
        "owner": "langchain-ai",
        "name": "open-swe",
    }
    assert client.runs.created[0]["config"]["configurable"]["acp_auth_provider"] == "langsmith"
    assert client.runs.created[0]["config"]["configurable"]["acp_auth_subject"] == "octocat"
    assert client.runs.created[0]["config"]["configurable"]["github_token"] == "ghu_test"
    assert client.runs.created[0]["config"]["configurable"]["github_login"] == "octocat"
    assert client.runs.created[0]["config"]["configurable"]["user_email"] == "octocat@example.com"
    assert client.runs.created[0]["multitask_strategy"] == "interrupt"
    assert conn.notifications[-1][1]["update"]["content"]["text"] == "I found the issue and opened a fix."


@pytest.mark.asyncio
async def test_prompt_includes_github_app_fallback_for_api_key_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeLangGraphClient()
    client.threads.thread = {
        "thread_id": "thread-1",
        "status": "idle",
        "updated_at": "2026-04-01T00:00:00+00:00",
        "metadata": {
            "repo": {"owner": "langchain-ai", "name": "open-swe"},
            "cwd": "/tmp/open-swe",
            "title": "Existing thread",
        },
    }
    agent = _make_agent(client)
    _mock_authenticated_principal(monkeypatch, _API_KEY_PRINCIPAL)

    await agent.load_session("/tmp/open-swe", "thread-1")
    await agent.prompt([TextContentBlock(type="text", text="Run the task")], "thread-1")

    assert client.runs.created[0]["config"]["configurable"]["acp_auth_provider"] == "api_key"
    assert client.runs.created[0]["config"]["configurable"]["allow_github_app_fallback"] is True
    assert client.runs.created[0]["config"]["configurable"]["github_token"] is None
