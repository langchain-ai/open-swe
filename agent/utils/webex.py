"""Webex Messaging API utilities."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any

import httpx

from agent.utils.langsmith import get_langsmith_trace_url

logger = logging.getLogger(__name__)

WEBEX_API_BASE_URL = "https://webexapis.com/v1"
WEBEX_BOT_TOKEN = os.environ.get("WEBEX_BOT_TOKEN", "")
WEBEX_BOT_EMAIL = os.environ.get("WEBEX_BOT_EMAIL", "")


def _webex_headers() -> dict[str, str]:
    if not WEBEX_BOT_TOKEN:
        return {}
    return {
        "Authorization": f"Bearer {WEBEX_BOT_TOKEN}",
        "Content-Type": "application/json",
    }


def verify_webex_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify Webex webhook signature (HMAC-SHA1 via X-Spark-Signature header)."""
    if not secret:
        logger.warning("WEBEX_WEBHOOK_SECRET is not configured — rejecting webhook request")
        return False
    if not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
    return hmac.compare_digest(expected, signature)


async def fetch_webex_message(message_id: str) -> dict[str, Any] | None:
    """Fetch a single message by ID.

    Webex webhook payloads omit the message text (encrypted at rest).
    This call retrieves the full message including text content.
    """
    if not WEBEX_BOT_TOKEN:
        return None

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{WEBEX_API_BASE_URL}/messages/{message_id}",
                headers=_webex_headers(),
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError:
            logger.exception("Failed to fetch Webex message %s", message_id)
            return None


async def post_webex_message(
    room_id: str,
    text: str,
    parent_id: str | None = None,
) -> bool:
    """Post a message to a Webex room, optionally as a thread reply."""
    if not WEBEX_BOT_TOKEN:
        return False

    payload: dict[str, str] = {
        "roomId": room_id,
        "markdown": text,
    }
    if parent_id:
        payload["parentId"] = parent_id

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{WEBEX_API_BASE_URL}/messages",
                headers=_webex_headers(),
                json=payload,
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError:
            logger.exception("Failed to post Webex message to room %s", room_id)
            return False


async def get_webex_person(person_id: str) -> dict[str, Any] | None:
    """Fetch person details (displayName, emails, etc.) by person ID."""
    if not WEBEX_BOT_TOKEN:
        return None

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{WEBEX_API_BASE_URL}/people/{person_id}",
                headers=_webex_headers(),
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError:
            logger.exception("Failed to fetch Webex person %s", person_id)
            return None


async def fetch_webex_thread_messages(
    room_id: str,
    parent_id: str,
    max_messages: int = 50,
) -> list[dict[str, Any]]:
    """Fetch messages in a Webex thread (replies to a parent message)."""
    if not WEBEX_BOT_TOKEN:
        return []

    params: dict[str, str | int] = {
        "roomId": room_id,
        "parentId": parent_id,
        "max": max_messages,
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{WEBEX_API_BASE_URL}/messages",
                headers=_webex_headers(),
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            items = data.get("items", [])
            if isinstance(items, list):
                return items
        except httpx.HTTPError:
            logger.exception("Failed to fetch Webex thread messages for room %s", room_id)
    return []


def strip_bot_mention(text: str) -> str:
    """Remove bot mention tokens from Webex message text."""
    if not text:
        return ""
    stripped = text
    if WEBEX_BOT_EMAIL:
        stripped = stripped.replace(WEBEX_BOT_EMAIL, "")
    bot_name = WEBEX_BOT_EMAIL.split("@")[0] if WEBEX_BOT_EMAIL else ""
    if bot_name:
        stripped = stripped.replace(f"@{bot_name}", "")
        stripped = stripped.replace(bot_name, "")
    return stripped.strip()


def format_webex_messages_for_prompt(
    messages: list[dict[str, Any]],
) -> str:
    """Format Webex thread messages into readable prompt text."""
    if not messages:
        return "(no thread messages available)"

    lines: list[str] = []
    for message in messages:
        text = message.get("text", "").strip() or "[non-text message]"
        person_email = message.get("personEmail", "unknown")
        lines.append(f"{person_email}: {text}")
    return "\n".join(lines)


async def post_webex_trace_reply(
    room_id: str,
    parent_id: str | None,
    run_id: str,
) -> None:
    """Post a trace URL reply in a Webex thread."""
    trace_url = get_langsmith_trace_url(run_id)
    if trace_url:
        await post_webex_message(
            room_id,
            f"Working on it! [View trace]({trace_url})",
            parent_id=parent_id,
        )
