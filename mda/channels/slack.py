"""Slack, through Open SWE's own Slack app: the conversations MDA's channel holds.

The app keeps its URLs at the backend's ``/webhooks/slack``, which forwards the
conversations this channel can start (mentions, including allowed bots', DMs,
kitchen channels, new concierge DMs, ``/btw`` questions and breakouts), everything
later in them and the clicks there, to LangSmith Trigger Server exactly as Slack
sent it. This channel owns those conversations' threads. Its handler decides what
each message starts and builds the turn's prompt, asking the backend only for what
needs Open SWE's records (``rt.slack``), and what a click starts
(``rt.slack_clicks``). The agent posts its own replies with Open SWE's Slack tools,
so the channel posts nothing itself.
"""

from managed_deepagents import channels
from rt.intake import backend_endpoint
from rt.slack import start_edit, start_turn
from rt.slack_clicks import start_click, start_shortcut

channel = channels.slack(
    # Every message reaches the handler, which decides what it starts.
    trigger_mode="all",
    auto_post=False,
    # No 👀 on the message that starts a run.
    reactions=False,
    # Trigger Server forwards a bot's mention; the handler starts runs only for the bots
    # on Open SWE's allow-list.
    allow_bot_triggers=True,
    # The handler picks each message's strategy; anything else redirects the work in progress.
    multitask_strategy="interrupt",
    on_event={
        "mention": start_turn,
        "directMessage": start_turn,
        "message": start_turn,
        # A person's edit of a message the conversation holds: the backend gives the
        # agent the correction. Deletions start nothing, as on the backend.
        "edit": start_edit,
        # Feedback reactions and the summon reaction, which the backend acts on as
        # /webhooks/slack does; they start no run here.
        "reaction": backend_endpoint("/mda/slack/reactions"),
        "interaction": start_click,
        # Explain this: Trigger Server forwards message shortcuts as Slack sends them.
        "shortcut": start_shortcut,
        # A channel's own changes keep the backend's channel, incident and review guide
        # records, as /webhooks/slack does; they start no run.
        "channel": backend_endpoint("/mda/slack/channel-events"),
    },
)
