"""Personal LangSmith connection endpoints and browser/desktop OAuth callbacks."""

import hmac
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from agent.dashboard.oauth import (
    STATE_TTL_SECONDS,
    DesktopConnectExchange,
    cookie_security,
    decode_state,
    desktop_callback_url,
    desktop_handoff_from_state,
    frontend_base_url,
    hash_state_nonce,
    issue_connect_handoff,
    issue_state,
    new_state_nonce,
    redeem_connect_handoff,
    require_session,
    valid_handoff_challenge,
)
from agent.encryption import decrypt_token, encrypt_token
from agent.langsmith_connection import credentials, oauth
from agent.store import TypedStore
from agent.utils.dashboard_links import dashboard_api_base_url


class CredentialRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()

        async def redacted_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": "Invalid LangSmith connection settings; check the key, region and workspace UUID"
                    },
                )

        return redacted_handler


router = APIRouter(tags=["langsmith"], route_class=CredentialRoute)
STATE_COOKIE = "osw_langsmith_oauth_state"
COOKIE_PATH = "/dashboard/api/langsmith"


class Session(BaseModel):
    sub: str


def session_for(request: Request) -> Session:
    return Session.model_validate(require_session(request))


SessionDep = Annotated[Session, Depends(session_for)]


class APIKeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    region: oauth.Region = oauth.Region.US
    api_key: SecretStr = Field(min_length=1, max_length=8192)
    workspace_id: UUID | None = None


class Flow(BaseModel):
    region: oauth.Region
    client_id: str
    encrypted_verifier: str = Field(repr=False)
    nonce: str
    redirect_uri: str
    expires_at: datetime
    generation: str = ""


def flows(login: str) -> TypedStore[Flow]:
    return TypedStore(["langsmith_oauth_flows", login.lower()], Flow)


def set_state_cookie(response: Response, nonce: str | None) -> None:
    secure, _ = cookie_security()
    if nonce is None:
        response.delete_cookie(STATE_COOKIE, path=COOKIE_PATH, secure=secure, samesite="lax")
    else:
        response.set_cookie(
            STATE_COOKIE,
            nonce,
            max_age=STATE_TTL_SECONDS,
            httponly=True,
            secure=secure,
            samesite="lax",
            path=COOKIE_PATH,
        )


@router.get("/my-credentials/langsmith")
async def get_status(session: SessionDep) -> credentials.Status:
    return await credentials.status(session.sub)


@router.put("/my-credentials/langsmith")
async def save_api_key(body: APIKeyInput, session: SessionDep) -> credentials.Status:
    try:
        return await credentials.connect_key(
            session.sub, body.region, body.api_key, body.workspace_id
        )
    except oauth.ConnectionError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    except ValueError:
        raise HTTPException(
            400, "Could not connect to LangSmith MCP; check the key, region and permissions"
        ) from None


@router.delete("/my-credentials/langsmith")
async def disconnect(session: SessionDep) -> credentials.Status:
    return await credentials.disconnect(session.sub)


@router.get("/langsmith/login")
async def login(
    session: SessionDep,
    region: oauth.Region = oauth.Region.US,
    desktop_handoff: str | None = None,
    desktop_port: int | None = Query(default=None, ge=1024, le=65535),
) -> RedirectResponse:
    generation = await credentials.generation(session.sub)
    nonce = new_state_nonce()
    nonce_hash = hash_state_nonce(nonce)
    redirect_uri = f"{dashboard_api_base_url()}/dashboard/api/langsmith/callback"
    state = issue_state(
        redirect_to=f"{frontend_base_url()}/my-settings",
        nonce_hash=nonce_hash,
        handoff_challenge=valid_handoff_challenge(desktop_handoff),
        handoff_port=desktop_port,
    )
    verifier, challenge = oauth.verifier_and_challenge()
    try:
        url, client_id = await oauth.authorize_url(
            region, redirect_uri=redirect_uri, state=state, challenge=challenge, nonce=nonce_hash
        )
    except oauth.ConnectionError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    await flows(session.sub).put(
        nonce_hash,
        Flow(
            region=region,
            client_id=client_id,
            encrypted_verifier=encrypt_token(verifier),
            nonce=nonce_hash,
            redirect_uri=redirect_uri,
            expires_at=datetime.now(UTC) + timedelta(seconds=STATE_TTL_SECONDS),
            generation=generation,
        ),
    )
    response = RedirectResponse(url, status_code=302)
    set_state_cookie(response, nonce)
    return response


async def complete(login: str, nonce_hash: str, code: str) -> None:
    async with credentials.lock(login):
        flow = await flows(login).get(nonce_hash)
        if flow is None or flow.expires_at <= datetime.now(UTC):
            raise HTTPException(400, "OAuth flow expired; please retry")
        await flows(login).delete(nonce_hash)
    try:
        verifier = decrypt_token(flow.encrypted_verifier)
        if not verifier:
            raise HTTPException(400, "OAuth flow expired; please retry")
        tokens = await oauth.token_request(
            flow.region,
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": flow.client_id,
                "redirect_uri": flow.redirect_uri,
                "code_verifier": verifier,
                "resource": oauth.issuer(flow.region) + "/mcp",
            },
        )
        identity = await oauth.verify_identity(
            flow.region, tokens.id_token, client_id=flow.client_id, nonce=flow.nonce
        )
        await credentials.save(
            login,
            credentials.oauth_record(tokens, flow.region, flow.client_id, identity),
            flow.generation,
        )
    except oauth.ConnectionError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    except ValueError:
        raise HTTPException(400, "Could not connect to LangSmith MCP; please retry") from None


@router.get("/langsmith/callback")
async def callback(
    request: Request, state: str, code: str | None = None, error: str | None = None
) -> RedirectResponse:
    payload = decode_state(state)
    nonce_hash = payload.get("nonce_hash")
    if not isinstance(nonce_hash, str):
        raise HTTPException(400, "OAuth state mismatch; please retry")
    if error or not code:
        raise HTTPException(400, "LangSmith connection was not authorized; please retry")
    handoff = desktop_handoff_from_state(payload)
    if handoff is not None:
        challenge, port = handoff
        handoff_code = issue_connect_handoff(
            provider="langsmith",
            challenge=challenge,
            claims={"nonce_hash": nonce_hash, "code": code},
        )
        response = RedirectResponse(desktop_callback_url(port, handoff_code), status_code=302)
    else:
        session = session_for(request)
        cookie = request.cookies.get(STATE_COOKIE)
        if not cookie or not hmac.compare_digest(hash_state_nonce(cookie), nonce_hash):
            raise HTTPException(400, "OAuth state mismatch; please retry")
        await complete(session.sub, nonce_hash, code)
        response = RedirectResponse(f"{frontend_base_url()}/my-settings", status_code=302)
    set_state_cookie(response, None)
    return response


@router.post("/langsmith/desktop/exchange")
async def desktop_exchange(body: DesktopConnectExchange, session: SessionDep) -> credentials.Status:
    claims = redeem_connect_handoff(provider="langsmith", code=body.code, verifier=body.verifier)
    nonce_hash, code = claims.get("nonce_hash"), claims.get("code")
    if not isinstance(nonce_hash, str) or not isinstance(code, str):
        raise HTTPException(400, "Malformed handoff code")
    await complete(session.sub, nonce_hash, code)
    return await credentials.status(session.sub)
