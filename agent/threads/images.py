"""Serve images the agent moved out of the conversation into the thread's store."""

import logging

from fastapi import HTTPException, Response

from agent.thread_images import image_owner_threads, open_image_store, parse_image_name
from agent.threads.access import _readable_thread_metadata

logger = logging.getLogger(__name__)


async def get_dashboard_thread_image(
    thread_id: str, image_name: str, login: str, *, email: str | None = None
) -> Response:
    parsed = parse_image_name(image_name)
    if parsed is None:
        raise HTTPException(404, "image not found")
    _, mime_type = parsed
    metadata = await _readable_thread_metadata(thread_id, login=login, email=email)
    # Images live with the thread that stored them, so a reference copied into
    # a private continuation is looked up in the source thread as a fallback.
    for owner in image_owner_threads(thread_id, metadata.get("continued_from_thread_id")):
        try:
            store = await open_image_store(owner)
            content = await store.get(image_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not read a thread image",
                extra={"thread_id": owner, "image_name": image_name},
                exc_info=True,
            )
            raise HTTPException(503, "Could not connect to the workspace.") from exc
        if content is not None:
            return Response(
                content=content,
                media_type=mime_type,
                headers={
                    # Image names are immutable, so the browser can keep them for good.
                    "Cache-Control": "private, max-age=31536000, immutable",
                    "X-Content-Type-Options": "nosniff",
                },
            )
    raise HTTPException(404, "image not found")
