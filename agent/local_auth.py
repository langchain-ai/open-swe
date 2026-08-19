import hmac
import os

from langgraph_sdk import Auth

auth = Auth()


@auth.authenticate
async def authenticate(authorization: str | None) -> Auth.types.MinimalUserDict:
    token = os.environ.get("OPEN_SWE_LOCAL_AUTH_TOKEN")
    scheme, _, supplied = authorization.partition(" ") if authorization else ("", "", "")
    if scheme.lower() != "bearer" or not token or not hmac.compare_digest(supplied, token):
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid bearer token")
    return {"identity": "local-user"}
