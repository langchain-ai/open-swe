"""Admin API for minting, listing, and revoking workspace API keys."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from agent.api_keys.deps import ADMIN_KEY_DEP
from agent.api_keys.models import MAX_EXPIRY_DAYS, NAME_MAX_CHARS, ApiKey
from agent.workspaces.store import WORKSPACES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/api-keys", tags=["api-keys"])


class ApiKeyCreate(BaseModel):
    workspace: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=NAME_MAX_CHARS)
    expires_at: datetime

    @field_validator("workspace", "name")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("expires_at")
    @classmethod
    def _bounded_expiry(cls, value: datetime) -> datetime:
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        if moment <= now:
            raise ValueError("expires_at must be in the future")
        if moment > now + timedelta(days=MAX_EXPIRY_DAYS):
            raise ValueError(f"expires_at must be at most {MAX_EXPIRY_DAYS} days out")
        return moment


class MintedApiKey(BaseModel):
    """The creation response — the only place the plaintext secret appears."""

    id: str
    workspace: str
    name: str
    key_suffix: str
    created_by: str
    created_at: datetime
    expires_at: datetime
    secret: str


@router.post("", status_code=201)
async def api_create_api_key(
    body: ApiKeyCreate,
    admin: dict[str, Any] = ADMIN_KEY_DEP,
) -> MintedApiKey:
    if not await WORKSPACES.slug_exists(body.workspace):
        raise HTTPException(404, "workspace not found")
    created_by = str(admin.get("sub") or "")
    key, secret = await ApiKey.create(
        workspace=body.workspace,
        name=body.name,
        expires_at=body.expires_at,
        created_by=created_by,
    )
    logger.info(
        "Minted a workspace API key",
        extra={"api_key_id": key.id, "workspace": key.workspace, "minted_by": created_by},
    )
    return MintedApiKey(
        id=key.id,
        workspace=key.workspace,
        name=key.name,
        key_suffix=key.key_suffix,
        created_by=key.created_by,
        created_at=key.created_at,
        expires_at=key.expires_at,
        secret=secret,
    )


@router.get("")
async def api_list_api_keys(
    workspace: str | None = None,
    _admin: dict[str, Any] = ADMIN_KEY_DEP,
) -> list[ApiKey]:
    return await ApiKey.list_all(workspace.strip() if workspace else None)


@router.delete("/{key_id}", status_code=204)
async def api_revoke_api_key(
    key_id: str,
    admin: dict[str, Any] = ADMIN_KEY_DEP,
) -> Response:
    if not await ApiKey.revoke(key_id):
        raise HTTPException(404, "api key not found")
    logger.info(
        "Revoked a workspace API key",
        extra={"api_key_id": key_id, "revoked_by": str(admin.get("sub") or "")},
    )
    return Response(status_code=204)
