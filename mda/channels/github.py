"""GitHub, through Open SWE's own App: every delivery the App's webhook sends.

The App's webhook is this deployment's. The channel forwards every delivery to the
backend as GitHub sent it (``forward_to``, the backend's ``/mda/github/deliveries``),
which logs it and runs its own work: reviews, walkthroughs, human review, the merge
queue, CI and the pull request state the dashboard shows. GitHub hears back only
once the backend has the delivery. The handler (``rt.github``) takes only the
deliveries that may start an agent run: an ``@openswe`` mention on an issue starts
the coding agent's run on the issue's conversation here, a pull request comment is
the backend's to route, and an issue or pull request may fire automations.
"""

from typing import cast

from managed_deepagents import GitHubHandlers, channels
from rt.github import on_event
from rt.github_routes import AGENT_EVENT_KEYS
from rt.intake import backend_endpoint

channel = channels.github(
    # The App's repository permissions (docs/INSTALLATION.md): its token
    # (``mda/github-token``) is the one a sandbox's GitHub proxy carries where MDA writes it.
    app={
        "permissions": {
            "contents": "write",
            "pull_requests": "write",
            "issues": "write",
            "checks": "write",
            "statuses": "read",
            "security_events": "write",
            "actions": "read",
            "workflows": "write",
        }
    },
    # A new mention redirects the work in progress, as the backend's own dispatch did.
    multitask_strategy="interrupt",
    # The agent's own pull requests still fire automations; the handler skips the
    # App's own comments, so the agent doesn't answer itself.
    include_own_events=True,
    # Every delivery, typed or not, the agent's own included, before any handler runs.
    forward_to=[backend_endpoint("/mda/github/deliveries")],
    on_event=cast(GitHubHandlers, dict.fromkeys(AGENT_EVENT_KEYS, on_event)),
)
