"""What Open SWE's Slack channel does with each message the backend handed to MDA.

The backend's ``/webhooks/slack`` gates every Slack message and forwards the ones
this deployment's conversations own, remembering the request. The handler asks
the backend what a message starts (``plan_slack_turn``): it builds the run from
that request, as for its own Slack runs, and signs its context. A mention, a
direct message or an explicit request redirects the thread's work in progress;
any other follow-up waits its turn behind it, as on the backend's own Slack
threads.
"""

from managed_deepagents import (
    ChannelRunOptions,
    ChannelRunResult,
    SlackChannelThread,
    SlackMessageEvent,
)
from runtime_api.slack import PLAN_SLACK_TURN, PlanSlackTurn

from rt.intake import ask_backend


async def start_turn(
    event: SlackMessageEvent, thread: SlackChannelThread
) -> ChannelRunResult | None:
    """The run a message starts on its conversation's thread, or ``None``."""
    start = await ask_backend(
        PLAN_SLACK_TURN,
        PlanSlackTurn(thread_id=thread.id, message_ts=event.message_ts),
        msg_id=f"msg_{event.delivery_id}",
    )
    run = start.run
    if run is None:
        return None
    options: ChannelRunOptions = {
        "context": run.context,
        "metadata": run.metadata,
        "multitask_strategy": "interrupt" if start.interrupts else "enqueue",
    }
    if run.webhook:
        options["webhook"] = run.webhook
    return (run.input, options)
