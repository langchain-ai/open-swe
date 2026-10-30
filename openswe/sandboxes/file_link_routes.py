"""Open a short sandbox file link by redirecting to a freshly signed LangSmith URL.

No session is required: like the signed URL it stands for, the link is a bearer
capability, and GitHub's image proxy has to fetch it anonymously.
"""

from datetime import UTC, datetime
from math import ceil

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from langsmith.sandbox import ResourceNotFoundError

from openswe.sandboxes.file_links import LINKS_PATH, get_link

router = APIRouter(prefix=LINKS_PATH, tags=["sandbox-file-links"])

# Long enough for a slow download to start; short so the target soon stops working if it leaks.
SIGNED_URL_SECONDS = 3600


@router.get("/{link_id}", status_code=302, response_class=RedirectResponse)
async def open_sandbox_file_link(link_id: str) -> RedirectResponse:
    link = await get_link(link_id)
    if link is None:
        raise HTTPException(404, "link not found")
    lifetime = SIGNED_URL_SECONDS
    if link.expires_at is not None:
        remaining = ceil((link.expires_at - datetime.now(UTC)).total_seconds())
        if remaining <= 0:
            raise HTTPException(410, "link expired")
        lifetime = min(lifetime, remaining)
    # Deferred: the provider loads deepagents, which the web app must not import at startup.
    from openswe.sandboxes.providers.langsmith import get_async_sandbox_client

    try:
        async with get_async_sandbox_client() as client:
            download = await client.generate_download_url(
                link.sandbox_id,
                link.path,
                expires_in_seconds=lifetime,
                content_type=link.content_type,
                content_disposition=link.content_disposition,
            )
    except ResourceNotFoundError:
        raise HTTPException(410, "the sandbox holding this file is gone") from None
    if not download.download_url:
        raise HTTPException(502, "LangSmith did not return a download URL")
    return RedirectResponse(
        download.download_url,
        status_code=302,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
