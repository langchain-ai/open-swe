"""Follow a short link.

No session is required: the link is a bearer capability, and GitHub's image proxy
has to fetch it anonymously.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from openswe.short_links.models import LINKS_PATH, ShortLink

router = APIRouter(prefix=LINKS_PATH, tags=["short-links"])


@router.get("/{token}", status_code=302, response_class=RedirectResponse)
async def open_short_link(token: str) -> RedirectResponse:
    link = await ShortLink.by_token(token)
    if link is None:
        raise HTTPException(404, "link not found")
    if link.expires_at is not None and link.expires_at <= datetime.now(UTC):
        raise HTTPException(410, "link expired")
    return RedirectResponse(
        link.target_url,
        status_code=302,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
