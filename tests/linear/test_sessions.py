"""Linear agent-session lifecycle: the first activity, repo resolution, follow-ups."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.linear import sessions
from agent.linear.schema import AgentSessionEvent, ErrorContent, ThoughtContent

_REPO = {"owner": "langchain-ai", "name": "open-swe"}


class _RecordingClient:
    """Stands in for the Linear GraphQL client, recording every call in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def create_agent_activity(self, session_id, content, *, ephemeral=False, signal=None):
        self.calls.append(("activity", content))

    async def get_issue(self, issue_id):
        self.calls.append(("get_issue", issue_id))
        return None

    async def update_agent_session_external_url(self, session_id, url):
        self.calls.append(("external_url", url))

    async def viewer_id(self):
        return "app-user"


def _created_event(**overrides: Any) -> AgentSessionEvent:
    payload: dict[str, Any] = {
        "type": "AgentSessionEvent",
        "action": "created",
        "webhookTimestamp": 0,
        "agentSession": {
            "id": "session-1",
            "status": "pending",
            "issue": {
                "id": "issue-1",
                "identifier": "OS-1",
                "title": "Fix the thing",
                "description": "Broken",
            },
            "creator": {"id": "u1", "name": "Zhen", "email": "zhen@example.com"},
        },
        "promptContext": "## OS-1\nFix the thing",
    }
    payload.update(overrides)
    return AgentSessionEvent.model_validate(payload)


def _prompted_event(body: str = "also update the docs") -> AgentSessionEvent:
    return AgentSessionEvent.model_validate(
        {
            "type": "AgentSessionEvent",
            "action": "prompted",
            "webhookTimestamp": 0,
            "agentSession": {
                "id": "session-1",
                "status": "active",
                "issue": {"id": "issue-1", "identifier": "OS-1", "title": "Fix the thing"},
            },
            "agentActivity": {
                "id": "activity-1",
                "content": {"type": "prompt", "body": body},
                "user": {"id": "u1", "name": "Zhen", "email": "zhen@example.com"},
            },
        }
    )


@pytest.fixture
def linear(monkeypatch: pytest.MonkeyPatch) -> _RecordingClient:
    client = _RecordingClient()
    monkeypatch.setattr(sessions, "linear_client", lambda: client)
    return client


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def dispatch(thread_id, content, configurable, **kwargs):
        calls.append({"thread_id": thread_id, "configurable": configurable, **kwargs})
        return {"run_id": "run-1"}

    monkeypatch.setattr(sessions, "dispatch_agent_run", dispatch)
    monkeypatch.setattr(sessions, "upsert_agent_thread_metadata", AsyncMock())
    monkeypatch.setattr(sessions, "stream_linear_activities", AsyncMock())
    monkeypatch.setattr(sessions, "resolve_login_from_email_async", AsyncMock(return_value="zhen"))
    monkeypatch.setattr(sessions, "langgraph_client", lambda: AsyncMock())
    return calls


async def test_start_session_thinks_before_anything_else(
    linear: _RecordingClient, dispatched: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions, "resolve_repo_config", AsyncMock(return_value=_REPO))

    await sessions.start_session(_created_event())

    kind, content = linear.calls[0]
    assert kind == "activity"
    assert isinstance(content, ThoughtContent)


async def test_start_session_without_a_repository_errors_and_never_dispatches(
    linear: _RecordingClient, dispatched: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions, "resolve_repo_config", AsyncMock(return_value=None))

    await sessions.start_session(_created_event())

    assert not dispatched
    assert any(isinstance(content, ErrorContent) for _kind, content in linear.calls)


async def test_start_session_carries_the_session_id_into_the_run_config(
    linear: _RecordingClient, dispatched: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions, "resolve_repo_config", AsyncMock(return_value=_REPO))

    await sessions.start_session(_created_event())

    configurable = dispatched[0]["configurable"]
    assert configurable["linear_issue"]["agent_session_id"] == "session-1"
    assert configurable["source"] == "linear"
    assert configurable["github_login"] == "zhen"


async def test_start_session_links_the_session_to_the_dashboard(
    linear: _RecordingClient, dispatched: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions, "resolve_repo_config", AsyncMock(return_value=_REPO))
    monkeypatch.setattr(sessions, "dashboard_thread_url", lambda thread_id: "https://web/t/1")

    await sessions.start_session(_created_event())

    assert ("external_url", "https://web/t/1") in linear.calls


async def test_start_session_uses_linear_prompt_context_when_present(
    linear: _RecordingClient, dispatched: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions, "resolve_repo_config", AsyncMock(return_value=_REPO))

    await sessions.start_session(_created_event())

    system_message = dispatched[0]["input"]["messages"][1]
    assert "## OS-1" in system_message["content"]


async def test_guidance_repository_beats_the_team_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sessions,
        "get_repo_config_from_team_mapping",
        lambda team, project: {"owner": "langchain-ai", "name": "from-team-map"},
    )
    event = _created_event(guidance=[{"body": "Always work in `langchain-ai/from-guidance`."}])

    repo_config = await sessions.resolve_repo_config(
        trigger_text="",
        requester_email="",
        issue=event.agent_session.issue,
        guidance=event.guidance,
    )

    assert repo_config == {"owner": "langchain-ai", "name": "from-guidance"}


async def test_text_named_repository_beats_guidance(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_config = await sessions.resolve_repo_config(
        trigger_text="work in repo:langchain-ai/from-text",
        requester_email="",
        issue=None,
        guidance=[sessions.GuidanceRule(body="Use `langchain-ai/from-guidance`.")],
    )

    assert repo_config == {"owner": "langchain-ai", "name": "from-text"}


async def test_continue_session_queues_onto_a_busy_thread(
    linear: _RecordingClient, dispatched: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions, "get_thread_active_status", AsyncMock(return_value=True))
    queue = AsyncMock(return_value=True)
    monkeypatch.setattr(sessions, "queue_message_for_thread", queue)

    await sessions.continue_session(_prompted_event())

    assert not dispatched
    queue.assert_awaited_once()
    assert queue.await_args is not None
    assert queue.await_args.args[1] == "also update the docs"
    assert any(isinstance(content, ThoughtContent) for _kind, content in linear.calls)


async def test_continue_session_dispatches_on_an_idle_thread(
    linear: _RecordingClient, dispatched: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions, "get_thread_active_status", AsyncMock(return_value=False))
    monkeypatch.setattr(sessions, "thread_repo_config", AsyncMock(return_value=_REPO))
    queue = AsyncMock()
    monkeypatch.setattr(sessions, "queue_message_for_thread", queue)

    await sessions.continue_session(_prompted_event())

    queue.assert_not_called()
    assert len(dispatched) == 1
    messages = dispatched[0]["input"]["messages"]
    assert "also update the docs" in messages[-1]["content"]
