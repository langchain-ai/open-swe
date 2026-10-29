import uuid
from typing import cast

import pytest
import rt.linear as linear
from managed_deepagents import (
    LinearChannelThread,
    LinearIssueCreateEvent,
    LinearSessionCreatedEvent,
    LinearSessionCreatedPayload,
    LinearSessionPromptedEvent,
)
from managed_deepagents._channels.http.endpoint import EndpointDeclined
from managed_deepagents._channels.http.types import ChannelName
from pydantic import BaseModel, TypeAdapter
from rt.intake import backend_endpoint
from runtime_api.endpoint import Endpoint
from runtime_api.linear import (
    RECORD_LINEAR_DELIVERY,
    START_LINEAR_SESSION,
    LinearDelivery,
    LinearDeliveryRecorded,
    LinearSessionDeclined,
    LinearSessionRun,
    LinearSessionStart,
    LinearSessionTurn,
)

_THREAD = LinearChannelThread(id=str(uuid.uuid4()), channel=ChannelName(name="linear"))
_RUN = LinearSessionRun(
    repository="acme/api",
    title="Bug",
    context={"graph": "agent", "__run_token": "signed"},
    metadata={"channel": "linear"},
    webhook="https://backend.example/webhooks/run-complete",
)


def test_the_channel_asks_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.example/")
    assert (
        backend_endpoint(START_LINEAR_SESSION.path)
        == "https://backend.example/mda/channel-api/start_linear_session"
    )

    # MDA reads the channels to build the deployment, without its environment:
    # a host that never resolves stands in, so a call fails instead of misrouting.
    monkeypatch.delenv("OPEN_SWE_BACKEND_URL")
    assert backend_endpoint("/x") == "https://open-swe-backend.invalid/x"


class _Backend:
    """The backend's channel API, answering ``start_linear_session`` with ``answer``."""

    def __init__(self, answer: LinearSessionRun | LinearSessionDeclined = _RUN) -> None:
        self.answer = answer
        self.recorded: list[LinearDelivery] = []
        self.turns: list[LinearSessionTurn] = []

    async def __call__(
        self, endpoint: Endpoint[BaseModel, BaseModel], request: BaseModel, *, msg_id: str
    ) -> BaseModel:
        if endpoint is RECORD_LINEAR_DELIVERY:
            self.recorded.append(cast(LinearDelivery, request))
            return LinearDeliveryRecorded()
        assert endpoint is START_LINEAR_SESSION
        assert msg_id == "msg_delivery-1"
        self.turns.append(cast(LinearSessionTurn, request))
        return LinearSessionStart(answer=self.answer)


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> _Backend:
    backend = _Backend()
    monkeypatch.setattr(linear, "ask_backend", backend)
    return backend


def _created(**session: object) -> LinearSessionCreatedEvent:
    return LinearSessionCreatedEvent(
        delivery_id="delivery-1",
        organization_id="org-1",
        issue_id="issue-1",
        session_id="session-1",
        # Validated as MDA's channel validates it, which keeps the email Linear sends.
        payload=TypeAdapter(LinearSessionCreatedPayload).validate_python(
            {
                "type": "AgentSessionEvent",
                "action": "created",
                "organizationId": "org-1",
                "webhookTimestamp": 1_700_000_000_000,
                "agentSession": {
                    "id": "session-1",
                    "issue": {
                        "id": "issue-1",
                        "identifier": "ENG-1",
                        "title": "Bug",
                        "description": "It crashes: ![shot](https://uploads.example/a.png)",
                        "url": "https://linear.app/acme/issue/ENG-1",
                    },
                    "comment": {"id": "comment-1", "body": "@Open SWE fix it"},
                    "creator": {"id": "user-1", "name": "Ada", "email": "ada@example.com"},
                    **session,
                },
            }
        ),
    )


