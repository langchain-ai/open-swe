"""Encrypted personal LangSmith credentials, refreshed at the point of use."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import text

from agent import database
from agent.encryption import decrypt_token, encrypt_token
from agent.langsmith_connection.oauth import (
    ConnectionError,
    Identity,
    Region,
    TokenResponse,
    issuer,
    request,
    token_request,
)
from agent.mcp.models import MCPConnection
from agent.mcp.runtime import discover_tools
from agent.store import TypedStore, now_iso


class Credential(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    method: Literal["oauth", "api_key"]
    region: Region = Region.US
    encrypted_token: str = Field(repr=False)
    encrypted_refresh_token: str = Field(default="", repr=False)
    client_id: str = ""
    expires_at: datetime | None = None
    refresh_until: datetime | None = None
    workspace_id: UUID | None = None
    identity: Identity | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    reconnect_required: bool = False
    revision: str = Field(default_factory=lambda: uuid4().hex)
    updated_at: str = Field(default_factory=now_iso)


class Status(BaseModel):
    connected: bool = False
    method: Literal["oauth", "api_key"] | None = None
    region: Region = Region.US
    email: str | None = None
    name: str | None = None
    workspace_id: UUID | None = None
    reconnect_required: bool = False


def store(login: str) -> TypedStore[Credential]:
    return TypedStore(["user_credentials", login.lower()], Credential)


@asynccontextmanager
async def lock(login: str) -> AsyncIterator[None]:
    async with database.transaction() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:subject, 0))"),
            {"subject": f"langsmith-credentials:{login.lower()}"},
        )
        yield


async def status(login: str) -> Status:
    record = await store(login).get("langsmith")
    if record is None:
        return Status()
    return Status(
        connected=not record.reconnect_required,
        method=record.method,
        region=record.region,
        workspace_id=record.workspace_id,
        email=record.identity.email if record.identity else None,
        name=record.identity.name if record.identity else None,
        reconnect_required=record.reconnect_required,
    )


def connection(record: Credential) -> MCPConnection:
    token = decrypt_token(record.encrypted_token)
    if not token:
        raise ConnectionError("Reconnect LangSmith in Profile Settings", status_code=400)
    headers = (
        {"Authorization": f"Bearer {token}"} if record.method == "oauth" else {"X-Api-Key": token}
    )
    if record.workspace_id:
        headers["X-Tenant-Id"] = str(record.workspace_id)
    return MCPConnection(
        name="personal_langsmith",
        url=issuer(record.region) + "/mcp",
        encrypted_headers=encrypt_token(json.dumps(headers)),
        header_names=list(headers),
        allowed_tools=record.allowed_tools,
        revision=record.revision,
        updated_at=record.updated_at,
    )


class Generation(BaseModel):
    revision: str = Field(default_factory=lambda: uuid4().hex)


def generations(login: str) -> TypedStore[Generation]:
    return TypedStore(["langsmith_credential_generations", login.lower()], Generation)


async def generation(login: str) -> str:
    async with lock(login):
        record = await generations(login).get("current")
        if record is None:
            record = await generations(login).put("current", Generation())
        return record.revision


async def save(login: str, record: Credential, expected_generation: str) -> Status:
    definitions = await discover_tools(connection(record), ("langsmith", login.lower()))
    record.allowed_tools = [tool.name for tool in definitions]
    async with lock(login):
        latest = await generations(login).get("current")
        if latest is None or latest.revision != expected_generation:
            raise ConnectionError("LangSmith connection changed; please retry", status_code=409)
        await store(login).put("langsmith", record)
        await generations(login).put("current", Generation())
    return await status(login)


async def connect_key(
    login: str, region: Region, api_key: SecretStr, workspace_id: UUID | None
) -> Status:
    expected_generation = await generation(login)
    headers = {"X-Api-Key": api_key.get_secret_value()}
    if workspace_id:
        headers["X-Tenant-Id"] = str(workspace_id)
    await request(
        region,
        "GET",
        "/v1/platform/workspaces/current/info" if workspace_id else "/orgs/current/info",
        headers=headers,
    )
    return await save(
        login,
        Credential(
            method="api_key",
            region=region,
            encrypted_token=encrypt_token(api_key.get_secret_value()),
            workspace_id=workspace_id,
        ),
        expected_generation,
    )


def oauth_record(
    tokens: TokenResponse, region: Region, client_id: str, identity: Identity
) -> Credential:
    return Credential(
        method="oauth",
        region=region,
        client_id=client_id,
        identity=identity,
        encrypted_token=encrypt_token(tokens.access_token.get_secret_value()),
        encrypted_refresh_token=encrypt_token(tokens.refresh_token.get_secret_value())
        if tokens.refresh_token
        else "",
        expires_at=datetime.now(UTC) + timedelta(seconds=tokens.expires_in),
        workspace_id=UUID(tokens.workspace_id) if tokens.workspace_id else None,
    )


async def disconnect(login: str) -> Status:
    async with lock(login):
        await generations(login).put("current", Generation())
        await store(login).delete("langsmith")
    return Status()


def needs_refresh(record: Credential) -> bool:
    return record.method == "oauth" and (
        record.expires_at is None or record.expires_at <= datetime.now(UTC) + timedelta(seconds=60)
    )


async def load(login: str) -> MCPConnection | None:
    while True:
        record = await store(login).get("langsmith")
        if record is None or record.reconnect_required:
            return None
        if not needs_refresh(record):
            return connection(record)
        if record.refresh_until and record.refresh_until > datetime.now(UTC):
            await asyncio.sleep(0.1)
            continue
        async with lock(login):
            latest = await store(login).get("langsmith")
            if latest is None or latest.revision != record.revision:
                continue
            refresh = decrypt_token(record.encrypted_refresh_token)
            if not refresh:
                record.reconnect_required = True
                await store(login).put("langsmith", record)
                return None
            record.revision = uuid4().hex
            record.refresh_until = datetime.now(UTC) + timedelta(seconds=30)
            await store(login).put("langsmith", record)
        return await refresh_connection(login, record, refresh)


async def refresh_connection(login: str, record: Credential, refresh: str) -> MCPConnection | None:
    try:
        tokens = await token_request(
            record.region,
            {
                "grant_type": "refresh_token",
                "client_id": record.client_id,
                "refresh_token": refresh,
                "resource": issuer(record.region) + "/mcp",
            },
        )
        record.encrypted_token = encrypt_token(tokens.access_token.get_secret_value())
        if tokens.refresh_token:
            record.encrypted_refresh_token = encrypt_token(tokens.refresh_token.get_secret_value())
        record.expires_at = datetime.now(UTC) + timedelta(seconds=tokens.expires_in)
        record.updated_at = now_iso()
    except ConnectionError as exc:
        if not exc.invalid_grant:
            raise
        record.reconnect_required = True
    finally:
        async with lock(login):
            latest = await store(login).get("langsmith")
            current = latest is not None and latest.revision == record.revision
            if current:
                record.revision = uuid4().hex
                record.refresh_until = None
                await store(login).put("langsmith", record)
    return connection(record) if current and not record.reconnect_required else None
