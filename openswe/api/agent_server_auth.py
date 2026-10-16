"""Who may call the Agent Server's own API, which the dashboard API sits in front of.

The backend reaches it with the deployment's LangSmith key and is the only caller
that may change anything. A person signed in to the dashboard may read the threads
they own; everything else goes through the dashboard API, which applies Open SWE's
thread rules before it calls here as the backend. Calls the backend makes in process
skip this entirely, as the Agent Server does for its own ASGI transport.
"""

import hmac
import os
from collections.abc import Sequence
from typing import Final

from langgraph_sdk import Auth
from starlette.authentication import AuthCredentials
from starlette.requests import HTTPConnection
from starlette.types import Scope

from openswe.config import ENV
from openswe.dashboard.oauth import optional_session

BACKEND_PERMISSION: Final = "openswe:backend"
_BACKEND_IDENTITY: Final = "openswe:backend"

auth = Auth()


def _backend_keys() -> set[str]:
    """The keys the backend's own Agent Server clients send, as the SDK resolves them."""
    sdk_keys = (
        os.getenv(f"{prefix}_API_KEY") for prefix in ("LANGGRAPH", "LANGSMITH", "LANGCHAIN")
    )
    return {
        key.strip().strip("'\"")
        for key in (*sdk_keys, ENV.LANGSMITH_API_KEY.optional())
        if key and key.strip().strip("'\"")
    }


def is_backend(permissions: Sequence[str]) -> bool:
    return BACKEND_PERMISSION in permissions


def backend_call(scope: Scope) -> bool:
    """Whether app middleware may answer or forward this call without the Agent Server.

    Only the backend's calls may skip the Agent Server's resource authorization;
    others go on to its routes, which apply it. The SDK's in-process transport
    mounts at ``/noauth``, which no request can set.
    """
    from langgraph_api.config import USES_CUSTOM_AUTH

    root_path = scope.get("root_path", "")
    credentials = scope.get("auth")
    return (
        not USES_CUSTOM_AUTH
        or root_path == "/noauth"
        or root_path.startswith("/noauth/")
        or (isinstance(credentials, AuthCredentials) and is_backend(credentials.scopes))
    )


@auth.authenticate
async def authenticate(scope: Scope) -> Auth.types.MinimalUserDict:
    conn = HTTPConnection(scope)
    api_key = conn.headers.get("x-api-key", "").strip()
    if api_key and any(hmac.compare_digest(api_key, key) for key in _backend_keys()):
        return {"identity": _BACKEND_IDENTITY, "permissions": [BACKEND_PERMISSION]}
    session = optional_session(conn)
    login = session.get("sub") if session else None
    if isinstance(login, str) and login.strip():
        return {"identity": login.strip(), "permissions": []}
    raise Auth.exceptions.HTTPException(status_code=401, detail="not authenticated")


@auth.on.threads.read
async def read_thread(ctx: Auth.types.AuthContext, value: object) -> Auth.types.FilterType | None:
    if is_backend(ctx.permissions):
        return None
    return {"owner_login": ctx.user.identity}


@auth.on.threads.search
async def search_threads(
    ctx: Auth.types.AuthContext, value: object
) -> Auth.types.FilterType | None:
    if is_backend(ctx.permissions):
        return None
    return {"owner_login": ctx.user.identity}


@auth.on
async def backend_only(ctx: Auth.types.AuthContext, value: object) -> None:
    if not is_backend(ctx.permissions):
        raise Auth.exceptions.HTTPException(
            status_code=403, detail="use the dashboard API to change threads"
        )
