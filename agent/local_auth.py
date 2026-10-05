import hmac

from langgraph_sdk import Auth

from agent.config import ENV

auth = Auth()


@auth.authenticate
async def authenticate(authorization: str | None) -> Auth.types.MinimalUserDict:
    token = ENV.OPEN_SWE_LOCAL_AUTH_TOKEN.optional()
    scheme, _, supplied = authorization.partition(" ") if authorization else ("", "", "")
    if scheme.lower() != "bearer" or not token or not hmac.compare_digest(supplied, token):
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid bearer token")
    return {"identity": "local-user"}
