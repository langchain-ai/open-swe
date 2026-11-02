"""What Open SWE's Slack channel does with each message the backend handed to MDA.

The backend's ``/webhooks/slack`` gates every Slack message and forwards the ones
this deployment's conversations own, remembering the request. The handler asks
the backend what a message starts (``plan_slack_turn``): it builds the run from
that request, as for its own Slack runs, and signs its context. A mention, a
direct message or an explicit request redirects the thread's work in progress;
any other follow-up waits its turn behind it, as on the backend's own Slack
threads. A concierge DM is one conversation for every Slack thread in it: the
backend names its thread, which the run starts on, so Slack's Stop in each Slack
thread reaches only the runs that thread started.
"""

from managed_deepagents import (
    ChannelRunOptions,
    ChannelRunResult,
    SlackChannelThread,
    SlackMessageEvent,
)
from runtime_api.slack import PLAN_SLACK_TURN, PlanSlackTurn, SlackTurnStart

from rt.intake import ask_backend


async def start_turn(
    event: SlackMessageEvent, thread: SlackChannelThread
) -> ChannelRunResult | None:
    """The run a message starts on its conversation's thread, or ``None``."""
    start = await ask_backend(
        PLAN_SLACK_TURN,
        PlanSlackTurn(
            thread_id=thread.id,
            message_ts=event.message_ts,
            channel_id=event.channel_id,
            thread_ts=event.thread_ts,
        ),
        msg_id=f"msg_{event.delivery_id}",
    )
    return run_result(start)


def run_result(start: SlackTurnStart) -> ChannelRunResult | None:
    """The run the backend built, as the channel starts it, or ``None``."""
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
    if start.thread_id is not None:
        # The DM's one thread. The agent answers in Slack with its own tools, so the
        # run delivers nothing itself.
        options["thread"] = {"mode": "persistent", "id": str(start.thread_id)}
    return (run.input, options)
