"""Let admins correct the link between a Slack member and a GitHub account."""

import logging
import re

from fastapi import HTTPException

from agent.slack.http import SLACK_REQUEST_ERRORS, SlackClient, slack_error
from agent.tools.admin_gate import require_admin
from agent.users import User

logger = logging.getLogger(__name__)

_SLACK_USER_ID = re.compile(r"^[UW][A-Z0-9]{2,99}$")


async def manage_slack_github_mapping(slack_user_id: str, github_login: str) -> dict[str, object]:
    """Link a verified Slack member ID to an existing Open SWE GitHub user."""
    if error := await require_admin("manage Slack-to-GitHub mappings"):
        return {"success": False, "error": error}
    slack_user_id = slack_user_id.strip()
    github_login = github_login.strip()
    if not _SLACK_USER_ID.fullmatch(slack_user_id):
        return {"success": False, "error": "A Slack member ID is required"}
    if not github_login:
        return {"success": False, "error": "A GitHub login is required"}

    user = await User.for_login("github", github_login)
    if user is None or not user.github_login:
        return {"success": False, "error": "GitHub account has not signed in to Open SWE"}

    try:
        async with SlackClient.bot() as client:
            response = await client.users_info(user=slack_user_id)
    except HTTPException as exc:
        logger.warning("Slack member verification failed", extra={"status_code": exc.status_code})
        return {"success": False, "error": "Could not verify Slack member"}
    except SLACK_REQUEST_ERRORS as exc:
        error = slack_error(exc)
        logger.warning("Slack member verification failed", extra={"slack_error": error})
        return {"success": False, "error": error}

    member = response.get("user")
    if (
        not isinstance(member, dict)
        or member.get("id") != slack_user_id
        or member.get("deleted")
        or member.get("is_bot")
    ):
        return {"success": False, "error": "Slack member not found or inactive"}

    previous = await User.login_for_slack(slack_user_id)
    await user.assign_slack_member(slack_user_id)
    return {
        "success": True,
        "slack_user_id": slack_user_id,
        "github_login": user.github_login,
        "previous_github_login": previous,
    }
