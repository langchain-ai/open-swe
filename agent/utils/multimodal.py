"""Finding and fetching the images a message links to."""

import logging
import mimetypes
import os
import posixpath
import re
from urllib.parse import urlparse

import httpx

from agent.media import (
    IMAGE_EXTENSIONS,
    MAX_MEDIA_BYTES,
    MediaRef,
    MediaUpload,
    attach_thread_media,
)
from agent.utils.http import DEFAULT_HTTP_TIMEOUT
from agent.utils.url_safety import request_with_safe_redirects

logger = logging.getLogger(__name__)

IMAGE_MARKDOWN_RE = re.compile(r"!\[[^\]]*\]\((https?://[^\s)]+)\)")
IMAGE_URL_RE = re.compile(
    r"(https?://[^\s)]+\.(?:png|jpe?g|gif|webp|bmp|tiff)(?:\?[^\s)]+)?)",
    re.IGNORECASE,
)


def extract_image_urls(text: str) -> list[str]:
    """Image URLs from markdown image syntax and direct image links, deduplicated."""
    if not text:
        return []
    return dedupe_urls([*IMAGE_MARKDOWN_RE.findall(text), *IMAGE_URL_RE.findall(text)])


def dedupe_urls(urls: list[str]) -> list[str]:
    return list(dict.fromkeys(urls))


def _image_provider(image_url: str) -> str | None:
    host = (urlparse(image_url).hostname or "").lower()
    if host == "uploads.linear.app" or host.endswith(".uploads.linear.app"):
        return "linear"
    if host == "files.slack.com" or host.endswith(".files.slack.com"):
        return "slack"
    return None


def _image_auth_headers_for_url(original_url: str, current_url: str) -> dict[str, str] | None:
    provider = _image_provider(original_url)
    if provider is None or _image_provider(current_url) != provider:
        return None
    if provider == "linear":
        linear_api_key = os.environ.get("LINEAR_API_KEY", "")
        if linear_api_key:
            return {"Authorization": linear_api_key}
    else:
        slack_bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
        if slack_bot_token:
            return {"Authorization": f"Bearer {slack_bot_token}"}
    logger.warning(
        "Provider credential not set; fetching image unauthenticated",
        extra={"image_provider": provider, "image_url": current_url},
    )
    return None


async def fetch_image(image_url: str, client: httpx.AsyncClient) -> MediaUpload | None:
    """Download one linked image, or None when it is unusable."""
    try:
        response, blocked = await request_with_safe_redirects(
            client,
            "GET",
            image_url,
            headers_for_url=_image_auth_headers_for_url,
        )
        if blocked:
            logger.warning(
                "Refusing to fetch image",
                extra={"image_url": image_url, "ssrf_reason": blocked["content"]},
            )
            return None
        if response is None:
            return None
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
        if not content_type:
            content_type = mimetypes.guess_type(image_url)[0] or ""
        if content_type not in IMAGE_EXTENSIONS:
            logger.warning(
                "Skipping image with unsupported content type",
                extra={"image_url": image_url, "content_type": content_type},
            )
            return None
        if len(response.content) > MAX_MEDIA_BYTES:
            logger.warning(
                "Skipping image above the size limit",
                extra={"image_url": image_url, "image_bytes": len(response.content)},
            )
            return None
        logger.info(
            "Fetched image",
            extra={
                "image_url": image_url,
                "content_type": content_type,
                "image_bytes": len(response.content),
            },
        )
        return MediaUpload(
            data=response.content,
            mime_type=content_type,
            file_name=posixpath.basename(urlparse(image_url).path) or None,
            source_url=image_url,
        )
    except Exception:
        logger.exception("Failed to fetch image", extra={"image_url": image_url})
        return None


async def fetch_images(image_urls: list[str]) -> dict[str, MediaUpload]:
    """Fetch every URL that yields a usable image, keyed by URL."""
    if not image_urls:
        return {}
    uploads: dict[str, MediaUpload] = {}
    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
        for image_url in dedupe_urls(image_urls):
            upload = await fetch_image(image_url, client)
            if upload is not None:
                uploads[image_url] = upload
    return uploads


async def attach_linked_images(
    thread_id: str, image_urls: list[str], *, environment_slug: str | None = None
) -> dict[str, MediaRef]:
    """Fetch linked images into the thread's sandbox, keyed by the URL each came from."""
    uploads = await fetch_images(image_urls)
    refs = await attach_thread_media(
        thread_id, list(uploads.values()), environment_slug=environment_slug
    )
    return dict(zip(uploads, refs, strict=True))
