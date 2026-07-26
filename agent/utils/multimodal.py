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
from langchain_core.messages.content import (
    ImageContentBlock,
    TextContentBlock,
    create_image_block,
    create_text_block,
)

from .linear import (
    LinearAuth,
    LinearAuthError,
    get_linear_auth,
    invalidate_linear_app_token,
)
from .url_safety import request_with_safe_redirects

logger = logging.getLogger(__name__)

_MAX_IMAGE_BYTES = 10 * 1024 * 1024

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


def vision_not_supported_warning(model_id: str, image_count: int) -> str:
    """Build a prompt-visible warning when images are sent to a text-only model."""
    return (
        f"\n\n**Note:** {image_count} image(s) were attached but the current model "
        f"({model_id}) does not support image input. The images were not included. "
        "Please switch to a vision-enabled model to process images."
    )


def _image_provider(image_url: str) -> str | None:
    host = (urlparse(image_url).hostname or "").lower()
    if host == "uploads.linear.app" or host.endswith(".uploads.linear.app"):
        return "linear"
    if host == "files.slack.com" or host.endswith(".files.slack.com"):
        return "slack"
    return None


def _image_auth_headers_for_url(
    original_url: str,
    current_url: str,
    linear_auth: LinearAuth | None = None,
) -> dict[str, str] | None:
    provider = _image_provider(original_url)
    if provider is None or _image_provider(current_url) != provider:
        return None
    if provider == "linear":
        if urlparse(current_url).scheme != "https":
            return None
        return linear_auth.headers if linear_auth else None

    slack_bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    if slack_bot_token:
        return {"Authorization": f"Bearer {slack_bot_token}"}
    logger.warning(
        "SLACK_BOT_TOKEN not set; cannot authenticate image fetch for %s",
        current_url,
    )
    return None


async def _request_image(
    image_url: str,
    client: httpx.AsyncClient,
) -> tuple[httpx.Response | None, dict[str, Any] | None]:
    linear_auth: LinearAuth | None = None
    if _image_provider(image_url) == "linear":
        try:
            linear_auth = await get_linear_auth()
        except LinearAuthError as exc:
            logger.warning("Cannot authenticate Linear image fetch: %s", exc)
            return None, None

    response: httpx.Response | None = None
    blocked: dict[str, Any] | None = None
    for attempt in range(2):
        last_hop_had_auth = False

        def headers_for_url(
            original_url: str,
            current_url: str,
            auth: LinearAuth | None = linear_auth,
        ) -> dict[str, str] | None:
            nonlocal last_hop_had_auth
            headers = _image_auth_headers_for_url(original_url, current_url, auth)
            last_hop_had_auth = bool(headers and "Authorization" in headers)
            return headers

        response, blocked = await request_with_safe_redirects(
            client,
            "GET",
            image_url,
            headers_for_url=headers_for_url,
        )
        if (
            response is not None
            and response.status_code == 401
            and linear_auth is not None
            and linear_auth.mode == "app"
            and last_hop_had_auth
            and attempt == 0
        ):
            invalidate_linear_app_token()
            try:
                linear_auth = await get_linear_auth()
            except LinearAuthError as exc:
                logger.warning("Cannot renew Linear image authentication: %s", exc)
                return None, None
            continue
        return response, blocked

    return response, blocked


async def fetch_image_block(
    image_url: str,
    client: httpx.AsyncClient,
) -> ImageContentBlock | TextContentBlock | None:
    """Fetch image bytes and build a model content block."""
    try:
        logger.debug("Fetching image from %s", image_url)
        response, blocked = await _request_image(image_url, client)
        if blocked:
            logger.warning(
                "Refusing to fetch image (SSRF guard) %s: %s", image_url, blocked["content"]
            )
            return None
        if response is None:
            return None
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
        if len(response.content) > _MAX_IMAGE_BYTES:
            logger.warning(
                "Image %s exceeds the %d-byte limit; skipping image",
                image_url,
                _MAX_IMAGE_BYTES,
            )
            return create_text_block(
                "An attached image was skipped because it exceeded the 10 MiB size limit."
            )

        encoded = base64.b64encode(response.content).decode("ascii")
        logger.info(
            "Fetched image %s (%s, %d bytes)",
            image_url,
            content_type,
            len(response.content),
        )
        return create_image_block(base64=encoded, mime_type=content_type)
    except Exception:
        if _image_provider(image_url) == "linear":
            logger.warning("Failed to fetch Linear image from %s", image_url)
        else:
            logger.exception("Failed to fetch image from %s", image_url)
        return None


def dedupe_urls(urls: list[str]) -> list[str]:
    return list(dict.fromkeys(urls))
