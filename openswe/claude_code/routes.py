"""Claude Code sync: the endpoint a person's hooks post to, and the tokens that let them.

The hook endpoint always answers with an empty body: Claude Code reads a JSON
body as a decision, and the mirror only ever watches.
"""

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

from openswe.audit_logs.middleware import audit_endpoint, bind_actor
from openswe.audit_logs.models import AuditLogEnrichments
from openswe.claude_code.hooks import HOOK_EVENT, MIRRORED_EVENTS, SessionMirror
from openswe.claude_code.sync_tokens import NAME_MAX_CHARS, ClaudeCodeSyncToken
from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.oauth import session_user_id
from openswe.database import postgres
from openswe.github.token_auth import bearer_github_token
from openswe.users.models import User
from openswe.utils.dashboard_links import dashboard_api_base_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/claude-code", tags=["claude-code"])

HOOKS_PATH = "/dashboard/api/claude-code/hooks"
# Hooks run inline in Claude Code, so a slow mirror must never hold up the session.
_HOOK_TIMEOUT_SECONDS = 5
# Claude Code holds each batch of a message off the screen until this hook answers.
_DISPLAY_HOOK_TIMEOUT_SECONDS = 2
_INVALID_TOKEN = "invalid Claude Code sync token"

HOOK_REQUEST_BODY: dict[str, object] = {
    "requestBody": {
        "required": True,
        "description": (
            "A Claude Code hook event, as Claude Code posts it. Authorized by a Claude Code "
            "sync token as a bearer token."
        ),
        "content": {"application/json": {"schema": {"type": "object"}}},
    }
}


def _require_database() -> None:
    if not postgres.configured():
        raise HTTPException(503, "Claude Code sync requires PostgreSQL; set POSTGRES_URI")


async def _hook_owner(request: Request) -> User:
    _require_database()
    presented = bearer_github_token(request)
    token = await ClaudeCodeSyncToken.authenticate(presented) if presented else None
    owner = await User.get(token.user_id) if token is not None else None
    if owner is None or not owner.github_login:
        raise HTTPException(401, _INVALID_TOKEN, headers={"WWW-Authenticate": "Bearer"})
    bind_actor(
        request,
        user_id=owner.id,
        enrichments=AuditLogEnrichments(actor_kind="person", actor_login=owner.github_login),
    )
    return owner


@router.post("/hooks", status_code=204, openapi_extra=HOOK_REQUEST_BODY)
async def api_claude_code_hook(request: Request) -> Response:
    owner = await _hook_owner(request)
    try:
        event = HOOK_EVENT.validate_python(await request.json())
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, "the body is not a Claude Code hook event") from exc
    try:
        await SessionMirror(owner, event.session_id).record(event)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return Response(status_code=204)


class SyncTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX_CHARS)

    @field_validator("name")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class SyncTokenView(BaseModel):
    """A stored token as its owner sees it. Has no field for the secret."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    token_suffix: str
    created_at: datetime
    last_used_at: datetime | None


class MintedSyncToken(SyncTokenView):
    """The creation response: the only place the secret appears, ready to paste into Claude Code."""

    secret: str
    settings: dict[str, JsonValue]


def hook_settings(secret: str) -> dict[str, JsonValue]:
    """The ``hooks`` block of a Claude Code ``settings.json`` that mirrors every session."""

    def handler(event: str) -> dict[str, JsonValue]:
        return {
            "type": "http",
            "url": dashboard_api_base_url() + HOOKS_PATH,
            "timeout": _DISPLAY_HOOK_TIMEOUT_SECONDS
            if event == "MessageDisplay"
            else _HOOK_TIMEOUT_SECONDS,
            "headers": {"Authorization": f"Bearer {secret}"},
        }

    return {"hooks": {event: [{"hooks": [handler(event)]}] for event in sorted(MIRRORED_EVENTS)}}


async def _session_user(session: dict[str, Any]) -> User:
    _require_database()
    user = await User.for_session(session_user_id(session), session["sub"])
    if user is None:
        raise HTTPException(403, "Claude Code sync requires a signed-in Open SWE user")
    return user


@router.get("/tokens")
async def api_list_claude_code_tokens(
    session: dict[str, Any] = SESSION_DEP,
) -> list[SyncTokenView]:
    user = await _session_user(session)
    return [
        SyncTokenView.model_validate(token) for token in await ClaudeCodeSyncToken.for_user(user.id)
    ]


@router.post("/tokens", status_code=201)
@audit_endpoint
async def api_create_claude_code_token(
    body: SyncTokenCreate,
    session: dict[str, Any] = SESSION_DEP,
) -> MintedSyncToken:
    user = await _session_user(session)
    token, secret = await ClaudeCodeSyncToken.create(user_id=user.id, name=body.name)
    logger.info(
        "Minted a Claude Code sync token",
        extra={"sync_token_id": str(token.id), "github_login": session["sub"]},
    )
    return MintedSyncToken(
        **SyncTokenView.model_validate(token).model_dump(),
        secret=secret,
        settings=hook_settings(secret),
    )


@router.delete("/tokens/{token_id}", status_code=204)
@audit_endpoint
async def api_revoke_claude_code_token(
    token_id: UUID,
    session: dict[str, Any] = SESSION_DEP,
) -> Response:
    user = await _session_user(session)
    if not await ClaudeCodeSyncToken.revoke(user_id=user.id, token_id=token_id):
        raise HTTPException(404, "token not found")
    logger.info(
        "Revoked a Claude Code sync token",
        extra={"sync_token_id": str(token_id), "github_login": session["sub"]},
    )
    return Response(status_code=204)
