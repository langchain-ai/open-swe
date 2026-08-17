"""Authenticated endpoint for signed sandbox file downloads."""

from __future__ import annotations

import logging
import mimetypes
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..utils.sandbox import create_sandbox
from ..utils.sandbox_downloads import (
    InvalidSandboxDownloadToken,
    SandboxDownloadError,
    SandboxDownloadFileNotFound,
    SandboxDownloadFileTooLarge,
    decode_sandbox_download_token,
    download_sandbox_file,
)
from .oauth import require_session
from .thread_api import get_dashboard_terminal_sandbox

logger = logging.getLogger(__name__)

sandbox_download_router = APIRouter(
    prefix="/dashboard/api/sandbox-files",
    tags=["dashboard"],
)
_SESSION_DEP = Depends(require_session)


@sandbox_download_router.get("/{token}")
async def download_sandbox_file_route(
    token: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    try:
        claims = decode_sandbox_download_token(token)
    except InvalidSandboxDownloadToken as exc:
        raise HTTPException(401, str(exc)) from exc
    except RuntimeError as exc:
        logger.exception("Sandbox download token configuration failed")
        raise HTTPException(500, "sandbox downloads are not configured") from exc

    sandbox_id, _ = await get_dashboard_terminal_sandbox(
        claims.thread_id,
        session["sub"],
        email=session.get("email"),
    )
    if sandbox_id != claims.sandbox_id:
        raise HTTPException(404, "sandbox download is no longer available")
    try:
        backend = await create_sandbox(sandbox_id)
        info, content = await download_sandbox_file(backend, claims.file_path)
    except SandboxDownloadFileNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except SandboxDownloadFileTooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except SandboxDownloadError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:
        logger.exception("Sandbox file download failed for thread %s", claims.thread_id)
        raise HTTPException(502, "sandbox file could not be downloaded") from exc

    current_sandbox_id, _ = await get_dashboard_terminal_sandbox(
        claims.thread_id,
        session["sub"],
        email=session.get("email"),
    )
    if current_sandbox_id != claims.sandbox_id:
        raise HTTPException(404, "sandbox download is no longer available")

    media_type = mimetypes.guess_type(info.filename)[0] or "application/octet-stream"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": _content_disposition(info.filename),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _content_disposition(filename: str) -> str:
    fallback = "".join(
        character if 32 <= ord(character) < 127 and character not in {'"', "\\"} else "_"
        for character in filename
    )
    fallback = fallback or "download"
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"
