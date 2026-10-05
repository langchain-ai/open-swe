"""Signed PR-description redirects with optional visitor attribution."""

import hashlib
import hmac
import logging
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.background import BackgroundTask
from starlette.responses import RedirectResponse

from agent.config import ENV
from agent.dashboard.oauth import optional_session, session_user_id
from agent.utils.dashboard_links import dashboard_api_base_url

logger = logging.getLogger(__name__)
router = APIRouter()
_AUDIENCE = "pr-description-link"


class PrLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aud: str
    url: str
    thread_id: str | None
    kind: str


def _signing_key(secret: str) -> bytes:
    return hmac.digest(secret.encode(), b"open-swe:pr-description-links", hashlib.sha256)


def _valid_destination(url: str) -> bool:
    if any(ord(char) < 33 or ord(char) == 127 for char in url) or "\\" in url:
        return False
    try:
        parsed = urlsplit(url)
        return bool(
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        )
    except ValueError:
        return False


def tracked_pr_link(url: str, *, thread_id: str | None, kind: str) -> str:
    """Keep published links usable until the deployment's signing secret changes."""
    secret = ENV.PR_LINK_SIGNING_KEY.get()
    base = dashboard_api_base_url()
    if not secret or not base or not _valid_destination(url):
        return url
    link = PrLink(aud=_AUDIENCE, url=url, thread_id=thread_id, kind=kind)
    token = jwt.encode(link.model_dump(), _signing_key(secret), algorithm="HS256")
    return f"{base}/dashboard/api/analytics/pr-link?{urlencode({'token': token})}"


async def _record_visit(link: PrLink, user_id: str | None, login: str | None) -> None:
    properties = {
        "surface": "pr_description",
        "link_kind": link.kind,
        "thread_id": link.thread_id,
        "product": "open-swe",
        "environment": ENV.DD_ENV.get(),
        "authenticated": user_id is not None,
    }
    logger.info(
        "PR description link visited",
        extra={**properties, "visitor_user_id": user_id, "visitor_login": login},
    )
    key = ENV.SEGMENT_WRITE_KEY.get()
    if not key:
        return
    payload: dict[str, object] = {
        "event": "PR Description Link Visited",
        "properties": properties,
        "context": {"ip": "0.0.0.0"},
    }
    if user_id:
        payload["userId"] = user_id
    else:
        payload["anonymousId"] = str(uuid4())
    try:
        async with httpx.AsyncClient(
            base_url="https://api.segment.io/v1/", auth=(key, ""), timeout=2.0
        ) as client:
            response = await client.post("track", json=payload)
            response.raise_for_status()
    except Exception:
        logger.warning("PR link telemetry delivery failed", exc_info=True)


@router.get("/analytics/pr-link", include_in_schema=False)
async def visit_pr_link(request: Request, token: str) -> RedirectResponse:
    secret = ENV.PR_LINK_SIGNING_KEY.get()
    if not secret:
        raise HTTPException(503, "PR links are unavailable")
    try:
        link = PrLink.model_validate(
            jwt.decode(token, _signing_key(secret), algorithms=["HS256"], audience=_AUDIENCE)
        )
        if not _valid_destination(link.url):
            raise ValueError("Invalid destination")
    except (jwt.PyJWTError, ValidationError, ValueError) as exc:
        raise HTTPException(400, "Invalid PR link") from exc
    session = optional_session(request)
    visitor = session_user_id(session) if session else None
    login = session.get("sub") if session and visitor else None
    return RedirectResponse(
        link.url,
        status_code=302,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        background=BackgroundTask(
            _record_visit,
            link,
            str(visitor) if visitor else None,
            login if isinstance(login, str) else None,
        ),
    )
