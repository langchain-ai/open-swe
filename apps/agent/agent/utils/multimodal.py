"""Utilities for building multimodal content blocks."""

from __future__ import annotations

import base64
import logging
import mimetypes
import re
from typing import Any

import httpx
from langchain_core.messages.content import create_image_block

logger = logging.getLogger(__name__)

IMAGE_MARKDOWN_RE = re.compile(r"!\[[^\]]*\]\((https?://[^\s)]+)\)")
IMAGE_URL_RE = re.compile(
    r"(https?://[^\s)]+\.(?:png|jpe?g|gif|webp|bmp|tiff)(?:\?[^\s)]+)?)",
    re.IGNORECASE,
)


def extract_image_urls(text: str) -> list[str]:
    """Extract image URLs from markdown image syntax and direct image links."""
    if not text:
        return []

    urls: list[str] = []
    urls.extend(IMAGE_MARKDOWN_RE.findall(text))
    urls.extend(IMAGE_URL_RE.findall(text))

    deduped = _dedupe_urls(urls)
    if deduped:
        logger.debug("Extracted %d image URL(s)", len(deduped))
    return deduped


def strip_image_urls(text: str, image_urls: list[str]) -> str:
    """Remove markdown image links and direct image URLs from text."""
    if not text or not image_urls:
        return text

    logger.debug("Stripping %d image URL(s) from text", len(image_urls))
    text = IMAGE_MARKDOWN_RE.sub("", text)
    for url in image_urls:
        text = text.replace(url, "")
    return text


async def fetch_image_block(
    image_url: str,
    client: httpx.AsyncClient,
    headers: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Fetch image bytes and build an image content block."""
    try:
        logger.debug("Fetching image from %s", image_url)
        response = await client.get(image_url, headers=headers)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
        if not content_type:
            guessed, _ = mimetypes.guess_type(image_url)
            content_type = guessed or "application/octet-stream"

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


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    return deduped
