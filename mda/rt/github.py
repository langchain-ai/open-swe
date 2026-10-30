"""What Open SWE's GitHub channel does with each GitHub delivery, in this deployment.

Every delivery goes to the backend's event log (``record_github_delivery``), which
answers what Open SWE knows about its repository. This side decides what the
delivery starts (``rt.github_routes``), and tells the backend in one call
(``act_on_github_delivery``): the backend's work, such as a review or a CI wakeup,
which the backend runs once per delivery; whether it may fire automations; a pull
request comment to route; and for an ``@openswe`` mention on an issue, the coding
agent's run on the issue's conversation. The backend checks and signs the runs, and
this side starts them: each automation's on a thread of its own, a comment's on the
pull request's own thread, the issue's on its conversation. A comment's run on the
agent's own thread the backend still starts itself. So a delivery takes at most two calls. An installation event, sent
when the App is installed, removed or suspended or its repositories change, is
logged the same way and drops the backend's cached repository listings.
"""

from typing import Final, cast
from uuid import UUID

from langgraph_sdk.schema import MultitaskStrategy
from managed_deepagents import (
    ChannelRunInput,
    ChannelRunOptions,
    GitHubChannelThread,
    GitHubEvent,
    GitHubEventResult,
    GitHubInstallationEvent,
    GitHubPayload,
)
from pydantic import JsonValue
from runtime_api.github import (
    ACT_ON_GITHUB_DELIVERY,
    RECORD_GITHUB_DELIVERY,
    ActOnGitHubDelivery,
    GitHubDelivery,
    GitHubIssueRun,
    GitHubIssueRunRequest,
    GitHubThreadRun,
    PlannedGitHubIssueRun,
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
    if not (routed.work or routed.comment or routed.automations or routed.issue_run):
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
            comment=routed.comment,
            mentioned=routed.mentioned,
            automations=routed.automations,
            issue_run=issue_run,
        ),
        msg_id=f"msg_{event.delivery_id}_act",
    )
    runs = [_issue_run(acted.issue_run)] if acted.issue_run and acted.issue_run.starts else []
    runs += [_thread_run(run) for run in acted.automations]
    if acted.comment_run is not None:
        # A newer comment replaces the work on an older one, as the backend's dispatch did.
        runs.append(_thread_run(acted.comment_run, multitask_strategy="interrupt"))
    return runs or None


async def on_installation_event(
    event: GitHubInstallationEvent, _thread: GitHubChannelThread
) -> GitHubEventResult:
    """Log the delivery, and start the backend's work it routes to; it starts no run."""
    delivery = GitHubDelivery(
        event=event.name,
        delivery_id=event.delivery_id,
        payload=cast(dict[str, JsonValue], event.payload),
    )
    facts = await ask_backend(
        RECORD_GITHUB_DELIVERY, delivery, msg_id=f"msg_{event.delivery_id}_record"
    )
    routed = route(event.name, delivery.payload, facts, from_app=False)
    if routed.work:
        await ask_backend(
            ACT_ON_GITHUB_DELIVERY,
            ActOnGitHubDelivery(**delivery.model_dump(), work=list(routed.work)),
            msg_id=f"msg_{event.delivery_id}_act",
        )
    return None


def _options(run: GitHubIssueRun | GitHubThreadRun) -> ChannelRunOptions:
    options: ChannelRunOptions = {"context": run.context, "metadata": run.metadata}
    if run.webhook:
        options["webhook"] = run.webhook
    return options


def _issue_run(planned: PlannedGitHubIssueRun) -> tuple[ChannelRunInput, ChannelRunOptions]:
    """The issue's run on its conversation, or, while it waits, what points it to the decision."""
    if planned.run is None:
        return (ACTS_WHILE_WAITING, {})
    return (planned.run.input, _options(planned.run))


def _thread_run(
    run: GitHubThreadRun, *, multitask_strategy: MultitaskStrategy | None = None
) -> tuple[ChannelRunInput, ChannelRunOptions]:
    """A run on the thread the backend planned it on: an automation's, or the pull request's."""
    options: ChannelRunOptions = {
        **_options(run),
        "thread": {"mode": "persistent", "id": str(run.thread_id)},
    }
    if multitask_strategy is not None:
        options["multitask_strategy"] = multitask_strategy
    return (run.input, options)
