"""What Open SWE's Slack channel does with each message the backend handed to MDA.

The backend's ``/webhooks/slack`` gates every Slack message and forwards the ones
this deployment's conversations own, remembering the request. The handler asks
the backend what a message starts (``plan_slack_turn``): it builds the run from
that request, as for its own Slack runs, and signs its context. A mention, a
direct message or an explicit request redirects the thread's work in progress;
any other follow-up waits its turn behind it, as on the backend's own Slack
threads. A concierge DM's runs go on the DM's one thread, which the backend names.
"""

from managed_deepagents import (
    ChannelRun,
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
    started: ChannelRun = {
        "input": run.input,
        "context": run.context,
        "metadata": run.metadata,
        "multitask_strategy": "interrupt" if start.interrupts else "enqueue",
    }
    if run.webhook:
        started["webhook"] = run.webhook
    if start.thread_id is not None:
        # The agent answers with its own Slack tools, so the run posts nothing (no ``auto_post``).
        started["thread"] = {"mode": "persistent", "id": str(start.thread_id)}
    # A list carries the run's options; a dict alone would be the run's state.
    return [started]
