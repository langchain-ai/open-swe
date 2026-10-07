"""Resolve people's dashboard photos from their linked Slack identity."""

import logging

from openswe.slack.users import SlackUser
from openswe.users.models import User

logger = logging.getLogger(__name__)


async def slack_profile(user: User | None) -> SlackUser | None:
    if user is None or not user.slack_user_id:
        return None
    return await SlackUser.load(user.slack_user_id)


async def avatar_for_login(login: str) -> str:
    try:
        profile = await slack_profile(await User.for_login("github", login))
        return profile.avatar_url if profile is not None else ""
    except Exception:
        logger.warning(
            "Could not resolve Slack avatar", extra={"github_login": login}, exc_info=True
        )
        return ""
