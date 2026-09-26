"""Resolve a Slack member to a linked GitHub account."""

import logging
import re

from fastapi import HTTPException

from agent.slack.http import SLACK_REQUEST_ERRORS, SlackClient, slack_bot_members, slack_error
from agent.users import User

logger = logging.getLogger(__name__)

_SLACK_USER_ID = re.compile(r"^[UW][A-Z0-9]{2,99}$")


def _names(member: dict[str, object]) -> set[str]:
    profile = member.get("profile")
    fields = (profile if isinstance(profile, dict) else {}, member)
    return {
        value.strip().casefold()
        for field in fields
        for key in ("display_name", "real_name", "name")
        if isinstance(value := field.get(key), str) and value.strip()
    }


async def slack_lookup_github_user(name_or_id: str) -> dict[str, object]:
    """Find the GitHub login linked to an exact Slack name or member ID."""
    query = name_or_id.strip()
    if not query:
        return {"success": False, "error": "name_or_id is required"}

    if _SLACK_USER_ID.fullmatch(query):
        login = await User.login_for_slack(query)
        return {"success": True, "slack_user_id": query, "github_login": login}

    try:
        matches: list[str] = []
        async with SlackClient.bot() as client:
            async for member in slack_bot_members(client):
                member_id = member.get("id")
                if (
                    isinstance(member_id, str)
                    and _SLACK_USER_ID.fullmatch(member_id)
                    and not member.get("deleted")
                    and not member.get("is_bot")
                    and query.removeprefix("@").casefold() in _names(member)
                ):
                    matches.append(member_id)
    except HTTPException as exc:
        logger.warning("Slack user lookup failed", extra={"status_code": exc.status_code})
        return {"success": False, "error": "Could not list Slack members"}
    except SLACK_REQUEST_ERRORS as exc:
        error = slack_error(exc)
        logger.warning("Slack user lookup failed", extra={"slack_error": error})
        return {"success": False, "error": error}

    if not matches:
        return {"success": True, "github_login": None, "error": "No matching Slack member"}
    if len(matches) > 1:
        return {"success": False, "error": "Ambiguous Slack name", "slack_user_ids": matches}
    login = await User.login_for_slack(matches[0])
    return {"success": True, "slack_user_id": matches[0], "github_login": login}
