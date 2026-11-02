"""Linear, through Open SWE's app: mentioning or delegating an issue to it opens a session."""

from managed_deepagents import channels
from rt.intake import backend_endpoint

# The backend decides what each event starts: a session turn starts the run it
# prepares; issue and comment events start nothing, and feed automations and the
# event log.
backend = backend_endpoint("/linear/channel/events")

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
        "AgentSessionEvent.created": backend,
        "AgentSessionEvent.prompted": backend,
        "Issue.create": backend,
        "Issue.update": backend,
        "Comment.create": backend,
    },
)
