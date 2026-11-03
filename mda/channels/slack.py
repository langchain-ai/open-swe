"""Slack, through Open SWE's own Slack app: the conversations the backend hands to MDA.

The app keeps its URLs at the backend's ``/webhooks/slack``, which forwards the new
conversations this channel can start (mentions, including allowed bots', DMs,
kitchen channels, new concierge DMs), everything later in them and the clicks there, to LangSmith Trigger Server
exactly as Slack sent it. This channel owns those
conversations' threads; its handler asks the backend what each message starts, and
whether it redirects the work in progress or waits its turn (``rt.slack``), and what a
click starts (``rt.slack_clicks``). The backend
gated the message already. The agent posts its own replies with Open SWE's Slack tools,
so the channel posts nothing itself.
"""

from managed_deepagents import channels
from rt.intake import backend_endpoint
from rt.slack import start_turn
from rt.slack_clicks import start_click, start_shortcut

channel = channels.slack(
    # The backend decides what reaches the channel, so every forwarded message counts.
    trigger_mode="all",
    auto_post=False,
    # No 👀 on the message that starts a run.
    reactions=False,
    # Trigger Server forwards a bot's mention; the backend hands over only the bots on
    # Open SWE's allow-list.
    allow_bot_triggers=True,
    # The handler picks each message's strategy; anything else redirects the work in progress.
    multitask_strategy="interrupt",
    # Trigger Server marks what comes from a code channel, and hands over a click in one of
    # its views, which names no Slack thread. The channel's session runs on the thread that
    # opened it, which this channel doesn't own, so the backend acts on those clicks.
    code_channel=True,
    on_event={
        "mention": start_turn,
        "directMessage": start_turn,
        "message": start_turn,
        "interaction": start_click,
        # Explain this: Trigger Server forwards message shortcuts as Slack sends them.
        "shortcut": start_shortcut,
        # A channel's own changes keep the backend's channel, incident and review guide
        # records, as /webhooks/slack does; they start no run.
        "channel": backend_endpoint("/mda/slack/channel-events"),
    },
)
