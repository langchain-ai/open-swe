"""Admin-managed MCP connections shared by one Open SWE workspace/deployment."""

import json
import re
from collections.abc import Callable, Coroutine
from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.encryption import decrypt_token, encrypt_token
from agent.store import TypedStore, now_iso

WORKSPACE_MCPS_NAMESPACE = ["workspace_mcps"]
_BLOCKED_HEADERS = {
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "proxy-authorization",
}
_SECRET_QUERY_SUFFIXES = (
    "apikey",
    "apitoken",
    "authtoken",
    "accesstoken",
    "refreshtoken",
    "authorization",
    "password",
    "secret",
)
_VALIDATION_MESSAGES = {
    "name": (
        "Connection name must start with a lowercase letter and contain only lowercase "
        "letters, numbers, hyphens, or underscores (1-32 characters); for example, incident"
    ),
    "url": (
        "Server URL must be HTTPS, at most 2048 characters, and contain no credentials, "
        "whitespace, or fragments; put authentication in headers"
    ),
    "transport": "Transport must be Streamable HTTP or SSE",
    "enabled": "Enabled must be true or false",
    "headers": (
        "Headers must have valid, unique names and plain-text values without line breaks; "
        "use at most 20 headers and 8192 characters per value"
    ),
    "allowed_tools": "Allowed tools must be a list of non-empty names (1-128 characters)",
    "oauth": "OAuth requires an HTTPS token URL, client ID, and client secret; use the client_credentials grant",
}


def _is_secret_query_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return normalized in {"key", "token"} or normalized.endswith(_SECRET_QUERY_SUFFIXES)


def _https_url(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or any(ord(char) < 33 for char in value)
        or any(_is_secret_query_key(key) for key, _ in parse_qsl(parsed.query))
    ):
        raise ValueError("Use an HTTPS URL without credentials or fragments")
    _ = parsed.port
    return value


class WorkspaceMCPOAuth(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)

    grant_type: Literal["client_credentials"] = "client_credentials"
    token_url: str = Field(max_length=2048)
    client_id: str = Field(min_length=1, max_length=2048)
    scope: str = Field(default="", max_length=2048)
    token_endpoint_auth_method: Literal["client_secret_post", "client_secret_basic"] = (
        "client_secret_post"
    )

    _token_url = field_validator("token_url")(_https_url)

    @field_validator("client_id")
    @classmethod
    def _client_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Client ID must not be empty")
        return value


class WorkspaceMCPOAuthUpdate(WorkspaceMCPOAuth):
    client_secret: str | None = Field(default=None, min_length=1, max_length=8192, repr=False)


class WorkspaceMCPRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def redacted_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # Error messages and nested locations can include submitted credentials.
                messages = dict.fromkeys(
                    _VALIDATION_MESSAGES.get(
                        error["loc"][1] if len(error["loc"]) > 1 else "",
                        "Invalid MCP connection settings",
                    )
                    for error in exc.errors()
                )
                return JSONResponse(status_code=422, content={"detail": "; ".join(messages)})

        return redacted_handler


class WorkspaceMCPUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    url: str = Field(max_length=2048)
    transport: Literal["streamable_http", "sse"] = "streamable_http"
    enabled: bool = True
    headers: dict[str, str] | None = Field(default=None, repr=False)
    allowed_tools: list[str] = Field(default_factory=list)
    oauth: WorkspaceMCPOAuthUpdate | None = Field(default=None, repr=False)

    _url = field_validator("url")(_https_url)

    @field_validator("headers")
    @classmethod
    def _headers(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        if len(value) > 20:
            raise ValueError("Use at most 20 headers")
        names: set[str] = set()
        for name, content in value.items():
            if (
                not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", name)
                or name.lower() in _BLOCKED_HEADERS
                or name.lower() in names
                or len(content) > 8192
                or any(ord(char) < 32 or ord(char) > 126 for char in content)
            ):
                raise ValueError("Invalid or duplicate authentication header")
            names.add(name.lower())
        return value

    @field_validator("allowed_tools")
    @classmethod
    def _tools(cls, value: list[str]) -> list[str]:
        if any(not name.strip() or len(name) > 128 for name in value):
            raise ValueError("Tool names must be non-empty and at most 128 characters")
        return list(dict.fromkeys(name.strip() for name in value))


class WorkspaceMCP(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    name: str
    url: str
    transport: Literal["streamable_http", "sse"] = "streamable_http"
    enabled: bool = True
    allowed_tools: list[str] = Field(default_factory=list)
    encrypted_headers: str = Field(default="", repr=False)
    header_names: list[str] = Field(default_factory=list)
    oauth: WorkspaceMCPOAuth | None = None
    encrypted_client_secret: str = Field(default="", repr=False)
    revision: str
    updated_at: str

    def public(self) -> dict[str, Any]:
        return self.model_dump(exclude={"encrypted_headers", "encrypted_client_secret"})

    def connection_headers(self) -> dict[str, str]:
        if not self.encrypted_headers:
            return {}
        decrypted = decrypt_token(self.encrypted_headers)
        if not decrypted:
            raise ValueError("MCP authentication headers could not be decrypted")
        return json.loads(decrypted)


_store = TypedStore(WORKSPACE_MCPS_NAMESPACE, WorkspaceMCP)


async def get_workspace_mcp(name: str) -> WorkspaceMCP | None:
    return await _store.get(name)


async def list_workspace_mcp_records() -> list[WorkspaceMCP]:
    return sorted(await _store.search_all(), key=lambda record: record.name)


async def list_workspace_mcps() -> list[dict[str, Any]]:
    return [record.public() for record in await list_workspace_mcp_records()]


async def prepare_workspace_mcp(name: str, update: WorkspaceMCPUpdate) -> WorkspaceMCP:
    """Validate a draft and resolve saved authentication without persisting it."""
    if name != update.name:
        raise ValueError("Connection name must match its URL path")
    previous = await get_workspace_mcp(name)
    if previous and previous.url != update.url and previous.header_names and update.headers is None:
        raise ValueError("Replace or clear authentication headers when changing the server URL")
    encrypted = previous.encrypted_headers if previous else ""
    header_names = previous.header_names if previous else []
    if update.headers is not None:
        encrypted = encrypt_token(json.dumps(update.headers)) if update.headers else ""
        header_names = sorted(update.headers)
    oauth = update.oauth
    if "oauth" not in update.model_fields_set and previous and previous.oauth:
        oauth = WorkspaceMCPOAuthUpdate(**previous.oauth.model_dump())
    oauth_settings = None
    encrypted_client_secret = ""
    if oauth:
        if any(header.lower() == "authorization" for header in header_names):
            raise ValueError("Remove the Authorization header when using OAuth")
        oauth_settings = WorkspaceMCPOAuth(**oauth.model_dump(exclude={"client_secret"}))
        if oauth.client_secret is not None:
            encrypted_client_secret = encrypt_token(oauth.client_secret)
        elif (
            previous
            and previous.oauth
            and previous.url == update.url
            and previous.oauth.token_url == oauth.token_url
            and previous.oauth.client_id == oauth.client_id
        ):
            encrypted_client_secret = previous.encrypted_client_secret
        if not encrypted_client_secret:
            raise ValueError("Provide a client secret for new or changed OAuth connections")
    return WorkspaceMCP(
        **update.model_dump(exclude={"headers", "oauth"}),
        encrypted_headers=encrypted,
        header_names=header_names,
        oauth=oauth_settings,
        encrypted_client_secret=encrypted_client_secret,
        revision=uuid4().hex,
        updated_at=now_iso(),
    )


async def save_workspace_mcp(name: str, update: WorkspaceMCPUpdate) -> dict[str, Any]:
    record = await prepare_workspace_mcp(name, update)
    await _store.put(name, record)
    return record.public()


async def delete_workspace_mcp(name: str) -> None:
    await _store.delete(name)
