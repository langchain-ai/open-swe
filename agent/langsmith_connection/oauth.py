"""LangSmith SaaS OAuth with PKCE and pinned public HTTPS transport."""

import base64
import hashlib
import secrets
from enum import StrEnum
from typing import Literal
from urllib.parse import urlencode

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from agent.mcp.transport import mcp_http_client


class Region(StrEnum):
    US = "us"
    EU = "eu"
    APAC = "apac"

    @property
    def issuer(self) -> str:
        match self:
            case Region.US:
                return "https://api.smith.langchain.com"
            case Region.EU:
                return "https://eu.api.smith.langchain.com"
            case Region.APAC:
                return "https://apac.api.smith.langchain.com"


class ConnectionError(Exception):
    def __init__(self, detail: str, *, status_code: int = 502, invalid_grant: bool = False):
        super().__init__(detail)
        self.status_code = status_code
        self.invalid_grant = invalid_grant


class TokenResponse(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    access_token: SecretStr = Field(min_length=1)
    token_type: Literal["Bearer"] = "Bearer"
    refresh_token: SecretStr | None = None
    expires_in: int = Field(gt=0)
    id_token: SecretStr | None = None
    workspace_id: str | None = None


class Identity(BaseModel):
    sub: str = Field(min_length=1)
    email: str | None = None
    name: str | None = None
    ls_org_id: str | None = None


class Registration(BaseModel):
    client_id: str = Field(min_length=1)


def parse_response[Model: BaseModel](response: httpx.Response, model: type[Model]) -> Model:
    try:
        return model.model_validate(response.json())
    except ValueError:
        raise ConnectionError("LangSmith returned an invalid connection response") from None


class Metadata(BaseModel):
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str


async def request(
    region: Region,
    method: str,
    path: str,
    *,
    data: dict[str, str] | None = None,
    body: dict[str, str | list[str]] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    try:
        async with mcp_http_client(region.issuer, timeout=httpx.Timeout(15)) as client:
            response = await client.request(
                method, region.issuer + path, data=data, json=body, headers=headers
            )
    except (httpx.HTTPError, ValueError) as exc:
        raise ConnectionError(
            "LangSmith is unavailable; please try again", status_code=503
        ) from exc
    if not response.is_success:
        invalid_grant = False
        try:
            error = response.json()
            invalid_grant = isinstance(error, dict) and error.get("error") == "invalid_grant"
        except ValueError:
            invalid_grant = False
        status = response.status_code
        raise ConnectionError(
            "LangSmith rejected the credential or connection request"
            if status < 500
            else "LangSmith is unavailable; please try again",
            status_code=400 if status < 500 else 503,
            invalid_grant=invalid_grant,
        )
    return response


def verifier_and_challenge() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    return verifier, challenge


async def authorize_url(
    region: Region, *, redirect_uri: str, state: str, challenge: str, nonce: str
) -> tuple[str, str]:
    response = await request(region, "GET", "/.well-known/oauth-authorization-server")
    metadata = parse_response(response, Metadata)
    expected = {
        "issuer": region.issuer,
        "authorization_endpoint": region.issuer + "/oauth/authorize",
        "token_endpoint": region.issuer + "/oauth/token",
        "registration_endpoint": region.issuer + "/oauth/register",
    }
    if metadata.model_dump() != expected:
        raise ConnectionError("LangSmith returned unexpected OAuth endpoints")
    registration = await request(
        region,
        "POST",
        "/oauth/register",
        body={
            "client_name": "Open SWE",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    client_id = parse_response(registration, Registration).client_id
    return region.issuer + "/oauth/authorize?" + urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "resource": region.issuer + "/mcp",
            "scope": "openid profile email offline_access",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    ), client_id


async def token_request(region: Region, data: dict[str, str]) -> TokenResponse:
    response = await request(region, "POST", "/oauth/token", data=data)
    return parse_response(response, TokenResponse)


async def verify_identity(
    region: Region, token: SecretStr | None, *, client_id: str, nonce: str
) -> Identity:
    if token is None:
        raise ConnectionError("LangSmith did not return an identity token")
    response = await request(region, "GET", "/.well-known/jwks.json")
    try:
        keys = jwt.PyJWKSet.from_dict(response.json())
        kid = jwt.get_unverified_header(token.get_secret_value()).get("kid")
        if not isinstance(kid, str) or not kid:
            raise ConnectionError("LangSmith returned an invalid identity token")
        key = keys[kid]
        claims = jwt.decode(
            token.get_secret_value(),
            key.key,
            algorithms=["EdDSA"],
            issuer=region.issuer,
            audience=client_id,
            options={"require": ["iss", "aud", "sub", "exp", "iat", "nonce"]},
        )
        if not isinstance(claims.get("nonce"), str) or not secrets.compare_digest(
            claims["nonce"], nonce
        ):
            raise ConnectionError("LangSmith identity nonce mismatch")
        return Identity.model_validate(claims)
    except jwt.PyJWTError, KeyError, ValueError:
        raise ConnectionError("LangSmith returned an invalid identity token") from None
