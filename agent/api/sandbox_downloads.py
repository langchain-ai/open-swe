"""Short public links for sandbox file downloads."""

from datetime import UTC, datetime
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from agent.store import get_value

router = APIRouter(tags=["sandbox-downloads"])
_NAMESPACE = ("sandbox_downloads",)


def _is_expired(expires_at: object) -> bool:
    if expires_at is None:
        return False
    if not isinstance(expires_at, str):
        return True
    try:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires <= datetime.now(UTC)


@router.get("/sandbox-download/{handle}")
async def sandbox_download(handle: str) -> RedirectResponse:
    record = await get_value(_NAMESPACE, handle)
    if record is None or _is_expired(record.get("expires_at")):
        raise HTTPException(404, "download not found")
    url = record.get("url")
    if not isinstance(url, str):
        raise HTTPException(404, "download not found")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise HTTPException(404, "download not found")
    return RedirectResponse(url, status_code=307, headers={"Cache-Control": "no-store"})
