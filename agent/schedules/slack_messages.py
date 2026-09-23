"""Regex matching for automations triggered by top-level Slack channel posts."""

import logging
import re
from collections.abc import Mapping
from typing import Any, TypedDict

import re2
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from agent.slack.allowed_bots import resolve_allowed_slack_bot
from agent.slack.client import (
    SlackChannelHistoryError,
    fetch_slack_channel_history,
    is_own_slack_message,
)
from agent.webhooks.common import SLACK_BOT_USER_ID

logger = logging.getLogger(__name__)

SLACK_CHANNEL_ID_RE = re.compile(r"^[CG][A-Z0-9]{8,}$")
MESSAGE_PATTERN_MAX_LENGTH = 500
_MATCH_TEXT_MAX_CHARS = 20_000
_PREVIEW_TEXT_MAX_CHARS = 300
_PREVIEW_DEFAULT_DAYS = 7
_PREVIEW_MAX_DAYS = 30
_PREVIEW_MAX_MESSAGES = 200
_TRIGGERING_SUBTYPES = frozenset({"", "bot_message", "file_share"})


class SlackMessageMatch(TypedDict):
    ts: str
    user: str
    text: str
    permalink: str


class SlackMessagePreview(TypedDict):
    channel_id: str
    pattern: str
    days: int
    scanned: int
    matches: list[SlackMessageMatch]


def compile_message_pattern(pattern: str) -> re2._Regexp:
    try:
        return re2.compile(pattern)
    except re2.error as exc:
        raise ValueError(f"message_pattern is not a valid RE2 regular expression: {exc}") from exc


def slack_message_url(channel_id: str, ts: str) -> str:
    return f"https://slack.com/archives/{channel_id}/p{ts.replace('.', '')}"


def is_triggering_message(message: Mapping[str, Any], bot_user_id: str) -> bool:
    """Whether a Slack message is a top-level post that channel automations may react to."""
    ts = message.get("ts")
    thread_ts = message.get("thread_ts")
    subtype = message.get("subtype") or ""
    return (
        isinstance(ts, str)
        and bool(ts)
        and (not thread_ts or thread_ts == ts)
        and subtype in _TRIGGERING_SUBTYPES
        and isinstance(message.get("text"), str)
        and not is_own_slack_message(dict(message), bot_user_id)
    )


async def is_trusted_sender(message: Mapping[str, Any], team_id: str) -> bool:
    """People always count; a bot counts only once a workspace admin has allowed it."""
    bot_id = message.get("bot_id")
    if message.get("subtype") != "bot_message" and not bot_id:
        return True
    user = message.get("user")
    app_id = message.get("app_id")
    return (
        await resolve_allowed_slack_bot(
            team_id,
            bot_id if isinstance(bot_id, str) else "",
            user_id=user if isinstance(user, str) else "",
            app_id=app_id if isinstance(app_id, str) else "",
        )
        is not None
    )


def _message_team_id(message: Mapping[str, Any]) -> str:
    team = message.get("team")
    if isinstance(team, str) and team:
        return team
    profile = message.get("bot_profile")
    team = profile.get("team_id") if isinstance(profile, Mapping) else None
    return team if isinstance(team, str) else ""


def message_matches(pattern: str, text: str) -> bool:
    return compile_message_pattern(pattern).search(text[:_MATCH_TEXT_MAX_CHARS]) is not None


class SlackMessagePreviewBody(BaseModel):
    slack_channel_id: str = Field(min_length=1)
    message_pattern: str = Field(min_length=1, max_length=MESSAGE_PATTERN_MAX_LENGTH)
    days: int = Field(default=_PREVIEW_DEFAULT_DAYS, ge=1, le=_PREVIEW_MAX_DAYS)

    @field_validator("slack_channel_id")
    @classmethod
    def _valid_slack_channel_id(cls, value: str) -> str:
        channel_id = value.strip().upper()
        if not SLACK_CHANNEL_ID_RE.fullmatch(channel_id):
            raise ValueError("slack_channel_id must be a Slack channel ID starting with C or G")
        return channel_id

    @field_validator("message_pattern")
    @classmethod
    def _valid_message_pattern(cls, value: str) -> str:
        compile_message_pattern(value)
        return value


_HISTORY_ERROR_RESPONSES: dict[str, tuple[int, str]] = {
    "not_in_channel": (409, "Invite the Open SWE bot to this channel to preview matches."),
    "channel_not_found": (404, "Slack channel not found, or the Open SWE bot cannot see it."),
    "slack_not_configured": (503, "Slack is not configured for this deployment."),
}


async def preview_message_pattern(body: SlackMessagePreviewBody) -> SlackMessagePreview:
    """Return recent top-level channel posts the pattern would have triggered on."""
    channel_id = body.slack_channel_id
    compiled = compile_message_pattern(body.message_pattern)
    try:
        history = await fetch_slack_channel_history(
            channel_id, days=body.days, limit=_PREVIEW_MAX_MESSAGES
        )
    except SlackChannelHistoryError as exc:
        status, detail = _HISTORY_ERROR_RESPONSES.get(
            exc.code, (502, f"Slack history read failed: {exc.code}")
        )
        logger.warning(
            "Slack message automation preview failed",
            extra={"slack_channel_id": channel_id, "slack_error": exc.code},
        )
        raise HTTPException(status, detail) from exc
    candidates = [m for m in history if is_triggering_message(m, SLACK_BOT_USER_ID)]
    matches: list[SlackMessageMatch] = []
    for message in candidates:
        text = str(message["text"])
        if compiled.search(text[:_MATCH_TEXT_MAX_CHARS]) is None or not await is_trusted_sender(
            message, _message_team_id(message)
        ):
            continue
        ts = str(message["ts"])
        user = message.get("user") or message.get("username") or message.get("bot_id") or ""
        matches.append(
            {
                "ts": ts,
                "user": str(user),
                "text": text[:_PREVIEW_TEXT_MAX_CHARS],
                "permalink": slack_message_url(channel_id, ts),
            }
        )
    return {
        "channel_id": channel_id,
        "pattern": body.message_pattern,
        "days": body.days,
        "scanned": len(candidates),
        "matches": matches,
    }
