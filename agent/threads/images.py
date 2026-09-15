"""Serve images the agent moved out of the conversation into the store."""

import base64
import binascii
import re

from fastapi import HTTPException, Response

from agent.store import get_value
from agent.thread_images import IMAGE_STORE_NAMESPACE, image_owner_threads
from agent.threads.access import _readable_thread_metadata

_IMAGE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SERVABLE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


async def get_dashboard_thread_image(
    thread_id: str, image_id: str, login: str, *, email: str | None = None
) -> Response:
    if not _IMAGE_ID_RE.fullmatch(image_id):
        raise HTTPException(404, "image not found")
    metadata = await _readable_thread_metadata(thread_id, login=login, email=email)
    stored = await get_value(IMAGE_STORE_NAMESPACE, image_id)
    if stored is None:
        raise HTTPException(404, "image not found")
    # Images are keyed globally, so an id from an unrelated thread must not be
    # servable under a thread the caller happens to read.
    owners = image_owner_threads(thread_id, metadata.get("continued_from_thread_id"))
    if stored.get("thread_id") not in owners:
        raise HTTPException(404, "image not found")
    mime_type = stored.get("mime_type")
    encoded = stored.get("base64")
    if mime_type not in _SERVABLE_MIME_TYPES or not isinstance(encoded, str):
        raise HTTPException(404, "image not found")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(404, "image not found") from exc
    return Response(
        content=content,
        media_type=mime_type,
        headers={
            # Image ids are immutable, so the browser can keep them for good.
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
