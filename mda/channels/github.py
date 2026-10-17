"""GitHub, through Open SWE's own App: @openswe mentions on issues.

The App's webhook stays at the backend's ``/webhooks/github``, which forwards the
issue events of the repositories ``GITHUB_ISSUES_VIA_MDA_CHANNEL`` names here as
GitHub sent them. Pull requests, reviews, CI and automations stay on the backend.
"""

from managed_deepagents import channels
from rt import intake
from rt.github import GITHUB

channel = channels.github(
    # A new mention redirects the work in progress, as the backend's own dispatch did.
    multitask_strategy="interrupt",
    on_event={
        "issues.opened": GITHUB.start_run,
        "issues.edited": GITHUB.start_run,
        "issues.reopened": GITHUB.start_run,
        "issue_comment.created": GITHUB.start_run,
        "issue_comment.edited": GITHUB.start_run,
    },
    # The channel starts the runs, so no run-complete webhook follows them.
    on_settled=intake.settled,
)
