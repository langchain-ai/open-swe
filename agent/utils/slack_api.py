"""Slack Web API transport: every HTTP call Open SWE makes to Slack."""

import asyncio
import logging
import re
import time
from typing import Any

import httpx

from ..config import slack_bot_token
from .dashboard_links import dashboard_thread_url
from .http import DEFAULT_HTTP_TIMEOUT
from .langsmith import get_langsmith_trace_url
from .run_usage import RunUsageSummary
from .slack_format import (
    SlackChannelContext,
    append_slack_web_link_footer,
    extract_channel_description_text,
    format_trace_reply,
    normalize_slack_channel_context,
    parse_slack_ts,
    with_slack_web_link_context_block,
)
from .user_messages import WARNING_ICON

logger = logging.getLogger(__name__)

SLACK_API_BASE_URL = "https://slack.com/api"
SLACK_THREAD_MAX_MESSAGES = 500
SLACK_CHANNEL_INFO_CACHE_TTL_SECONDS = 300

_SLACK_CHANNEL_INFO_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _slack_headers() -> dict[str, str]:
    token = slack_bot_token()
    if not token:
        return {}
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }


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


def _log_automated_warning_sent_to_slack(
    channel_id: str,
    thread_ts: str | None,
    text: str,
) -> None:
    if not text.lstrip().startswith(WARNING_ICON):
        return
    if thread_ts:
        logger.error(
            "Sent automated warning message to Slack thread %s/%s: %s",
            channel_id,
            thread_ts,
            text,
        )
        return
    logger.error("Sent automated warning message to Slack channel %s: %s", channel_id, text)


async def _post_slack_message_with_ts(
    channel_id: str,
    text: str,
    *,
    thread_ts: str | None = None,
    unfurl_links: bool = True,
    unfurl_media: bool = True,
    blocks: list[dict[str, Any]] | None = None,
) -> tuple[str | None, str | None]:
    if not slack_bot_token():
        return None, "missing_slack_bot_token"

    payload: dict[str, Any] = {
        "channel": channel_id,
        "text": text,
        "unfurl_links": unfurl_links,
        "unfurl_media": unfurl_media,
    }
    if thread_ts is not None:
        payload["thread_ts"] = thread_ts
    if blocks:
        payload["blocks"] = blocks

    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as http_client:
        try:
            response = await http_client.post(
                f"{SLACK_API_BASE_URL}/chat.postMessage",
                headers=_slack_headers(),
                json=payload,
            )
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                logger.warning("Slack chat.postMessage rate limited (retry-after=%s)", retry_after)
                if retry_after:
                    return None, f"rate_limited: {retry_after}"
                return None, "rate_limited"
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                error = data.get("error")
                logger.warning("Slack chat.postMessage failed: %s", error)
                if error == "ratelimited":
                    return None, "rate_limited"
                return None, error
            message_ts = data.get("ts")
            if isinstance(message_ts, str) and message_ts:
                _log_automated_warning_sent_to_slack(channel_id, thread_ts, text)
                return message_ts, None
            return None, None
        except httpx.HTTPError as exc:
            logger.exception("Slack chat.postMessage request failed")
            return None, f"http_error: {type(exc).__name__}"


async def post_slack_thread_reply_with_ts(
    channel_id: str,
    thread_ts: str,
    text: str,
    *,
    unfurl_links: bool = True,
    unfurl_media: bool = True,
    blocks: list[dict[str, Any]] | None = None,
    usage: RunUsageSummary | None = None,
    agent_thread_id: str | None = None,
) -> tuple[str | None, str | None]:
    """Post a reply in a Slack thread and return its Slack timestamp and error."""
    dashboard_url = dashboard_thread_url(agent_thread_id) if agent_thread_id else None
    blocks = with_slack_web_link_context_block(text, blocks, dashboard_url, usage)
    text = append_slack_web_link_footer(text, dashboard_url, usage)
    return await _post_slack_message_with_ts(
        channel_id,
        text,
        thread_ts=thread_ts,
        unfurl_links=unfurl_links,
        unfurl_media=unfurl_media,
        blocks=blocks,
    )


async def post_slack_top_level_message_with_ts(
    channel_id: str,
    text: str,
    *,
    unfurl_links: bool = True,
    unfurl_media: bool = True,
    blocks: list[dict[str, Any]] | None = None,
) -> tuple[str | None, str | None]:
    """Post a top-level Slack message and return its timestamp and error."""
    return await _post_slack_message_with_ts(
        channel_id,
        text,
        unfurl_links=unfurl_links,
        unfurl_media=unfurl_media,
        blocks=blocks,
    )


