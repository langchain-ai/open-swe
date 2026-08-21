import hmac

from langgraph_sdk import Auth

from .config import local_auth_token

auth = Auth()


@auth.authenticate
async def authenticate(authorization: str | None) -> Auth.types.MinimalUserDict:
    token = local_auth_token()
    scheme, _, supplied = authorization.partition(" ") if authorization else ("", "", "")
    if scheme.lower() != "bearer" or not token or not hmac.compare_digest(supplied, token):
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid bearer token")
    return {"identity": "local-user"}
