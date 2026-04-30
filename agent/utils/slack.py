"""Slack API utilities."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import time
from typing import Any

import httpx

from agent.utils.langsmith import get_langsmith_trace_url

logger = logging.getLogger(__name__)

SLACK_API_BASE_URL = "https://slack.com/api"
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")


def _slack_headers() -> dict[str, str]:
    if not SLACK_BOT_TOKEN:
        return {}
    return {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _parse_ts(ts: str | None) -> float:
    try:
        return float(ts or "0")
    except (TypeError, ValueError):
        return 0.0


def _extract_slack_user_name(user: dict[str, Any]) -> str:
    profile = user.get("profile", {})
    if isinstance(profile, dict):
        display_name = profile.get("display_name")
        if isinstance(display_name, str) and display_name.strip():
            return display_name.strip()
        real_name = profile.get("real_name")
        if isinstance(real_name, str) and real_name.strip():
            return real_name.strip()

    real_name = user.get("real_name")
    if isinstance(real_name, str) and real_name.strip():
        return real_name.strip()

    name = user.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()

    return "unknown"


def replace_bot_mention_with_username(text: str, bot_user_id: str, bot_username: str) -> str:
    """Replace Slack bot ID mention token with @username."""
    if not text:
        return ""
    if bot_user_id and bot_username:
        return text.replace(f"<@{bot_user_id}>", f"@{bot_username}")
    return text


def convert_mentions_to_slack_format(text: str) -> str:
    """Convert @Name(USER_ID) patterns to Slack's <@USER_ID> mention format."""
    return re.sub(r"@[^()]+\(([A-Z0-9]+)\)", r"<@\1>", text)


