"""Linear, through Open SWE's app: mentioning or delegating an issue to it opens a session.

The handlers run here (``rt.linear``): a session turn starts the coding agent's run
on the session's thread, with what the backend knows of its author, repository and
workspace; issue and comment events start nothing, and feed the backend's
automations and event log.
"""

from managed_deepagents import channels
from rt.linear import record, start_session

channel = channels.linear(
    app={
        "name": "Open SWE",
        "description": (
            "Open SWE's coding agent: mention it or delegate an issue to it, and it works "
            "on the issue's repository and answers in the session."
        ),
    },
    # A new message in the session redirects the work in progress, as before.
    multitask_strategy="interrupt",
    # Issues other integrations create or change (GitHub sync, Sentry, ...) still feed
    # automations and the event log; they start no run themselves. The channel always
    # ignores the app's own changes, so its comments can't trigger it.
    ignore_bots=False,
    on_event={
        "AgentSessionEvent.created": start_session,
        "AgentSessionEvent.prompted": start_session,
        "Issue.create": record,
        "Issue.update": record,
        "Comment.create": record,
    },
)
