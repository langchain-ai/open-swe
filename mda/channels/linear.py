"""Linear, through Open SWE's app: mentioning or delegating an issue to it opens a session."""

from managed_deepagents import channels
from rt.linear import LINEAR

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
    on_event={
        "AgentSessionEvent.created": LINEAR.start_run,
        "AgentSessionEvent.prompted": LINEAR.start_run,
        # These start no conversation: automations and event listeners read them.
        "Issue.create": LINEAR.forward,
        "Issue.update": LINEAR.forward,
        "Comment.create": LINEAR.forward,
    },
)
