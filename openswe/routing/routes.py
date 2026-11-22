"""Authenticated dashboard API for flat integration bindings."""

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import ADMIN_DEP, DashboardReadDep
from openswe.dashboard.oauth import session_user_id
from openswe.routing.store import (
    INTEGRATIONS,
    ChannelPrompt,
    ChannelSettings,
    IntegrationSettings,
    RepositorySettings,
)
from openswe.slack.channels import ChannelLookupUnavailable

router = APIRouter(tags=["routing"])


class ChannelConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: ChannelPrompt


class RoutingErrors:
    @staticmethod
    async def handle() -> AsyncIterator[None]:
        try:
            yield
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ChannelLookupUnavailable as exc:
            raise HTTPException(
                503, str(exc), headers={"Retry-After": str(exc.retry_after)}
            ) from exc


ERRORS = [Depends(RoutingErrors.handle)]


@router.get("/routing")
async def api_routing(reader: DashboardReadDep) -> IntegrationSettings:
    return await INTEGRATIONS.settings(session=reader.session)


@router.put("/routing/repositories/{owner}/{name}", dependencies=ERRORS)
@audit_endpoint
async def api_configure_repository(
    owner: str, name: str, admin: dict[str, object] = ADMIN_DEP
) -> RepositorySettings:
    return await INTEGRATIONS.configure_repository(f"{owner}/{name}", actor=session_user_id(admin))


@router.delete("/routing/repositories/{owner}/{name}", status_code=204, dependencies=ERRORS)
@audit_endpoint
async def api_remove_repository(
    owner: str, name: str, _admin: dict[str, object] = ADMIN_DEP
) -> None:
    await INTEGRATIONS.remove_repository(f"{owner}/{name}")


@router.put("/routing/slack-channels/{channel_id}", dependencies=ERRORS)
@audit_endpoint
async def api_configure_slack_channel(
    channel_id: str, body: ChannelConfiguration, admin: dict[str, object] = ADMIN_DEP
) -> ChannelSettings:
    return await INTEGRATIONS.configure_channel(
        channel_id,
        actor=session_user_id(admin),
        prompt=body.prompt,
    )


@router.delete("/routing/slack-channels/{channel_id}", status_code=204, dependencies=ERRORS)
@audit_endpoint
async def api_remove_slack_channel(channel_id: str, _admin: dict[str, object] = ADMIN_DEP) -> None:
    await INTEGRATIONS.remove_channel(channel_id)
