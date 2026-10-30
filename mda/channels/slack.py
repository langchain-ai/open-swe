"""Slack, through Open SWE's own Slack app: the conversations the backend hands to MDA.

The app keeps its URLs at the backend's ``/webhooks/slack``, which forwards the new
conversations this channel can start (people's mentions and DMs), and everything later
in them, to LangSmith Trigger Server exactly as Slack sent it. This channel owns those
conversations' threads; its handler asks the backend what each message starts, and
whether it redirects the work in progress or waits its turn (``rt.slack``). The backend
gated the message already. The agent posts its own replies with Open SWE's Slack tools,
so the channel posts nothing itself.
"""

from managed_deepagents import channels
from rt.slack import start_turn

channel = channels.slack(
    # The backend decides what reaches the channel, so every forwarded message counts.
    trigger_mode="all",
    reply="none",
    # The handler picks each message's strategy; anything else redirects the work in progress.
    multitask_strategy="interrupt",
    on_event={
        "mention": start_turn,
        "directMessage": start_turn,
        "message": start_turn,
    },
)
