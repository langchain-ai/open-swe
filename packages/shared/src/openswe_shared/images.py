"""The images a message links to, fetched as model content blocks."""

import base64
import logging
import mimetypes
import re

import httpx2
from langchain_core.messages.content import (
    ImageContentBlock,
    TextContentBlock,
    create_image_block,
    create_text_block,
)

from openswe_shared.url_safety import HeadersForUrl, request_with_safe_redirects

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


async def fetch_image_block(
    image_url: str,
    client: httpx2.AsyncClient,
    *,
    headers_for_url: HeadersForUrl | None = None,
) -> ImageContentBlock | TextContentBlock | None:
    """Fetch image bytes and build a model content block.

    ``headers_for_url`` authenticates a host that needs it, such as Slack's file
    host; it is asked again for each redirect, never carrying them to another origin.
    """
    try:
        logger.debug("Fetching image from %s", image_url)
        response = await request_with_safe_redirects(
            client,
            "GET",
            image_url,
            headers_for_url=headers_for_url,
        )
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
        logger.exception("Failed to fetch image from %s", image_url)
        return None


def dedupe_urls(urls: list[str]) -> list[str]:
    return list(dict.fromkeys(urls))
