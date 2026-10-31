from __future__ import annotations

from typing import Any

import pytest

from managed_deepagents._channels.github_app_setup import EVENT_PERMISSIONS, github_app_setup
from managed_deepagents._channels.github_schema import SUPPORTED_EVENT_KEYS


def handle(*keys: str) -> dict[str, object]:
    return {key: lambda _event, _thread: None for key in keys}


def test_knows_the_permission_for_every_supported_event() -> None:
    assert set(EVENT_PERMISSIONS) == SUPPORTED_EVENT_KEYS


def test_asks_for_the_permissions_the_handled_events_need() -> None:
    setup = github_app_setup(
        handle("issues.opened", "issue_comment.created", "workflow_run.completed"), None
    )
    assert setup == {
        "events": ["issue_comment.created", "issues.opened", "workflow_run.completed"],
        "webhookEvents": ["issue_comment", "issues", "workflow_run"],
        "permissions": [
            {"name": "actions", "level": "read", "events": ["workflow_run.completed"]},
            {
                "name": "issues",
                "level": "write",
                "events": ["issue_comment.created", "issues.opened"],
            },
            {"name": "metadata", "level": "read", "events": []},
        ],
    }


def test_asks_only_to_read_for_ci_and_push_events() -> None:
    setup = github_app_setup(
        handle("push", "status", "check_run.completed", "check_suite.completed"), None
    )
    assert setup["webhookEvents"] == ["check_run", "check_suite", "push", "status"]
    assert [(p["name"], p["level"]) for p in setup["permissions"]] == [
        ("checks", "read"),
        ("contents", "read"),
        ("metadata", "read"),
        ("statuses", "read"),
    ]


def test_merges_app_permissions_keeping_the_higher_level() -> None:
    handlers = handle("issues.opened", "issue_comment.created", "workflow_run.completed")
    setup = github_app_setup(
        handlers,
        {
            "permissions": {"contents": "read", "issues": "read"},
            "repositories": ["acme/web", "Acme/API", "acme/api"],
        },
    )
    assert setup["repositories"] == ["acme/api", "acme/web"]
    assert setup["permissions"] == [
        {"name": "actions", "level": "read", "events": ["workflow_run.completed"]},
        {"name": "contents", "level": "read", "events": []},
        {
            "name": "issues",
            "level": "write",
            "events": ["issue_comment.created", "issues.opened"],
        },
        {"name": "metadata", "level": "read", "events": []},
    ]

    # A level app.permissions raises is not the events' to explain.
    raised = github_app_setup(handlers, {"permissions": {"actions": "write"}})
    assert raised["permissions"][0] == {"name": "actions", "level": "write", "events": []}


@pytest.mark.parametrize(
    ("app", "message"),
    [
        ({"permissions": {"Contents": "read"}}, "invalid permission name"),
        ({"permissions": {"contents": "admin"}}, "must be read or write"),
        ({"repositories": []}, "at least one repository"),
        ({"repositories": ["api"]}, "must be owner/name"),
        ({"repositories": ["acme/.."]}, "must be owner/name"),
        ({"repositories": ["acme/a b"]}, "must be owner/name"),
        ({"repositories": ["acme/a/b"]}, "must be owner/name"),
        ({"repositories": ["-acme/api"]}, "must be owner/name"),
        ({"repositories": ["acme/api", "other/web"]}, "one owner"),
        ({"public": True}, "unknown GitHub app option public"),
    ],
)
def test_rejects_invalid_app_options(app: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        github_app_setup({}, app)