async def post_slack_thread_reply(
    channel_id: str,
    thread_ts: str,
    text: str,
    *,
    blocks: list[dict[str, Any]] | None = None,
    agent_thread_id: str | None = None,
) -> bool:
    """Post a reply in a Slack thread."""
    kwargs: dict[str, Any] = {"blocks": blocks}
    if agent_thread_id is not None:
        kwargs["agent_thread_id"] = agent_thread_id
    message_ts, _ = await post_slack_thread_reply_with_ts(channel_id, thread_ts, text, **kwargs)
    return message_ts is not None


async def update_slack_message(
    channel_id: str,
    message_ts: str,
    text: str,
    *,
    unfurl_links: bool = True,
    unfurl_media: bool = True,
    blocks: list[dict[str, Any]] | None = None,
) -> tuple[bool, str | None]:
    """Update a Slack message and return success plus any Slack error."""
    if not slack_bot_token():
        return False, "missing_slack_bot_token"

    payload: dict[str, Any] = {
        "channel": channel_id,
        "ts": message_ts,
        "text": text,
        "unfurl_links": unfurl_links,
        "unfurl_media": unfurl_media,
    }
    if blocks is not None:
        payload["blocks"] = blocks

    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as http_client:
        try:
            response = await http_client.post(
                f"{SLACK_API_BASE_URL}/chat.update",
                headers=_slack_headers(),
                json=payload,
            )
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                logger.warning("Slack chat.update rate limited (retry-after=%s)", retry_after)
                if retry_after:
                    return False, f"rate_limited: {retry_after}"
                return False, "rate_limited"
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                error = data.get("error")
                logger.warning("Slack chat.update failed: %s", error)
                if error == "ratelimited":
                    return False, "rate_limited"
                return False, error
            return True, None
        except httpx.HTTPError as exc:
            logger.exception("Slack chat.update request failed")
            return False, f"http_error: {type(exc).__name__}"


async def add_slack_reaction(channel_id: str, message_ts: str, emoji: str = "eyes") -> bool:
    """Add a reaction to a Slack message."""
    if not slack_bot_token():
        return False

    payload = {
        "channel": channel_id,
        "timestamp": message_ts,
        "name": emoji,
    }

    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as http_client:
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
    if not slack_bot_token():
        return None

    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as http_client:
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


def _cached_slack_channel_info(channel_id: str) -> dict[str, Any] | None:
    cached = _SLACK_CHANNEL_INFO_CACHE.get(channel_id)
    if not cached:
        return None
    expires_at, channel = cached
    if expires_at <= time.time():
        _SLACK_CHANNEL_INFO_CACHE.pop(channel_id, None)
        return None
    return dict(channel)


def _cache_slack_channel_info(channel_id: str, channel: dict[str, Any]) -> None:
    _SLACK_CHANNEL_INFO_CACHE[channel_id] = (
        time.time() + SLACK_CHANNEL_INFO_CACHE_TTL_SECONDS,
        dict(channel),
    )


async def get_slack_channel_info(channel_id: str) -> dict[str, Any] | None:
    """Get Slack channel details (including topic/purpose) by channel ID."""
    if not slack_bot_token() or not channel_id:
        return None

    cached = _cached_slack_channel_info(channel_id)
    if cached is not None:
        return cached

    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as http_client:
        try:
            response = await http_client.get(
                f"{SLACK_API_BASE_URL}/conversations.info",
                headers=_slack_headers(),
                params={"channel": channel_id},
            )
            if getattr(response, "status_code", None) == 429:
                retry_after = response.headers.get("Retry-After")
                logger.warning(
                    "Slack conversations.info rate limited (retry-after=%s)", retry_after
                )
                return None
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                logger.warning("Slack conversations.info failed: %s", data.get("error"))
                return None
            channel = data.get("channel")
            if isinstance(channel, dict):
                _cache_slack_channel_info(channel_id, channel)
                return dict(channel)
        except httpx.HTTPError:
            logger.exception("Slack conversations.info request failed")
    return None


async def get_slack_channel_context(channel_id: str) -> SlackChannelContext:
    """Fetch and normalize Slack channel context."""
    channel = await get_slack_channel_info(channel_id)
    return normalize_slack_channel_context(channel_id, channel)


async def get_slack_channel_description(channel_id: str) -> str:
    """Fetch a Slack channel's combined topic + purpose text."""
    channel = await get_slack_channel_info(channel_id)
    return extract_channel_description_text(channel)


