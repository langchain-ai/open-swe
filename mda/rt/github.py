"""What Open SWE's GitHub channel does with each GitHub delivery, in this deployment.

Every delivery goes to the backend's event log (``record_github_delivery``), which
answers what Open SWE knows about its repository. This side decides what the
delivery starts (``rt.github_routes``), and tells the backend in one call
(``act_on_github_delivery``): the backend's work, such as a review, a CI wakeup or a
pull request comment's run, which the backend runs once per delivery; and for an
``@openswe`` mention on an issue, the coding agent's run on the issue's
conversation, which the backend checks and signs. So a delivery takes at most two calls.
"""

from typing import Final, cast
from uuid import UUID

from managed_deepagents import (
    ChannelRunOptions,
    GitHubChannelThread,
    GitHubEvent,
    GitHubEventResult,
    GitHubPayload,
)
from pydantic import JsonValue
from runtime_api.github import (
    ACT_ON_GITHUB_DELIVERY,
    RECORD_GITHUB_DELIVERY,
    ActOnGitHubDelivery,
    GitHubDelivery,
    GitHubIssueRunRequest,
)

from rt.github_routes import route
from rt.intake import ask_backend

# What a mention starts while the issue's thread waits on an interrupt: the run
# doesn't start, the channel only points the issue to the decision.
ACTS_WHILE_WAITING: Final = "Open SWE was mentioned while the thread waits."


async def on_event[Payload: GitHubPayload](
    event: GitHubEvent[Payload], thread: GitHubChannelThread
) -> GitHubEventResult:
    """Log the delivery, start the backend's work it routes to, and an issue's run."""
    delivery = GitHubDelivery(
        event=event.name,
        delivery_id=event.delivery_id,
        payload=cast(dict[str, JsonValue], event.payload),
    )
    facts = await ask_backend(
        RECORD_GITHUB_DELIVERY, delivery, msg_id=f"msg_{event.delivery_id}_record"
    )
    routed = route(event.name, delivery.payload, facts, from_app=event.from_app)
    if not routed.work and not routed.issue_run:
        return None
    issue_run = (
        GitHubIssueRunRequest(thread_id=UUID(thread.id), waiting=thread.interrupted)
        if routed.issue_run
        else None
    )
    acted = await ask_backend(
        ACT_ON_GITHUB_DELIVERY,
        ActOnGitHubDelivery(
            **delivery.model_dump(),
            work=list(routed.work),
            mentioned=routed.mentioned,
            issue_run=issue_run,
        ),
        msg_id=f"msg_{event.delivery_id}_act",
    )
    planned = acted.issue_run
    if planned is None or not planned.starts:
        return None
    run = planned.run
    if run is None:
        return ACTS_WHILE_WAITING
    options: ChannelRunOptions = {"context": run.context, "metadata": run.metadata}
    if run.webhook:
        options["webhook"] = run.webhook
    return (run.input, options)
