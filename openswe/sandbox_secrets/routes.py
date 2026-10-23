"""Dashboard API for a person's own sandbox secrets. Values are write-only."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic import Field

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.oauth import session_user_id
from openswe.sandbox_secrets.models import (
    MAX_VALUE_CHARS,
    SandboxSecret,
    SecretRejected,
    SecretSettings,
    SecretView,
)
from openswe.users.models import User

router = APIRouter(prefix="/me/sandbox-secrets", tags=["sandbox-secrets"])


class SecretUpdate(SecretSettings):
    value: str | None = Field(default=None, max_length=MAX_VALUE_CHARS)


async def _owner(session: dict[str, Any]) -> UUID:
    user = await User.for_session(session_user_id(session), session["sub"])
    if user is None:
        raise HTTPException(403, "Sandbox secrets need an Open SWE account")
    return user.id


@router.get("")
async def api_list_sandbox_secrets(session: dict[str, Any] = SESSION_DEP) -> list[SecretView]:
    return [secret.view() for secret in await SandboxSecret.owned_by(await _owner(session))]


@router.put("/{name}", status_code=204)
@audit_endpoint
async def api_save_sandbox_secret(
    name: str, body: SecretUpdate, session: dict[str, Any] = SESSION_DEP
) -> Response:
    try:
        await SandboxSecret.save(
            await _owner(session),
            name,
            SecretSettings.model_validate(body.model_dump(exclude={"value"})),
            body.value,
        )
    except SecretRejected as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(status_code=204)


@router.delete("/{name}", status_code=204)
@audit_endpoint
async def api_delete_sandbox_secret(name: str, session: dict[str, Any] = SESSION_DEP) -> Response:
    if not await SandboxSecret.remove(await _owner(session), name):
        raise HTTPException(404, "Sandbox secret not found")
    return Response(status_code=204)
