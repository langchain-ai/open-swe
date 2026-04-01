from __future__ import annotations

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


_AUTHENTICATED_USER = acp_connector.AuthenticatedUser(
    github_token="ghu_test",
    github_login="octocat",
    github_user_id=123,
    github_email="octocat@example.com",
    github_name="The Octocat",
)


def _make_agent(client: _FakeLangGraphClient) -> acp_connector.OpenSWEAcpAgent:
    return acp_connector.OpenSWEAcpAgent(
        url="https://langgraph.example.com",
        assistant_id="agent",
        client_factory=lambda **_: client,
    )


def _mock_authenticated_user(
    monkeypatch: pytest.MonkeyPatch,
    user: acp_connector.AuthenticatedUser = _AUTHENTICATED_USER,
) -> None:
    monkeypatch.setenv(acp_connector.ACP_GITHUB_TOKEN_ENV, user.github_token)

    async def fake_resolve_authenticated_user_from_github_token(
        token: str,
    ) -> acp_connector.AuthenticatedUser:
        assert token == user.github_token
        return user

    monkeypatch.setattr(
        acp_connector,
        "resolve_authenticated_user_from_github_token",
        fake_resolve_authenticated_user_from_github_token,
    )


@pytest.mark.asyncio
async def test_initialize_advertises_github_token_auth() -> None:
    agent = _make_agent(_FakeLangGraphClient())

    response = await agent.initialize()

    assert response.auth_methods
    assert response.auth_methods[0].id == acp_connector.ACP_GITHUB_TOKEN_AUTH_METHOD_ID
    assert response.auth_methods[0].vars[0].name == acp_connector.ACP_GITHUB_TOKEN_ENV


@pytest.mark.asyncio
async def test_new_session_requires_authentication() -> None:
    agent = _make_agent(_FakeLangGraphClient())

    with pytest.raises(RequestError) as exc_info:
        await agent.new_session("/tmp/open-swe")

    assert str(exc_info.value) == "Authentication required"


@pytest.mark.asyncio
async def test_new_session_persists_repo_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeLangGraphClient()
    agent = _make_agent(client)
    conn = _FakeConnection()
    agent.on_connect(conn)
    _mock_authenticated_user(monkeypatch)

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
        "acp_user": {
            "login": "octocat",
            "id": "123",
            "email": "octocat@example.com",
            "name": "The Octocat",
        },
    }
    assert conn.notifications[-1][1]["update"]["sessionUpdate"] == "session_info_update"


@pytest.mark.asyncio
async def test_list_sessions_filters_by_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeLangGraphClient()
    client.threads.search_results = [
        {
            "thread_id": "thread-1",
            "updated_at": "2026-04-01T00:00:00+00:00",
            "metadata": {
                "repo": {"owner": "langchain-ai", "name": "open-swe"},
                "title": "One",
                "acp_user": {"login": "octocat", "id": "123"},
            },
        },
        {
            "thread_id": "thread-2",
            "updated_at": "2026-04-01T00:00:00+00:00",
            "metadata": {
                "repo": {"owner": "langchain-ai", "name": "open-swe"},
                "title": "Two",
                "acp_user": {"login": "other-user", "id": "999"},
            },
        },
    ]
    agent = _make_agent(client)
    _mock_authenticated_user(monkeypatch)

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
    _mock_authenticated_user(monkeypatch)

    await agent.load_session("/tmp/open-swe", "thread-1")

    updates = [payload["update"]["sessionUpdate"] for _, payload in conn.notifications]
    assert updates == ["session_info_update", "user_message_chunk", "agent_message_chunk"]


@pytest.mark.asyncio
async def test_load_session_rejects_thread_owned_by_different_acp_user(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeLangGraphClient()
    client.threads.thread = {
        "thread_id": "thread-1",
        "status": "idle",
        "updated_at": "2026-04-01T00:00:00+00:00",
        "metadata": {
            "repo": {"owner": "langchain-ai", "name": "open-swe"},
            "title": "Existing thread",
            "acp_user": {"login": "someone-else", "id": "456"},
        },
    }
    agent = _make_agent(client)
    _mock_authenticated_user(monkeypatch)

    with pytest.raises(RequestError) as exc_info:
        await agent.load_session("/tmp/open-swe", "thread-1")

    assert str(exc_info.value) == "Authentication required"


@pytest.mark.asyncio
async def test_prompt_forwards_to_langgraph_and_replays_new_messages(
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
    _mock_authenticated_user(monkeypatch)

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
    assert client.runs.created[0]["config"]["configurable"]["github_token"] == "ghu_test"
    assert client.runs.created[0]["config"]["configurable"]["github_login"] == "octocat"
    assert client.runs.created[0]["config"]["configurable"]["user_email"] == "octocat@example.com"
    assert client.runs.created[0]["multitask_strategy"] == "interrupt"
    assert conn.notifications[-1][1]["update"]["content"]["text"] == "I found the issue and opened a fix."
