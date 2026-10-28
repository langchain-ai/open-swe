from __future__ import annotations

from typing import Any

import pytest

from managed_deepagents._channels.linear_app_setup import EVENT_RESOURCE_TYPES, linear_app_setup
from managed_deepagents._channels.linear_schema import SUPPORTED_EVENT_KEYS


def handle(*keys: str) -> dict[str, object]:
    return {key: lambda _event, _thread: "x" for key in keys}


def test_knows_the_resource_type_for_every_supported_event() -> None:
    assert set(EVENT_RESOURCE_TYPES) == SUPPORTED_EVENT_KEYS


def test_asks_for_session_scopes_and_events_while_sessions_are_on() -> None:
    setup = linear_app_setup(handle("Issue.update"), None, sessions=True)
    assert setup["events"] == [
        "AgentSessionEvent.created",
        "AgentSessionEvent.prompted",
        "Issue.update",
    ]
    assert setup["webhookResourceTypes"] == [
        "AgentSessionEvent",
        "Issue",
        "OAuthAuthorization",
        "PermissionChange",
    ]
    assert setup["scopes"] == ["read", "write", "app:mentionable", "app:assignable"]
    assert "initiative:write" in setup["allowedScopes"]


def test_asks_only_to_read_and_write_without_sessions() -> None:
    setup = linear_app_setup(handle("Comment.create"), None, sessions=False)
    assert setup["events"] == ["Comment.create"]
    assert setup["sessions"] is False
    assert setup["webhookResourceTypes"] == ["Comment", "OAuthAuthorization"]
    assert setup["scopes"] == ["read", "write"]


def test_asks_for_session_scopes_when_a_session_event_is_handled_without_sessions() -> None:
    setup = linear_app_setup(handle("AgentSessionEvent.created"), None, sessions=False)
    assert setup["events"] == ["AgentSessionEvent.created"]
    assert setup["sessions"] is True
    assert setup["webhookResourceTypes"] == [
        "AgentSessionEvent",
        "OAuthAuthorization",
        "PermissionChange",
    ]
    assert setup["scopes"] == ["read", "write", "app:mentionable", "app:assignable"]


@pytest.mark.parametrize(
    ("app", "message"),
    [
        ({"teams": []}, "must list team keys"),
        ({"teams": [f"T{i}" for i in range(101)]}, "at most 100"),
        ({"teams": ["ENG-1"]}, "invalid team key 'ENG-1'"),
        ({"teams": ["A" * 17]}, "invalid team key"),
        ({"name": "x"}, r"app\.name"),
        ({"name": "My Linear bot"}, r"app\.name"),
        ({"description": " "}, r"app\.description"),
        ({"team": ["ENG"]}, "unknown Linear app option team"),
    ],
)
def test_rejects_invalid_app_options(app: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        linear_app_setup({}, app, sessions=True)