async def fetch_slack_thread_messages(channel_id: str, thread_ts: str) -> list[dict[str, Any]]:
    """Fetch messages for a Slack thread, keeping the most recent window."""
    if not slack_bot_token():
        return []

    messages: list[dict[str, Any]] = []
    cursor: str | None = None
    truncated = False

    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as http_client:
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

            if len(messages) >= SLACK_THREAD_MAX_MESSAGES:
                truncated = True
                logger.warning(
                    "Slack thread %s/%s capped at %d messages",
                    channel_id,
                    thread_ts,
                    SLACK_THREAD_MAX_MESSAGES,
                )
                break

            response_metadata = payload.get("response_metadata", {})
            cursor = (
                response_metadata.get("next_cursor") if isinstance(response_metadata, dict) else ""
            )
            if not cursor:
                break

    if truncated:
        messages = messages[-SLACK_THREAD_MAX_MESSAGES:]
    messages.sort(key=lambda item: parse_slack_ts(item.get("ts")))
    return messages


async def fetch_slack_thread_message_by_ts(
    channel_id: str, thread_ts: str, message_ts: str
) -> dict[str, Any] | None:
    """Fetch an exact reply from a Slack thread."""
    if not slack_bot_token():
        return None

    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as http_client:
        try:
            response = await http_client.get(
                f"{SLACK_API_BASE_URL}/conversations.replies",
                headers=_slack_headers(),
                params={
                    "channel": channel_id,
                    "ts": thread_ts,
                    "oldest": message_ts,
                    "latest": message_ts,
                    "inclusive": "true",
                    "limit": 1,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError:
            logger.exception(
                "Slack conversations.replies request failed for channel=%s thread=%s ts=%s",
                channel_id,
                thread_ts,
                message_ts,
            )
            return None

    if not payload.get("ok"):
        logger.warning(
            "Slack conversations.replies failed for channel=%s thread=%s ts=%s: %s",
            channel_id,
            thread_ts,
            message_ts,
            payload.get("error"),
        )
        return None
    messages = payload.get("messages", [])
    return next(
        (
            message
            for message in messages
            if isinstance(message, dict) and message.get("ts") == message_ts
        ),
        None,
    )


async def fetch_slack_message_by_ts(channel_id: str, message_ts: str) -> dict[str, Any] | None:
    """Fetch a single Slack message by channel and timestamp."""
    if not slack_bot_token():
        return None

    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as http_client:
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


async def get_slack_permalink(channel_id: str, message_ts: str) -> str | None:
    """Return the public permalink for a Slack message, or None if unavailable."""
    if not slack_bot_token() or not channel_id or not message_ts:
        return None

    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as http_client:
        try:
            response = await http_client.get(
                f"{SLACK_API_BASE_URL}/chat.getPermalink",
                headers=_slack_headers(),
                params={"channel": channel_id, "message_ts": message_ts},
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                logger.warning(
                    "Slack chat.getPermalink failed for channel=%s ts=%s: %s",
                    channel_id,
                    message_ts,
                    data.get("error"),
                )
                return None
            permalink = data.get("permalink")
            return permalink if isinstance(permalink, str) and permalink else None
        except httpx.HTTPError:
            logger.exception(
                "Slack chat.getPermalink request failed for channel=%s ts=%s",
                channel_id,
                message_ts,
            )
    return None


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


async def post_slack_trace_reply(
    channel_id: str, thread_ts: str, thread_id: str, *, include_dashboard_link: bool = True
) -> str | None:
    """Post a trace URL reply in a Slack thread and return its Slack timestamp."""
    trace_url = await get_langsmith_trace_url(thread_id)
    dashboard_url = dashboard_thread_url(thread_id) if include_dashboard_link else None
    message_ts, _ = await post_slack_thread_reply_with_ts(
        channel_id,
        thread_ts,
        format_trace_reply(trace_url, dashboard_url),
        unfurl_links=False,
        unfurl_media=False,
        agent_thread_id=thread_id,
    )
    return message_ts


async def update_slack_trace_reply_for_web_handoff(
    channel_id: str, message_ts: str, thread_id: str
) -> bool:
    """Update the initial Slack trace reply after a dashboard handoff."""
    trace_url = await get_langsmith_trace_url(thread_id)
    dashboard_url = dashboard_thread_url(thread_id)
    ok, error = await update_slack_message(
        channel_id,
        message_ts,
        format_trace_reply(trace_url, dashboard_url, moved_to_web=True),
        unfurl_links=False,
        unfurl_media=False,
    )
    if not ok:
        logger.warning(
            "Failed to update Slack trace reply for web handoff: channel=%s ts=%s error=%s",
            channel_id,
            message_ts,
            error,
        )
    return ok
