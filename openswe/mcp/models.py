"""Connection settings and encrypted credentials shared by MCP scopes."""

import json
import re
from collections.abc import Mapping
from typing import Any, Final, Literal, Self
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from runtime_api.connections import ConnectionRef

from openswe.encryption import decrypt_token, encrypt_token
from openswe.prompts import prompt
from openswe.utils.time import now_iso

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


_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}")

type MCPScope = Literal["instance", "workspace", "user", "managed"]

# Whose credential a tier's server reads from its MDA connection: the deployment's for
# the servers every run gets, the person's own for theirs.
CONNECTION_OWNERS: Final[Mapping[MCPScope, Literal["agent", "user"]]] = {
    "instance": "agent",
    "workspace": "agent",
    "user": "user",
}


def _is_header_name(name: str) -> bool:
    return bool(_HEADER_NAME.fullmatch(name)) and name.lower() not in _BLOCKED_HEADERS


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


class MCPOAuth(BaseModel):
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


class MCPOAuthUpdate(MCPOAuth):
    client_secret: str | None = Field(default=None, min_length=1, max_length=8192, repr=False)


class MCPConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    url: str = Field(max_length=2048)
    transport: Literal["streamable_http", "sse"] = "streamable_http"
    enabled: bool = True
    headers: dict[str, str] | None = Field(default=None, repr=False)
    allowed_tools: list[str] = Field(default_factory=list)
    oauth: MCPOAuthUpdate | None = Field(default=None, repr=False)
    # An MDA connection holding the server's credential: the MDA run reads it and connects
    # to the server itself, so the backend keeps no headers or OAuth for it.
    connection: ConnectionRef | None = None
    # The header the connection's credential goes in as it is; None sends it as
    # ``Authorization: Bearer <credential>``.
    connection_header: str | None = None

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
                not _is_header_name(name)
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

    @field_validator("connection")
    @classmethod
    def _connection(cls, value: ConnectionRef | None) -> ConnectionRef | None:
        if value is not None and not value.for_mcp_server:
            raise ValueError("Name an MDA connection made for MCP servers: open-swe-mcp-<server>")
        return value

    @field_validator("connection_header")
    @classmethod
    def _connection_header(cls, value: str | None) -> str | None:
        if value is not None and not _is_header_name(value):
            raise ValueError("Invalid connection header name")
        return value


class MCPToolProvenance(BaseModel):
    """Credential-free provenance from the caller's authorized connection sources."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    connection_name: str
    scope: MCPScope
    overridden_scopes: list[MCPScope]

    def note(self) -> str:
        """What the description of each tool from this connection ends with, for the model."""
        return prompt("tools/mcp-provenance", provenance=self.model_dump_json())


class MCPToolDescription(BaseModel):
    name: str
    description: str = ""


class MCPConnectionPublic(BaseModel):
    """Connection settings safe to return to the dashboard; credentials stay server-side."""

    model_config = ConfigDict(hide_input_in_errors=True)

    name: str
    url: str
    transport: Literal["streamable_http", "sse"] = "streamable_http"
    enabled: bool = True
    allowed_tools: list[str] = Field(default_factory=list)
    header_names: list[str] = Field(default_factory=list)
    oauth: MCPOAuth | None = None
    connection: ConnectionRef | None = None
    connection_header: str | None = None
    revision: str
    updated_at: str


class MCPConnection(MCPConnectionPublic):
    encrypted_headers: str = Field(default="", repr=False)
    encrypted_client_secret: str = Field(default="", repr=False)

    @model_validator(mode="after")
    def _one_credential(self) -> Self:
        if self.connection is None:
            if self.connection_header is not None:
                raise ValueError("A connection header needs an MDA connection")
        elif (
            self.header_names
            or self.oauth
            or self.encrypted_headers
            or self.encrypted_client_secret
        ):
            raise ValueError("An MCP server whose credential is an MDA connection keeps none")
        return self

    def public(self) -> dict[str, Any]:
        return self.model_dump(exclude={"encrypted_headers", "encrypted_client_secret"})

    def allows_tool(self, name: str) -> bool:
        return name in self.allowed_tools

    @property
    def offers_tools(self) -> bool:
        return bool(self.allowed_tools)

    def connection_headers(self) -> dict[str, str]:
        if not self.encrypted_headers:
            return {}
        decrypted = decrypt_token(self.encrypted_headers)
        if not decrypted:
            raise ValueError("MCP authentication headers could not be decrypted")
        return json.loads(decrypted)


def _connection_backed(
    update: MCPConnectionUpdate, connection: ConnectionRef, header: str | None, scope: MCPScope
) -> MCPConnection:
    """A draft whose credential is an MDA connection, keeping none the previous record saved."""
    if update.headers or update.oauth:
        raise ValueError("Remove the headers and OAuth: the MDA connection holds the credential")
    owner = CONNECTION_OWNERS.get(scope)
    if connection.owner != owner:
        raise ValueError(
            "A personal MCP server reads its owner's own MDA connection (owner user)"
            if owner == "user"
            else "A shared MCP server reads the deployment's MDA connection (owner agent)"
        )
    return MCPConnection(
        **update.model_dump(exclude={"headers", "oauth", "connection", "connection_header"}),
        connection=connection,
        connection_header=header,
        revision=uuid4().hex,
        updated_at=now_iso(),
    )


async def prepare_connection(
    update: MCPConnectionUpdate, previous: MCPConnection | None = None, *, scope: MCPScope
) -> MCPConnection:
    """Resolve a draft's credentials using only the previous record in the same scope."""
    if previous and previous.name != update.name:
        raise ValueError("Connection name cannot change while reusing saved credentials")
    fields = update.model_fields_set
    if (
        previous
        and previous.connection
        and "connection" not in fields
        and not update.headers
        and update.oauth is None
    ):
        # An update that names no credential keeps the connection, as one omitting OAuth keeps it.
        if previous.url != update.url:
            raise ValueError("Name the MDA connection again when changing the server URL")
        header = (
            update.connection_header
            if "connection_header" in fields
            else previous.connection_header
        )
        return _connection_backed(update, previous.connection, header, scope)
    if update.connection is not None:
        return _connection_backed(update, update.connection, update.connection_header, scope)
    if update.connection_header is not None:
        raise ValueError("Name the MDA connection whose credential the header carries")
    if previous and previous.url != update.url and previous.header_names and update.headers is None:
        raise ValueError("Replace or clear authentication headers when changing the server URL")
    encrypted = previous.encrypted_headers if previous else ""
    header_names = previous.header_names if previous else []
    if update.headers is not None:
        encrypted = encrypt_token(json.dumps(update.headers)) if update.headers else ""
        header_names = sorted(update.headers)
    oauth = update.oauth
    if "oauth" not in update.model_fields_set and previous and previous.oauth:
        oauth = MCPOAuthUpdate(**previous.oauth.model_dump())
    oauth_settings = None
    encrypted_client_secret = ""
    if oauth:
        if any(header.lower() == "authorization" for header in header_names):
            raise ValueError("Remove the Authorization header when using OAuth")
        oauth_settings = MCPOAuth(**oauth.model_dump(exclude={"client_secret"}))
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
    return MCPConnection(
        **update.model_dump(exclude={"headers", "oauth"}),
        encrypted_headers=encrypted,
        header_names=header_names,
        oauth=oauth_settings,
        encrypted_client_secret=encrypted_client_secret,
        revision=uuid4().hex,
        updated_at=now_iso(),
    )