@pytest.mark.asyncio
async def test_a_session_turn_starts_the_run_the_backend_planned(
    backend: _Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def image(url: str, _client: object) -> dict[str, str]:
        return {"type": "image", "url": url}

    monkeypatch.setattr(linear, "fetch_image_block", image)

    result = await linear.start_session(_created(), _THREAD)

    # Logged and handed to automations, as every delivery the channel handles.
    assert [delivery.key for delivery in backend.recorded] == ["AgentSessionEvent.created"]
    (turn,) = backend.turns
    assert str(turn.thread_id) == _THREAD.id
    assert (turn.author.email, turn.body, turn.comment_id) == (
        "ada@example.com",
        "@Open SWE fix it",
        "comment-1",
    )
    assert isinstance(result, tuple)
    run_input, options = result
    # The run calls back with the context the backend signed, and completes through its webhook.
    assert options == {
        "context": _RUN.context,
        "metadata": _RUN.metadata,
        "webhook": _RUN.webhook,
    }
    introduction, issue, human = cast(dict[str, list[dict[str, object]]], run_input)["messages"]
    assert "system:linear-issue" in str(introduction["content"])
    # The issue's prompt names the repository the backend chose, beside the image it shows.
    text, shown = cast(list[dict[str, str]], issue["content"])
    assert "## Repository: acme/api" in text["text"]
    assert shown == {"type": "image", "url": "https://uploads.example/a.png"}
    assert 'sender="linear:user-1"' in str(human["content"])
    assert "@Open SWE fix it" in str(human["content"])


@pytest.mark.asyncio
async def test_a_reply_in_the_session_is_the_repliers_turn(backend: _Backend) -> None:
    session = _created().payload["agentSession"]
    event = LinearSessionPromptedEvent(
        delivery_id="delivery-1",
        organization_id="org-1",
        issue_id="issue-1",
        session_id="session-1",
        payload={
            "type": "AgentSessionEvent",
            "action": "prompted",
            "organizationId": "org-1",
            "webhookTimestamp": 1_700_000_000_000,
            "agentSession": session,
            "agentActivity": {
                "content": {"type": "prompt", "body": "and the docs"},
                "sourceCommentId": "comment-2",
                "user": {"id": "user-2", "name": "Grace"},
            },
        },
    )

    await linear.start_session(event, _THREAD)

    (turn,) = backend.turns
    assert (turn.author.id, turn.body, turn.comment_id) == ("user-2", "and the docs", "comment-2")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("declined", "told"),
    [
        (LinearSessionDeclined(reason="no_repository"), "repo:owner/name"),
        (
            LinearSessionDeclined(reason="repository_not_allowed", repository="acme/secret"),
            "I'm not allowed to work in `acme/secret`",
        ),
        (
            LinearSessionDeclined(reason="sign_in", settings_url="https://swe.example/settings"),
            "[your Open SWE settings](https://swe.example/settings)",
        ),
        (LinearSessionDeclined(reason="sign_in_again"), "no longer valid"),
    ],
    ids=["no-repository", "not-allowed", "sign-in", "sign-in-again"],
)
async def test_the_session_hears_why_the_backend_started_nothing(
    monkeypatch: pytest.MonkeyPatch, declined: LinearSessionDeclined, told: str
) -> None:
    monkeypatch.setattr(linear, "ask_backend", _Backend(declined))

    result = await linear.start_session(_created(), _THREAD)

    assert isinstance(result, EndpointDeclined)
    assert told in result.message


@pytest.mark.asyncio
async def test_a_session_without_its_issue_is_told_so(backend: _Backend) -> None:
    result = await linear.start_session(_created(issue=None), _THREAD)

    assert result == EndpointDeclined(linear.UNREADABLE_SESSION)
    assert backend.turns == []


@pytest.mark.asyncio
async def test_issue_events_start_nothing(backend: _Backend) -> None:
    event = LinearIssueCreateEvent(
        delivery_id="delivery-1",
        organization_id="org-1",
        issue_id="issue-1",
        payload={
            "type": "Issue",
            "action": "create",
            "organizationId": "org-1",
            "webhookTimestamp": 1_700_000_000_000,
            "data": {"id": "issue-1", "identifier": "ENG-1", "title": "Bug"},
        },
    )

    assert await linear.record(event, _THREAD) is None
    (delivery,) = backend.recorded
    assert (delivery.key, delivery.delivery_id) == ("Issue.create", "delivery-1")
    assert backend.turns == []
