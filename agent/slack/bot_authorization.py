"""Authorize Slack-bot system threads from saved ownership and the active allowlist."""

from collections.abc import Mapping
from typing import Any

import langgraph_sdk

from agent.github.app import get_github_app_installation_token_with_expiry
from agent.run_config import RunConfig
from agent.slack.allowed_bots import resolve_allowed_slack_bot
from agent.source_context import SourceContext
from agent.utils.json_types import thread_metadata


class BotAuthorizationError(RuntimeError):
    """The Slack bot is not authorized to execute in this thread."""


async def validate_bot_thread(metadata: Mapping[str, Any]) -> bool:
    slack = SourceContext.from_metadata(dict(metadata)).slack_thread
    if slack is None or not slack.triggering_bot_id:
        return False
    if metadata.get("owner_type") != "system" or metadata.get("visibility") != "public":
        raise BotAuthorizationError("Slack bots require a public system thread.")
    bot = await resolve_allowed_slack_bot(
        slack.team_id,
        slack.triggering_bot_id,
        user_id=slack.triggering_user_id,
        app_id=slack.triggering_bot_app_id,
    )
    if bot is None:
        raise BotAuthorizationError("This Slack bot is no longer allowed to run Open SWE.")
    return True


async def authorize_bot_thread(thread_id: str) -> bool:
    metadata = thread_metadata(await langgraph_sdk.get_client().threads.get(thread_id))
    return await validate_bot_thread(metadata)


async def bot_thread_configurable(
    thread_id: str, configurable: Mapping[str, Any]
) -> dict[str, Any]:
    metadata = thread_metadata(await langgraph_sdk.get_client().threads.get(thread_id))
    requested_bot = RunConfig.parse(configurable).slack_thread
    if requested_bot and requested_bot.triggering_bot_id:
        opening = SourceContext.from_metadata(metadata).slack_thread
        if (
            metadata.get("owner_type") != "system"
            or opening is None
            or opening.team_id != requested_bot.team_id
            or opening.triggering_bot_id != requested_bot.triggering_bot_id
        ):
            raise BotAuthorizationError("Slack bot cannot run in a thread with another owner.")
    result = dict(configurable)
    result.pop("slack_bot_thread", None)
    if await validate_bot_thread(metadata):
        result["slack_bot_thread"] = True
        for key in ("github_login", "github_user_id", "user_email", "admin_thread"):
            result.pop(key, None)
    return result


async def bot_installation_token(thread_id: str) -> tuple[str, str | None] | None:
    if not await authorize_bot_thread(thread_id):
        return None
    token, expires_at = await get_github_app_installation_token_with_expiry()
    if not token:
        raise BotAuthorizationError("The Open SWE GitHub App is not configured.")
    return token, expires_at
