"""The images a channel message links to, fetched as model content blocks.

As the backend's ``openswe.utils.multimodal`` fetches them (AGENTS.md's exception
for channel input), with this deployment's own guard: every request, redirects
included, goes over HTTPS to an address checked as public (``rt.public_http``).
An image that can't be fetched is left out.
"""

import base64
import logging
import mimetypes
import re
from typing import Final
from urllib.parse import urljoin

import httpx2
from langchain_core.messages.content import (
    ImageContentBlock,
    TextContentBlock,
    create_image_block,
    create_text_block,
)

from rt.public_http import PublicOrigin

logger = logging.getLogger(__name__)

_MAX_IMAGE_BYTES: Final = 10 * 1024 * 1024
_MAX_REDIRECTS: Final = 5
_TIMEOUT: Final = httpx2.Timeout(30.0, connect=10.0)
_SUPPORTED_TYPES: Final = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
_IMAGE_MARKDOWN_RE: Final = re.compile(r"!\[[^\]]*\]\((https?://[^\s)]+)\)")
_IMAGE_URL_RE: Final = re.compile(
    r"(https?://[^\s)]+\.(?:png|jpe?g|gif|webp|bmp|tiff)(?:\?[^\s)]+)?)",
    re.IGNORECASE,
)


def extract_image_urls(text: str) -> list[str]:
    """Image URLs from markdown image syntax and direct image links, each once."""
    if not text:
        return []
    return list(dict.fromkeys([*_IMAGE_MARKDOWN_RE.findall(text), *_IMAGE_URL_RE.findall(text)]))


class _TooLargeError(ValueError):
    pass


async def _get(url: str) -> tuple[httpx2.Response, bytes]:
    """The image at ``url``, following redirects one origin at a time."""
    for _ in range(_MAX_REDIRECTS + 1):
        async with (
            httpx2.AsyncClient(
                transport=PublicOrigin(url, name="image host"),
                timeout=_TIMEOUT,
                follow_redirects=False,
                trust_env=False,
            ) as client,
            client.stream("GET", url) as response,
        ):
            location = response.headers.get("Location")
            if response.is_redirect and location:
                url = urljoin(url, location)
                continue
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > _MAX_IMAGE_BYTES:
                    raise _TooLargeError
            return response, bytes(body)
    raise httpx2.TooManyRedirects("too many redirects")


async def fetch_image_block(url: str) -> ImageContentBlock | TextContentBlock | None:
    """The image at ``url`` as a model content block; ``None`` when it can't be fetched."""
    try:
        response, content = await _get(url)
    except _TooLargeError:
        logger.warning("A linked image exceeds the size limit; skipping it")
        return create_text_block(
            "An attached image was skipped because it exceeded the 10 MiB size limit."
        )
    except Exception as error:  # noqa: BLE001 - a missing image never fails the turn
        logger.warning(
            "Fetching a linked image failed; skipping it",
            extra={"error_type": type(error).__name__},
        )
        return None
    content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
    if not content_type:
        content_type, _ = mimetypes.guess_type(url)
    if content_type not in _SUPPORTED_TYPES:
        logger.warning(
            "A linked image has an unsupported content type; skipping it",
            extra={"content_type": content_type},
        )
        return None
    return create_image_block(
        base64=base64.b64encode(content).decode("ascii"), mime_type=content_type
    )
