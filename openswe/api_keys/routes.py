"""Admin API for minting, listing, and revoking workspace API keys."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from openswe.api_keys.deps import ADMIN_KEY_DEP
from openswe.api_keys.models import MAX_EXPIRY_DAYS, NAME_MAX_CHARS, ApiKey, ApiKeyStatus
from openswe.audit_logs.middleware import audit_endpoint
from openswe.users.models import User
from openswe.web.oauth import session_user_id
from openswe.workspaces.store import WORKSPACES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/api-keys", tags=["api-keys"])


class ApiKeyCreate(BaseModel):
    workspace: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=NAME_MAX_CHARS)
    expires_at: datetime
    description: str | None = Field(default=None, max_length=4000)

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


class ApiKeyView(BaseModel):
    """A stored key as the admin API reports it. Has no field for the secret."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace: str
    name: str
    key_suffix: str
    created_by: str
    created_at: datetime | None
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    status: ApiKeyStatus
    description: str | None = None
    created_by_name: str | None = None


class MintedApiKey(ApiKeyView):
    """The creation response — the only place the plaintext secret appears."""

    secret: str


async def key_view(key: ApiKey) -> ApiKeyView:
    view = ApiKeyView.model_validate(key)
    try:
        user_id = UUID(key.created_by)
    except ValueError:
        return view
    user = await User.get(user_id)
    if user is not None:
        view.created_by_name = user.display_name or user.login_for("github")
    return view


@router.post("", status_code=201)
@audit_endpoint
async def api_create_api_key(
    body: ApiKeyCreate,
    admin: dict[str, Any] = ADMIN_KEY_DEP,
) -> MintedApiKey:
    creator = await User.for_session(session_user_id(admin), admin["sub"])
    if creator is None:
        raise HTTPException(403, "API key creation requires an admin signed in as a real user")
    creator_id = creator.id
    workspace_id = await WORKSPACES.id_for_slug(body.workspace)
    if workspace_id is None:
        raise HTTPException(404, "workspace not found")
    created_by = str(creator_id)
    key, secret = await ApiKey.create(
        workspace_id=workspace_id,
        workspace=body.workspace,
        name=body.name,
        expires_at=body.expires_at,
        created_by=created_by,
        description=body.description,
    )
    logger.info(
        "Minted a workspace API key",
        extra={"api_key_id": key.id, "workspace": key.workspace, "minted_by": created_by},
    )
    return MintedApiKey(**(await key_view(key)).model_dump(), secret=secret)


@router.get("")
async def api_list_api_keys(
    workspace: str | None = None,
    _admin: dict[str, Any] = ADMIN_KEY_DEP,
) -> list[ApiKeyView]:
    keys = await ApiKey.list_all(workspace.strip() if workspace else None)
    return [await key_view(key) for key in keys]


@router.delete("/{key_id}", status_code=204)
@audit_endpoint
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
