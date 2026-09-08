"""Authentication for the LangGraph API routes (``auth`` in ``langgraph.json``).

The LangGraph API (``/threads``, ``/runs``, ``/assistants``, ``/store``, …) is only
ever called by Open SWE itself: the FastAPI side proxies the dashboard's requests
to it with ``X-Api-Key`` set to ``LANGSMITH_API_KEY``, the SDK clients used for
background work read the same key from the environment, and the desktop app's
embedded backend sends ``Authorization: Bearer OPEN_SWE_LOCAL_AUTH_TOKEN``. Every
other request is rejected, so the API is closed even where the server itself
would not authenticate (``langgraph dev``, or a standalone image left on the
default ``LANGGRAPH_AUTH_TYPE=noop``).

Custom routes are outside this handler: ``/dashboard/api/*`` checks its session
cookie and ``/webhooks/*`` checks signatures.
"""

import hmac

from langgraph_sdk import Auth

from agent.config import ENV

auth = Auth()


def _matches(supplied: str, expected: str | None) -> bool:
    return bool(expected) and hmac.compare_digest(supplied.encode(), expected.encode())


def _header(headers: dict | None, name: str) -> str:
    if not headers:
        return ""
    for key, value in headers.items():
        key_str = key.decode("latin-1") if isinstance(key, bytes) else str(key)
        if key_str.lower() == name:
            return value.decode("latin-1") if isinstance(value, bytes) else str(value)
    return ""


@auth.authenticate
async def authenticate(
    headers: dict | None, authorization: str | None
) -> Auth.types.MinimalUserDict:
    if _matches(_header(headers, "x-api-key"), ENV.LANGSMITH_API_KEY.optional()):
        return {"identity": "open-swe-backend"}
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() == "bearer" and _matches(
        token.strip(), ENV.OPEN_SWE_LOCAL_AUTH_TOKEN.optional()
    ):
        return {"identity": "local-user"}
    raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid API key or bearer token")
