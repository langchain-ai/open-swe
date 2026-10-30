"""GitHub, through Open SWE's own App: every delivery the App's webhook sends.

The App's webhook is this deployment's. The handler decides what each delivery
starts (``rt.github``): an ``@openswe`` mention on an issue starts the coding
agent's run on the issue's conversation here; reviews, pull request comments, CI,
the merge queue and automations are the backend's work, which it asks the backend
to run.
"""

from managed_deepagents import channels
from rt.github import on_event

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
    on_event={
        "issues.opened": on_event,
        "issues.edited": on_event,
        "issues.reopened": on_event,
        "issue_comment.created": on_event,
        "issue_comment.edited": on_event,
        "pull_request.opened": on_event,
        "pull_request.edited": on_event,
        "pull_request.reopened": on_event,
        "pull_request.ready_for_review": on_event,
        "pull_request.converted_to_draft": on_event,
        "pull_request.synchronize": on_event,
        "pull_request.closed": on_event,
        "pull_request_review.submitted": on_event,
        "pull_request_review.edited": on_event,
        "pull_request_review.dismissed": on_event,
        "pull_request_review_comment.created": on_event,
        "pull_request_review_comment.edited": on_event,
        "push": on_event,
        "status": on_event,
        "check_run.completed": on_event,
        "check_suite.completed": on_event,
        "workflow_run.completed": on_event,
        "merge_group.checks_requested": on_event,
        "merge_group.destroyed": on_event,
    },
)
