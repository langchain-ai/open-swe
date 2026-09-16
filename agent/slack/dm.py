"""Bot DMs, optionally run as one session instead of a thread per message.

Off unless the person turns on ``dm_session_enabled`` in their own settings. When
it is on, the conversation is keyed with the same non-message timestamp code
channels and incident channels use, so replies post into the DM instead of
opening a thread and the channel's own history is the session transcript. That
timestamp is also how the rest of the code recognizes the mode: only a DM the
owner enabled ever reaches a run with it.
"""

from agent.dashboard.agent_overrides import profile_dm_session_enabled
from agent.dashboard.profiles import get_profile
from agent.dashboard.user_mappings import login_for_slack_id
from agent.slack.client import SlackChannelContext

DM_SESSION_TS = "0"


def is_dm_channel(channel_context: SlackChannelContext | None) -> bool:
    """Whether Slack reports this channel as a direct message with the bot."""
    return channel_context is not None and channel_context.get("is_im") is True


def is_dm_session(channel_context: SlackChannelContext | None, thread_ts: str) -> bool:
    """Whether this location is a DM running as one session."""
    return is_dm_channel(channel_context) and thread_ts == DM_SESSION_TS


async def dm_session_enabled(slack_user_id: str) -> bool:
    """Whether this person has turned the one-session DM on."""
    if not slack_user_id:
        return False
    login = await login_for_slack_id(slack_user_id)
    if not login:
        return False
    return profile_dm_session_enabled(await get_profile(login))


def dm_thread_title(name: str) -> str:
    """The fixed name of a person's DM thread, or "" until Slack tells us who they are.

    An empty title leaves the thread unnamed rather than naming it after one
    request, so the next message can still name it for the person.
    """
    return f"DMs between Open SWE and {name.strip()}" if name.strip() else ""
