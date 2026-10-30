"""GitHub, through Open SWE's own App: every delivery the App's webhook sends.

The App's webhook is this deployment's, and the channel takes every event it
sends, the App's installation events included. The handler decides what each
delivery starts (``rt.github``): an ``@openswe`` mention on an issue starts the coding
agent's run on the issue's conversation here; reviews, pull request comments, CI,
the merge queue and automations are the backend's work, which it asks the backend
to run.
"""

from typing import cast, get_args

from managed_deepagents import (
    GitHubHandlers,
    GitHubInstallationEventKey,
    SupportedEventKey,
    channels,
)
from rt.github import on_event, on_installation_event

channel = channels.github(
    # The App's repository permissions (docs/INSTALLATION.md): its token
    # (``mda/github-token``) is the one a box's GitHub proxy carries where MDA writes it.
    app={
        "permissions": {
            "contents": "write",
            "pull_requests": "write",
            "issues": "write",
            "checks": "write",
            "statuses": "read",
            "security_events": "read",
            "actions": "read",
            "workflows": "write",
        }
    },
    # A new mention redirects the work in progress, as the backend's own dispatch did.
    multitask_strategy="interrupt",
    # The agent's own pushes and pull requests still update reviews, approvals and
    # the merge queue; the handler skips the App's own comments.
    include_own_events=True,
    # Every event the App sends, so each delivery reaches the backend's event log
    # (``listen_events``, analytics); ``rt.github_routes`` decides what each starts.
    on_event=cast(
        GitHubHandlers,
        {
            **dict.fromkeys(get_args(SupportedEventKey), on_event),
            **dict.fromkeys(get_args(GitHubInstallationEventKey), on_installation_event),
        },
    ),
)
