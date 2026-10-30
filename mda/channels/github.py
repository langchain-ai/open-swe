"""GitHub, through Open SWE's own App: @openswe mentions on issues.

The App's webhook stays at the backend's ``/webhooks/github``, which forwards the
issue events that would start a run here as GitHub sent them. Pull requests,
reviews, CI and automations stay on the backend.
"""

from managed_deepagents import channels
from rt.intake import backend_endpoint

# The backend decides what each mention starts, and whether one on a thread that
# waits on an interrupt gets the notice that it does.
backend = backend_endpoint("/github/channel/events")

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
            "security_events": "read",
            "actions": "read",
            "workflows": "write",
        }
    },
    # A new mention redirects the work in progress, as the backend's own dispatch did.
    multitask_strategy="interrupt",
    on_event={
        "issues.opened": backend,
        "issues.edited": backend,
        "issues.reopened": backend,
        "issue_comment.created": backend,
        "issue_comment.edited": backend,
    },
)
