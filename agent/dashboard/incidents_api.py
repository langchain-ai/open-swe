"""Authenticated dashboard projections and commands for Incidents."""

from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.dashboard.admin import is_admin
from agent.dashboard.oauth import require_session
from agent.incidents.access import is_observability_authorized

router = APIRouter(prefix="/incidents", tags=["incidents"])


class IncidentCommandBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(min_length=1, max_length=200)
    action: Literal["ask", "investigate_again", "pause", "resume", "complete", "reopen"]
    text: str | None = Field(default=None, max_length=8000)

    @model_validator(mode="after")
    def require_question(self) -> Self:
        if self.action == "ask" and not self.text:
            raise ValueError("ask requires nonempty text")
        return self


class IncidentSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0, strict=True)
    policy: dict[str, Any]


async def _responder(
    session: Annotated[dict[str, Any], Depends(require_session)],
) -> dict[str, Any]:
    if not is_observability_authorized(session.get("email"), login=session.get("sub")):
        raise HTTPException(403, "observability access required")
    return session


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


responder = _responder
actor = _actor


@router.get("/settings")
async def get_settings(
    session: Annotated[dict[str, Any], Depends(_administrator)],
) -> dict[str, Any]:
    from agent.incidents import service

    return await service.get_settings()


@router.patch("/settings", status_code=202)
async def update_settings(
    body: IncidentSettingsBody,
    session: Annotated[dict[str, Any], Depends(_administrator)],
) -> dict[str, Any]:
    from agent.incidents import service

    return await service.update_settings(
        policy=body.policy,
        expected_version=body.expected_version,
        actor=_actor(session),
    )


@router.get("/records")
async def list_incidents(
    session: Annotated[dict[str, Any], Depends(_responder)],
    view: Literal["active", "inactive", "all"] | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    from agent.incidents import service as incidents

    return await incidents.list_incidents(
        view=view,
        q=q,
        limit=limit,
        cursor=cursor,
        include_setup=is_admin(session.get("email"), login=session.get("sub")),
    )


@router.get("/records/{incident_id}")
async def get_incident(
    incident_id: str,
    session: Annotated[dict[str, Any], Depends(_responder)],
) -> dict[str, Any]:
    from agent.incidents import service

    return await service.get_incident(
        incident_id, include_setup=is_admin(session.get("email"), login=session.get("sub"))
    )


@router.post("/records/{incident_id}/commands", status_code=202)
async def submit_command(
    incident_id: str,
    body: IncidentCommandBody,
    session: Annotated[dict[str, Any], Depends(_responder)],
) -> dict[str, Any]:
    from agent.incidents import service

    return await service.submit_command(
        incident_id,
        action=body.action,
        text=body.text,
        request_id=body.request_id,
        actor=_actor(session),
    )
