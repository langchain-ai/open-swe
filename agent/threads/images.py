"""Serve images the agent moved out of the conversation into the store."""

import base64
import binascii
import re

from fastapi import HTTPException, Response

from agent.middleware.image_offload import IMAGE_STORE_NAMESPACE
from agent.store import get_value
from agent.threads.access import _readable_thread_metadata

_IMAGE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SERVABLE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_FILE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _content_disposition(file_name: object) -> str:
    name = _FILE_NAME_RE.sub("-", file_name).strip("-") if isinstance(file_name, str) else ""
    return f'inline; filename="{name or "image"}"'


async def get_dashboard_thread_image(
    thread_id: str, image_id: str, login: str, *, email: str | None = None
) -> Response:
    if not _IMAGE_ID_RE.fullmatch(image_id):
        raise HTTPException(404, "image not found")
    await _readable_thread_metadata(thread_id, login=login, email=email)
    stored = await get_value(IMAGE_STORE_NAMESPACE, image_id)
    if stored is None:
        raise HTTPException(404, "image not found")
    owner = stored.get("thread_id")
    if not isinstance(owner, str) or not owner:
        raise HTTPException(404, "image not found")
    # A thread copied from another one references the original's images, so
    # the caller must be able to read the thread the image was stored under.
    if owner != thread_id:
        await _readable_thread_metadata(owner, login=login, email=email)
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
            "Content-Disposition": _content_disposition(stored.get("file_name")),
        },
    )
