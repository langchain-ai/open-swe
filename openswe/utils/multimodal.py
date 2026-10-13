"""Finding linked files in message text and downloading them as attachments."""

import logging
import posixpath
import re
from urllib.parse import urlparse

import httpx2

from openswe.attachments import Attachment
from openswe.config import ENV
from openswe.utils.url_safety import request_with_safe_redirects

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


def _file_host(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    if host == "uploads.linear.app" or host.endswith(".uploads.linear.app"):
        return "linear"
    if host == "files.slack.com" or host.endswith(".files.slack.com"):
        return "slack"
    return None


def _auth_headers_for_url(original_url: str, current_url: str) -> dict[str, str] | None:
    provider = _file_host(original_url)
    if provider is None or _file_host(current_url) != provider:
        return None
    if provider == "slack":
        slack_bot_token = ENV.SLACK_BOT_TOKEN.get()
        if slack_bot_token:
            return {"Authorization": f"Bearer {slack_bot_token}"}
        logger.warning(
            "SLACK_BOT_TOKEN not set; cannot authenticate attachment fetch for %s",
            current_url,
        )
    return None


async def fetch_attachment(url: str, client: httpx2.AsyncClient) -> Attachment | None:
    """Download a linked file as an attachment; ``None`` when it cannot be attached."""
    try:
        response = await request_with_safe_redirects(
            client,
            "GET",
            url,
            headers_for_url=_auth_headers_for_url,
        )
        response.raise_for_status()
        return Attachment.of(
            response.content,
            mime_type=response.headers.get("Content-Type"),
            file_name=posixpath.basename(urlparse(url).path) or None,
        )
    except Exception:
        logger.warning(
            "Could not attach a linked file", exc_info=True, extra={"attachment_url": url}
        )
        return None


def dedupe_urls(urls: list[str]) -> list[str]:
    return list(dict.fromkeys(urls))