def verify_slack_signature(
    body: bytes,
    timestamp: str,
    signature: str,
    secret: str,
    max_age_seconds: int = 300,
) -> bool:
    """Verify Slack request signature."""
    if not secret:
        logger.warning("SLACK_SIGNING_SECRET is not configured — rejecting webhook request")
        return False
    if not timestamp or not signature:
        return False
    try:
        request_timestamp = int(timestamp)
    except ValueError:
        return False
    if abs(int(time.time()) - request_timestamp) > max_age_seconds:
        return False

    base_string = f"v0:{timestamp}:{body.decode('utf-8', errors='replace')}"
    expected = (
        "v0="
        + hmac.new(secret.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, signature)


def strip_bot_mention(text: str, bot_user_id: str, bot_username: str = "") -> str:
    """Remove bot mention token from Slack text."""
    if not text:
        return ""
    stripped = text
    if bot_user_id:
        stripped = stripped.replace(f"<@{bot_user_id}>", "")
    if bot_username:
        stripped = stripped.replace(f"@{bot_username}", "")
    return stripped.strip()


def select_slack_context_messages(
    messages: list[dict[str, Any]],
    current_message_ts: str,
    bot_user_id: str,
    bot_username: str = "",
) -> tuple[list[dict[str, Any]], str]:
    """Select context from thread start or previous bot mention."""
    if not messages:
        return [], "thread_start"

    current_ts = _parse_ts(current_message_ts)
    ordered = sorted(messages, key=lambda item: _parse_ts(item.get("ts")))
    up_to_current = [item for item in ordered if _parse_ts(item.get("ts")) <= current_ts]
    if not up_to_current:
        up_to_current = ordered

    mention_tokens = []
    if bot_user_id:
        mention_tokens.append(f"<@{bot_user_id}>")
    if bot_username:
        mention_tokens.append(f"@{bot_username}")
    if not mention_tokens:
        return up_to_current, "thread_start"

    last_mention_index = -1
    for index, message in enumerate(up_to_current[:-1]):
        text = message.get("text", "")
        if isinstance(text, str) and any(token in text for token in mention_tokens):
            last_mention_index = index

    if last_mention_index >= 0:
        return up_to_current[last_mention_index:], "last_mention"
    return up_to_current, "thread_start"


def format_slack_messages_for_prompt(
    messages: list[dict[str, Any]],
    user_names_by_id: dict[str, str] | None = None,
    bot_user_id: str = "",
    bot_username: str = "",
) -> str:
    """Format Slack messages into readable prompt text."""
    if not messages:
        return "(no thread messages available)"

    lines: list[str] = []
    for message in messages:
        text = (
            replace_bot_mention_with_username(
                str(message.get("text", "")),
                bot_user_id=bot_user_id,
                bot_username=bot_username,
            ).strip()
            or "[non-text message]"
        )
        user_id = message.get("user")
        if isinstance(user_id, str) and user_id:
            author_name = (user_names_by_id or {}).get(user_id) or user_id
            author = f"@{author_name}({user_id})"
        else:
            bot_profile = message.get("bot_profile", {})
            if isinstance(bot_profile, dict):
                bot_name = bot_profile.get("name") or message.get("username") or "Bot"
            else:
                bot_name = message.get("username") or "Bot"
            author = f"@{bot_name}(bot)"
        lines.append(f"{author}: {text}")
    return "\n".join(lines)


async def post_slack_thread_reply(channel_id: str, thread_ts: str, text: str) -> bool:
    """Post a reply in a Slack thread."""
    if not SLACK_BOT_TOKEN:
        return False

    payload = {
        "channel": channel_id,
        "thread_ts": thread_ts,
        "text": text,
    }

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(
                f"{SLACK_API_BASE_URL}/chat.postMessage",
                headers=_slack_headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                logger.warning("Slack chat.postMessage failed: %s", data.get("error"))
                return False
            return True
        except httpx.HTTPError:
            logger.exception("Slack chat.postMessage request failed")
            return False


async def post_slack_ephemeral_message(
    channel_id: str, user_id: str, text: str, thread_ts: str | None = None
) -> bool:
    """Post an ephemeral message visible only to one user."""
    if not SLACK_BOT_TOKEN:
        return False

    payload: dict[str, str] = {
        "channel": channel_id,
        "user": user_id,
        "text": text,
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(
                f"{SLACK_API_BASE_URL}/chat.postEphemeral",
                headers=_slack_headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                logger.warning("Slack chat.postEphemeral failed: %s", data.get("error"))
                return False
            return True
        except httpx.HTTPError:
            logger.exception("Slack chat.postEphemeral request failed")
            return False


async def add_slack_reaction(channel_id: str, message_ts: str, emoji: str = "eyes") -> bool:
    """Add a reaction to a Slack message."""
    if not SLACK_BOT_TOKEN:
        return False

    payload = {
        "channel": channel_id,
        "timestamp": message_ts,
        "name": emoji,
    }

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(
                f"{SLACK_API_BASE_URL}/reactions.add",
                headers=_slack_headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("ok"):
                return True
            if data.get("error") == "already_reacted":
                return True
            logger.warning("Slack reactions.add failed: %s", data.get("error"))
            return False
        except httpx.HTTPError:
            logger.exception("Slack reactions.add request failed")
            return False


async def get_slack_user_info(user_id: str) -> dict[str, Any] | None:
    """Get Slack user details by user ID."""
    if not SLACK_BOT_TOKEN:
        return None

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.get(
                f"{SLACK_API_BASE_URL}/users.info",
                headers=_slack_headers(),
                params={"user": user_id},
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                logger.warning("Slack users.info failed: %s", data.get("error"))
                return None
            user = data.get("user")
            if isinstance(user, dict):
                return user
        except httpx.HTTPError:
            logger.exception("Slack users.info request failed")
    return None


async def get_slack_user_names(user_ids: list[str]) -> dict[str, str]:
    """Get display names for a set of Slack user IDs."""
    unique_ids = sorted({user_id for user_id in user_ids if isinstance(user_id, str) and user_id})
    if not unique_ids:
        return {}

    user_infos = await asyncio.gather(
        *(get_slack_user_info(user_id) for user_id in unique_ids),
        return_exceptions=True,
    )

    user_names: dict[str, str] = {}
    for user_id, user_info in zip(unique_ids, user_infos, strict=True):
        if isinstance(user_info, dict):
            user_names[user_id] = _extract_slack_user_name(user_info)
        else:
            user_names[user_id] = user_id
    return user_names


async def fetch_slack_thread_messages(channel_id: str, thread_ts: str) -> list[dict[str, Any]]:
    """Fetch all messages for a Slack thread."""
    if not SLACK_BOT_TOKEN:
        return []

    messages: list[dict[str, Any]] = []
    cursor: str | None = None

    async with httpx.AsyncClient() as http_client:
        while True:
            params: dict[str, str | int] = {"channel": channel_id, "ts": thread_ts, "limit": 200}
            if cursor:
                params["cursor"] = cursor

            try:
                response = await http_client.get(
                    f"{SLACK_API_BASE_URL}/conversations.replies",
                    headers=_slack_headers(),
                    params=params,
                )
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPError:
                logger.exception("Slack conversations.replies request failed")
                break

            if not payload.get("ok"):
                logger.warning("Slack conversations.replies failed: %s", payload.get("error"))
                break

            batch = payload.get("messages", [])
            if isinstance(batch, list):
                messages.extend(item for item in batch if isinstance(item, dict))

            response_metadata = payload.get("response_metadata", {})
            cursor = (
                response_metadata.get("next_cursor") if isinstance(response_metadata, dict) else ""
            )
            if not cursor:
                break

    messages.sort(key=lambda item: _parse_ts(item.get("ts")))
    return messages


SLACK_MESSAGE_URL_RE = re.compile(
    r"https?://[a-zA-Z0-9\-]+\.slack\.com/archives/([A-Za-z0-9]+)/p(\d{16})(?:\?[^\s>]*)?"
)


def parse_slack_message_url(url: str) -> tuple[str, str] | None:
    """Parse a Slack message URL into (channel_id, message_ts).

    URL format: https://{workspace}.slack.com/archives/{channel_id}/p{ts_without_dot}
    The 16-digit timestamp becomes {first_10}.{last_6} (e.g. p1776281321762829 -> 1776281321.762829).
    """
    match = SLACK_MESSAGE_URL_RE.search(url)
    if not match:
        return None
    channel_id = match.group(1)
    raw_ts = match.group(2)
    message_ts = f"{raw_ts[:10]}.{raw_ts[10:]}"
    return channel_id, message_ts


def extract_slack_message_urls(text: str) -> list[tuple[str, str, str]]:
    """Extract all Slack message URLs from text.

    Returns list of (full_url, channel_id, message_ts) tuples.
    """
    results: list[tuple[str, str, str]] = []
    for match in SLACK_MESSAGE_URL_RE.finditer(text):
        full_url = match.group(0)
        parsed = parse_slack_message_url(full_url)
        if parsed:
            results.append((full_url, parsed[0], parsed[1]))
    return results


async def fetch_slack_message_by_ts(channel_id: str, message_ts: str) -> dict[str, Any] | None:
    """Fetch a single Slack message by channel and timestamp."""
    if not SLACK_BOT_TOKEN:
        return None

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.get(
                f"{SLACK_API_BASE_URL}/conversations.history",
                headers=_slack_headers(),
                params={
                    "channel": channel_id,
                    "latest": message_ts,
                    "oldest": message_ts,
                    "inclusive": "true",
                    "limit": 1,
                },
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                logger.warning(
                    "Slack conversations.history failed for channel=%s ts=%s: %s",
                    channel_id,
                    message_ts,
                    data.get("error"),
                )
                return None
            messages = data.get("messages", [])
            if messages and isinstance(messages[0], dict):
                return messages[0]
        except httpx.HTTPError:
            logger.exception(
                "Slack conversations.history request failed for channel=%s ts=%s",
                channel_id,
                message_ts,
            )
    return None


async def resolve_slack_message_url(url: str) -> dict[str, Any] | None:
    """Resolve a Slack message URL to its message content.

    Returns a dict with keys: text, user, ts, channel_id, files, thread_ts (if threaded).
    """
    parsed = parse_slack_message_url(url)
    if not parsed:
        return None

    channel_id, message_ts = parsed
    message = await fetch_slack_message_by_ts(channel_id, message_ts)
    if not message:
        return None

    result: dict[str, Any] = {
        "channel_id": channel_id,
        "ts": message.get("ts", message_ts),
        "text": message.get("text", ""),
        "user": message.get("user", ""),
        "files": message.get("files", []),
    }
    if message.get("thread_ts"):
        result["thread_ts"] = message["thread_ts"]
    return result


async def resolve_slack_links_in_context(
    context_messages: list[dict[str, Any]],
    user_names_by_id: dict[str, str],
) -> tuple[str, list[str]]:
    """Resolve cross-posted Slack message links found in context messages.

    Returns (resolved_links_section, image_urls) where resolved_links_section
    is a formatted markdown string for the prompt, and image_urls is a list
    of image URLs from resolved message attachments.
    """
    all_context_text = " ".join(msg.get("text", "") for msg in context_messages)
    slack_links = extract_slack_message_urls(all_context_text)
    if not slack_links:
        return "", []

    resolved_parts: list[str] = []
    image_urls: list[str] = []
    seen_urls: set[str] = set()

    for link_url, _cid, _ts in slack_links:
        if link_url in seen_urls:
            continue
        seen_urls.add(link_url)
        try:
            resolved = await resolve_slack_message_url(link_url)
            if resolved:
                author_id = resolved.get("user", "")
                author = user_names_by_id.get(author_id, author_id)
                if author_id and author == author_id:
                    extra_names = await get_slack_user_names([author_id])
                    author = extra_names.get(author_id, author_id)
                resolved_text = resolved.get("text", "(empty message)")
                resolved_parts.append(
                    f"**{link_url}**\n  Author: {author}\n  Message: {resolved_text}"
                )
                for file_info in resolved.get("files", []):
                    if (
                        isinstance(file_info, dict)
                        and file_info.get("mimetype", "").startswith("image/")
                        and file_info.get("url_private")
                    ):
                        image_urls.append(file_info["url_private"])
            else:
                resolved_parts.append(
                    f"**{link_url}**\n  (Could not fetch — bot may not have access)"
                )
        except Exception:
            logger.exception("Failed to resolve Slack link %s", link_url)
            resolved_parts.append(f"**{link_url}**\n  (Error resolving link)")

    resolved_links_section = ""
    if resolved_parts:
        resolved_links_section = "\n\n## Cross-posted Slack Messages\n" + "\n\n".join(
            resolved_parts
        )

    return resolved_links_section, image_urls


async def post_slack_trace_reply(channel_id: str, thread_ts: str, thread_id: str) -> None:
    """Post a trace URL reply in a Slack thread."""
    trace_url = get_langsmith_trace_url(thread_id)
    if trace_url:
        await post_slack_thread_reply(
            channel_id, thread_ts, f"Working on it! <{trace_url}|View trace>"
        )
    else:
        await post_slack_thread_reply(channel_id, thread_ts, "Working on it!")
