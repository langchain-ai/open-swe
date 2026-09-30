"""Record dashboard actions that failed in the browser under the ID the user sees."""

import logging
from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from agent.dashboard.deps import SESSION_DEP

logger = logging.getLogger(__name__)

router = APIRouter()

_ERROR_ID_PATTERN = r"^(req|err)_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class ClientErrorReport(BaseModel):
    error_id: str = Field(pattern=_ERROR_ID_PATTERN)
    title: str = Field(max_length=200)
    error_message: str = Field(max_length=2000)
    status: int | None = Field(default=None, ge=0, le=599)
    mutation: str | None = Field(default=None, max_length=500)
    path: str = Field(default="", max_length=500)


@router.post("/client-errors", status_code=204)
async def api_report_client_error(
    report: ClientErrorReport,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    logger.warning(
        "Dashboard action failed",
        extra={
            "error_id": report.error_id,
            "error_title": report.title,
            "error_message": report.error_message,
            "status_code": report.status,
            "mutation": report.mutation,
            "page_path": report.path,
            "login": session["sub"],
        },
    )
    return Response(status_code=204)
