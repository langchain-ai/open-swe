"""Fill in the display names the legacy import never had.

The legacy ``user_mappings`` import created users with only a GitHub login and
a Slack identity, so most rows pre-dating dashboard sign in carry an empty
``display_name``. Every Slack request already resolves the sender's profile
name for the prompt context; this persists it, once, when the table does not
have a name yet. A name from the dashboard GitHub sign in is the person's own
claim on their account and is never overwritten.
"""

import logging

from agent.users.models import User

logger = logging.getLogger(__name__)


async def persist_display_name(slack_user_id: str, name: str, *, team_id: str = "") -> None:
    """Give the person behind ``slack_user_id`` their Slack name, if they are nameless.

    Known values win and empty ones leave what is stored alone (see
    ``User._known``): a missing Slack profile or an empty ``name`` writes
    nothing, and a stored display name is kept.
    """
    name = name.strip()
    if not name or name == "unknown" or not slack_user_id:
        return
    user = await User.for_identity("slack", slack_user_id)
    if user is None or user.display_name:
        return
    await user.link("slack", slack_user_id, team_id=team_id)
    await user.rename(name)
    logger.info(
        "Backfilled a display name from Slack",
        extra={"slack_user_id": slack_user_id, "user_id": str(user.id)},
    )
