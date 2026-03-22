"""Utilities for building multimodal content blocks."""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from langchain_core.messages.content import create_image_block

from .url_validation import validate_url

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

    deduped = dedupe_urls(urls)
    if deduped:
        logger.debug("Extracted %d image URL(s)", len(deduped))
    return deduped


async def fetch_image_block(
    image_url: str,
    client: httpx.AsyncClient,
) -> dict[str, Any] | None:
    """Fetch image bytes and build an image content block."""
    result = validate_url(image_url)
    if result is None:
        logger.warning("Rejected unsafe image URL: %s", image_url)
        return None

    resolved_ip, port, hostname = result

    try:
        logger.debug("Fetching image from %s (via %s)", image_url, resolved_ip)
        headers: dict[str, str] = {"Host": hostname}
        if "uploads.linear.app" in image_url:
            linear_api_key = os.environ.get("LINEAR_API_KEY", "")
            if linear_api_key:
                headers["Authorization"] = linear_api_key
            else:
                logger.warning(
                    "LINEAR_API_KEY not set; cannot authenticate image fetch for %s",
                    image_url,
                )

        # Connect to the resolved IP directly to prevent DNS-rebinding
        # (TOCTOU) attacks.  The Host header is set to the original hostname
        # so TLS SNI and virtual-host routing work correctly.
        parsed = urlparse(image_url)
        pinned_netloc = f"{resolved_ip}:{port}" if port not in (80, 443) else resolved_ip
        pinned_url = urlunparse(parsed._replace(netloc=pinned_netloc))

        response = await client.get(pinned_url, headers=headers)
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
