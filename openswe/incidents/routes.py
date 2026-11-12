"""Admin settings for the channels Robin joins as incident channels."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.admin import is_admin
from openswe.dashboard.oauth import require_session

router = APIRouter(prefix="/incidents", tags=["incidents"])


class IncidentSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0, strict=True)
    policy: dict[str, Any]


async def _administrator(
    session: Annotated[dict[str, Any], Depends(require_session)],
) -> dict[str, Any]:
    if not is_admin(session.get("email"), login=session.get("sub")):
        raise HTTPException(403, "admin only")
    return session


def _actor(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"github:{session['sub']}",
        "platform": "github",
        "github_login": session["sub"],
        "email": session.get("email"),
    }


@router.get("/settings")
async def get_settings(
    session: Annotated[dict[str, Any], Depends(_administrator)],
) -> dict[str, Any]:
    from openswe.incidents import service

    return await service.get_settings()


@router.patch("/settings", status_code=202)
@audit_endpoint
async def update_settings(
    body: IncidentSettingsBody,
    session: Annotated[dict[str, Any], Depends(_administrator)],
) -> dict[str, Any]:
    from openswe.incidents import service

    return await service.update_settings(
        policy=body.policy,
        expected_version=body.expected_version,
        actor=_actor(session),
    )
