"""Utilities for building multimodal content blocks."""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from langchain_core.messages.content import create_image_block

logger = logging.getLogger(__name__)

IMAGE_MARKDOWN_RE = re.compile(r"!\[[^\]]*\]\((https?://[^\s)]+)\)")
IMAGE_URL_RE = re.compile(
    r"(https?://[^\s)]+\.(?:png|jpe?g|gif|webp|bmp|tiff)(?:\?[^\s)]+)?)",
    re.IGNORECASE,
)
GITHUB_ASSET_URL_RE = re.compile(r"(https://github\.com/user-attachments/assets/[a-zA-Z0-9_-]+)")


def extract_image_urls(text: str) -> list[str]:
    """Extract image URLs from markdown image syntax and direct image links."""
    if not text:
        return []

    urls: list[str] = []
    urls.extend(IMAGE_MARKDOWN_RE.findall(text))
    urls.extend(IMAGE_URL_RE.findall(text))
    urls.extend(GITHUB_ASSET_URL_RE.findall(text))

    deduped = dedupe_urls(urls)
    if deduped:
        logger.debug("Extracted %d image URL(s)", len(deduped))
    return deduped


async def fetch_image_block(
    image_url: str,
    client: httpx.AsyncClient,
) -> dict[str, Any] | None:
    """Fetch image bytes and build an image content block."""
    try:
        logger.debug("Fetching image from %s", image_url)
        headers = None
        host = (urlparse(image_url).hostname or "").lower()
        if host == "uploads.linear.app" or host.endswith(".uploads.linear.app"):
            linear_api_key = os.environ.get("LINEAR_API_KEY", "")
            if linear_api_key:
                headers = {"Authorization": linear_api_key}
            else:
                logger.warning(
                    "LINEAR_API_KEY not set; cannot authenticate image fetch for %s",
                    image_url,
                )
        elif host == "files.slack.com" or host.endswith(".files.slack.com"):
            slack_bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
            if slack_bot_token:
                headers = {"Authorization": f"Bearer {slack_bot_token}"}
            else:
                logger.warning(
                    "SLACK_BOT_TOKEN not set; cannot authenticate image fetch for %s",
                    image_url,
                )
        response = await client.get(image_url, headers=headers, follow_redirects=True)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
        if not content_type:
            guessed, _ = mimetypes.guess_type(image_url)
            if not guessed:
                logger.warning(
                    "Could not determine content type for %s; skipping image",
                    image_url,
                )
                return None
            content_type = guessed

        supported_types = {"image/jpeg", "image/png", "image/gif", "image/webp"}
        if content_type not in supported_types:
            logger.warning(
                "Unsupported content type '%s' for %s; skipping image",
                content_type,
                image_url,
            )
            return None

        encoded = base64.b64encode(response.content).decode("ascii")
        logger.info(
            "Fetched image %s (%s, %d bytes)",
            image_url,
            content_type,
            len(response.content),
        )
        return create_image_block(base64=encoded, mime_type=content_type)
    except Exception:
        logger.exception("Failed to fetch image from %s", image_url)
        return None


def dedupe_urls(urls: list[str]) -> list[str]:
    return list(dict.fromkeys(urls))
