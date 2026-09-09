"""Parsing of Linear webhook payloads and agent-activity content."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from agent.linear.schema import (
    ActionContent,
    AgentSessionEvent,
    CommentCreateEvent,
    ElicitationContent,
    ErrorContent,
    LinearWebhookEnvelope,
    PromptContent,
    ResponseContent,
    ThoughtContent,
    parse_linear_webhook,
)


def _raw(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode()


def _comment_payload() -> dict[str, Any]:
    return {
        "action": "create",
        "type": "Comment",
        "createdAt": "2026-09-09T12:00:00.000Z",
        "organizationId": "org-1",
        "webhookTimestamp": 1789000000000,
        "webhookId": "wh-1",
        "actor": {"id": "user-1", "type": "user", "name": "Ramon"},
        "data": {
            "id": "comment-1",
            "body": "@open-swe please fix this",
            "createdAt": "2026-09-09T12:00:00.000Z",
            "issueId": "issue-1",
            "user": {"id": "user-1", "name": "Ramon", "email": "ramon@example.com"},
            "issue": {
                "id": "issue-1",
                "identifier": "OS-42",
                "title": "Broken thing",
                "team": {"id": "team-1", "name": "Infrastructure", "key": "INF"},
                "comments": {"nodes": [{"id": "comment-0", "body": "earlier"}]},
            },
        },
    }


def _agent_session_created_payload() -> dict[str, Any]:
    return {
        "action": "created",
        "type": "AgentSessionEvent",
        "createdAt": "2026-09-09T12:00:00.000Z",
        "organizationId": "org-1",
        "webhookTimestamp": 1789000000000,
        "webhookId": "wh-2",
        "oauthClientId": "oauth-1",
        "appUserId": "app-user-1",
        "promptContext": "The user delegated OS-42 to the agent.",
        "guidance": [{"body": "Always open a draft PR."}],
        "previousComments": [{"id": "comment-0", "body": "earlier", "issueId": "issue-1"}],
        "agentSession": {
            "id": "session-1",
            "status": "pending",
            "appUserId": "app-user-1",
            "createdAt": "2026-09-09T12:00:00.000Z",
            "creator": {"id": "user-1", "name": "Ramon", "email": "ramon@example.com"},
            "comment": {"id": "comment-1", "body": "@open-swe go", "issueId": "issue-1"},
            "issue": {"id": "issue-1", "identifier": "OS-42", "title": "Broken thing"},
        },
    }


def _agent_session_prompted_payload() -> dict[str, Any]:
    payload = _agent_session_created_payload()
    payload["action"] = "prompted"
    payload["agentSession"]["status"] = "awaitingInput"
    payload["agentActivity"] = {
        "id": "activity-1",
        "createdAt": "2026-09-09T12:05:00.000Z",
        "sourceCommentId": "comment-2",
        "content": {"type": "prompt", "body": "also update the changelog"},
    }
    return payload


def test_parses_comment_create_event() -> None:
    event = parse_linear_webhook(_raw(_comment_payload()))

    assert isinstance(event, CommentCreateEvent)
    assert event.webhook_timestamp == 1789000000000
    assert event.data.issue_id == "issue-1"
    assert event.data.user is not None
    assert event.data.user.email == "ramon@example.com"
    assert event.data.issue is not None
    assert [comment.id for comment in event.data.issue.comments] == ["comment-0"]
    assert event.actor is not None
    assert event.actor.name == "Ramon"


def test_parses_agent_session_created_event() -> None:
    event = parse_linear_webhook(_raw(_agent_session_created_payload()))

    assert isinstance(event, AgentSessionEvent)
    assert event.action == "created"
    assert event.agent_session.id == "session-1"
    assert event.agent_session.status == "pending"
    assert event.prompt_context == "The user delegated OS-42 to the agent."
    assert [rule.body for rule in event.guidance] == ["Always open a draft PR."]
    assert [comment.id for comment in event.previous_comments] == ["comment-0"]
    assert event.app_user_id == "app-user-1"
    assert event.agent_activity is None


def test_parses_agent_session_prompted_event() -> None:
    event = parse_linear_webhook(_raw(_agent_session_prompted_payload()))

    assert isinstance(event, AgentSessionEvent)
    assert event.action == "prompted"
    assert event.agent_activity is not None
    content = event.agent_activity.content
    assert isinstance(content, PromptContent)
    assert content.body == "also update the changelog"
    assert event.agent_activity.ephemeral is False


def test_unknown_type_falls_back_to_the_envelope() -> None:
    event = parse_linear_webhook(
        _raw({"action": "update", "type": "Reaction", "webhookTimestamp": 1789000000000})
    )

    assert type(event) is LinearWebhookEnvelope
    assert event.type == "Reaction"


def test_rejects_a_payload_without_an_envelope() -> None:
    with pytest.raises(ValidationError):
        parse_linear_webhook(b'{"type": "Comment"}')


def test_agent_activity_content_serializes_for_linear() -> None:
    dumps = [
        content.model_dump(by_alias=True, exclude_none=True)
        for content in (
            ThoughtContent(body="checking the failing test"),
            ActionContent(action="Reading", parameter="agent/server.py"),
            ActionContent(action="Reading", parameter="agent/server.py", result="240 lines"),
            ElicitationContent(body="Which branch should I target?"),
            ResponseContent(body="Opened a draft PR."),
            ErrorContent(body="Sandbox unreachable", reason_code="sandbox_unavailable"),
            PromptContent(body="also update the changelog"),
        )
    ]

    assert dumps == [
        {"type": "thought", "body": "checking the failing test"},
        {"type": "action", "action": "Reading", "parameter": "agent/server.py"},
        {
            "type": "action",
            "action": "Reading",
            "parameter": "agent/server.py",
            "result": "240 lines",
        },
        {"type": "elicitation", "body": "Which branch should I target?"},
        {"type": "response", "body": "Opened a draft PR."},
        {"type": "error", "body": "Sandbox unreachable", "reasonCode": "sandbox_unavailable"},
        {"type": "prompt", "body": "also update the changelog"},
    ]
