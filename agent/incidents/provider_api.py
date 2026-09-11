"""Responder-authenticated provider reads and queued provider operations."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from agent.dashboard import incidents_api
from agent.incidents import providers

router = APIRouter(tags=["incidents"])
Responder = Annotated[dict[str, Any], Depends(incidents_api.responder)]


class ProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(min_length=1, max_length=200)


class AttachProviderBody(ProviderRequest):
    connection_name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    external_id: str = Field(min_length=1, max_length=2048)


class ProviderStatusBody(ProviderRequest):
    status_id: str | None = Field(default=None, min_length=1, max_length=200)
    severity_id: str | None = Field(default=None, min_length=1, max_length=200)


class ProviderReadBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    incident_id: str = Field(min_length=1, max_length=200)
    connection_name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")


class ProviderSearchBody(ProviderReadBody):
    query: str = Field(default="", max_length=2000)


class ProviderExternalBody(ProviderReadBody):
    external_id: str = Field(min_length=1, max_length=2048)


@router.get("/connections")
async def list_connections(
    incident_id: Annotated[str, Query(min_length=1, max_length=200)],
    session: Responder,
) -> dict[str, Any]:
    return await providers.list_connections(incident_id)


@router.post("/search")
async def search_external(body: ProviderSearchBody, session: Responder) -> dict[str, Any]:
    return await providers.search_external(body.incident_id, body.connection_name, body.query)


@router.post("/external")
async def read_external(body: ProviderExternalBody, session: Responder) -> dict[str, Any]:
    return await providers.read_external(body.incident_id, body.connection_name, body.external_id)


@router.get("/{incident_id}")
async def provider_context(incident_id: str, session: Responder) -> dict[str, Any]:
    return await providers.provider_context(incident_id)


@router.get("/{incident_id}/operations/{operation_id}")
async def get_operation(incident_id: str, operation_id: str, session: Responder) -> dict[str, Any]:
    return await providers.get_operation(incident_id, operation_id)


@router.post("/{incident_id}/attach", status_code=202)
async def attach_provider(
    incident_id: str,
    body: AttachProviderBody,
    session: Responder,
) -> dict[str, Any]:
    return await providers.submit_provider_command(
        incident_id,
        "attach",
        body.model_dump(exclude={"request_id"}),
        body.request_id,
        incidents_api.actor(session),
    )


@router.post("/{incident_id}/refresh", status_code=202)
async def refresh_provider(
    incident_id: str,
    body: ProviderRequest,
    session: Responder,
) -> dict[str, Any]:
    return await providers.submit_provider_command(
        incident_id,
        "refresh",
        {},
        body.request_id,
        incidents_api.actor(session),
    )


@router.post("/{incident_id}/status", status_code=202)
async def update_status(
    incident_id: str,
    body: ProviderStatusBody,
    session: Responder,
) -> dict[str, Any]:
    return await providers.submit_provider_command(
        incident_id,
        "status",
        body.model_dump(exclude={"request_id"}, exclude_none=True),
        body.request_id,
        incidents_api.actor(session),
    )
