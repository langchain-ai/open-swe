from typing import cast

import pytest

from managed_deepagents import channels
from managed_deepagents._channels.github_types import (
    GitHubAppOptions,
    GitHubChannelThread,
    GitHubEvent,
    GitHubHandlers,
    GitHubLabeledIssuePayload,
    GitHubPullRequestPayload,
    GitHubWorkflowPayload,
)


def labeled(event: GitHubEvent[GitHubLabeledIssuePayload], thread: GitHubChannelThread) -> str:
    return event.payload["label"]["name"] + event.payload["issue"]["title"]


def pull_request(event: GitHubEvent[GitHubPullRequestPayload], thread: GitHubChannelThread) -> str:
    return event.payload["pull_request"]["title"]


def workflow(event: GitHubEvent[GitHubWorkflowPayload], thread: GitHubChannelThread) -> str | None:
    return event.payload["workflow_run"]["conclusion"]


def test_typed_handlers_and_app_metadata() -> None:
    channel = channels.github(
        app={"permissions": {"contents": "read"}},
        on_event={
            "issues.labeled": labeled,
            "pull_request.opened": pull_request,
            "workflow_run.completed": workflow,
        },
    )
    # Deploy provides the App's token at this name.
    assert channel.requirements("github")["agentConnections"] == ["mda/github-token"]
    assert channel.requirements("github")["providerConfig"] == {
        "events": ["issues.labeled", "pull_request.opened", "workflow_run.completed"],
        "webhookEvents": ["issues", "pull_request", "workflow_run"],
        "permissions": [
            {"name": "actions", "level": "read", "events": ["workflow_run.completed"]},
            {"name": "contents", "level": "read", "events": []},
            {"name": "issues", "level": "write", "events": ["issues.labeled"]},
            {"name": "metadata", "level": "read", "events": []},
            {"name": "pull_requests", "level": "write", "events": ["pull_request.opened"]},
        ],
    }


def test_unsupported_key() -> None:
    with pytest.raises(ValueError, match="unsupported GitHub event"):
        channels.github(on_event=cast("GitHubHandlers", {"issues.deleted": labeled}))


def test_unknown_app_option_is_rejected_instead_of_dropped() -> None:
    with pytest.raises(ValueError, match="unknown GitHub app option repositores"):
        channels.github(app=cast("GitHubAppOptions", {"repositores": ["acme/api"]}))
