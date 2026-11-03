"""What Open SWE's Slack channel does with each message the backend handed to MDA.

The backend's ``/webhooks/slack`` gates every Slack message and forwards the ones
this deployment's conversations own, remembering the request. The handler asks
the backend what a message starts (``plan_slack_turn``): it builds the run from
that request, as for its own Slack runs, and signs its context. A mention, a
direct message or an explicit request redirects the thread's work in progress;
any other follow-up waits its turn behind it, as on the backend's own Slack
threads. A concierge DM's runs go on the DM's one thread, and a `/btw` question's or a
new breakout's on one of its own, which the backend names.
When the backend starts nothing and says why, such as a person who must sign in to
Open SWE first, the handler replies with it in the Slack thread.
"""

from typing import Final

from managed_deepagents import (
    ChannelReplyResult,
    ChannelRun,
    ChannelRunResult,
    SlackChannelThread,
    SlackMessageEvent,
    reply,
)
from runtime_api.slack import PLAN_SLACK_TURN, PlanSlackTurn, SlackTurnDeclined, SlackTurnStart

from rt.intake import ask_backend

EXTERNAL_CHANNEL: Final = "Open SWE does not operate in channels with external participants."


def decline_text(declined: SlackTurnDeclined) -> str:
    """What the Slack thread hears when the backend starts nothing for a message."""
    if declined.reason == "external_channel":
        return EXTERNAL_CHANNEL
    # Without a dashboard URL the text names the settings page, since the person
    # must still be told why nothing started.
    settings = (
        f"<{declined.settings_url}|your Open SWE settings>"
        if declined.settings_url
        else "your Open SWE settings"
    )
    if declined.reason == "sign_in_again":
        return (
            "🔐 Your GitHub sign-in is no longer valid, so I can't resolve your GitHub "
            f"account. Re-connect it in {settings}, then tag me again."
        )
    return (
        "👋 I couldn't resolve your GitHub account from Slack. Sign in with GitHub and "
        f"connect your Slack account in {settings}, then tag me again."
    )


async def start_turn(
    event: SlackMessageEvent, thread: SlackChannelThread
) -> ChannelRunResult | ChannelReplyResult | None:
    """The run a message starts on its conversation's thread, the reply that says why
    it starts none, or ``None``."""
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
    if start.declined is not None:
        return reply(decline_text(start.declined))
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
