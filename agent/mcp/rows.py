"""MCP connection rows, and the conversion between them and the domain record.

One row is one connection in one scope: a personal one belongs to a ``users``
row and a workspace one to a slug, so the two scopes can hold the same name and
a deleted person takes their connections with them.
:class:`agent.mcp.models.MCPConnection` stays the domain and API shape; the two
``encrypted_*`` columns hold what :mod:`agent.encryption` produced, verbatim.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid7

from pydantic import JsonValue
from sqlalchemy import ForeignKey, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from agent.database.orm import NOW, Base
from agent.mcp.models import MCPConnection


class MCPConnectionColumns(MappedAsDataclass, kw_only=True):
    """The settings and credentials both scopes store; each table owns its identity."""

    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    url: Mapped[str] = mapped_column(default="")
    revision: Mapped[str] = mapped_column(default="")
    # Plain text with a database check; the Literal type lives on the record model.
    transport: Mapped[str] = mapped_column(
        server_default="streamable_http", default="streamable_http"
    )
    enabled: Mapped[bool] = mapped_column(default=True)
    allowed_tools: Mapped[list[str]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), default_factory=list
    )
    header_names: Mapped[list[str]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), default_factory=list
    )
    oauth: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB, default=None)
    encrypted_headers: Mapped[str] = mapped_column(server_default="", default="")
    encrypted_client_secret: Mapped[str] = mapped_column(server_default="", default="")
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class UserMCPConnectionRow(MCPConnectionColumns, Base):
    __tablename__ = "user_mcp_connection"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str]


class WorkspaceMCPConnectionRow(MCPConnectionColumns, Base):
    __tablename__ = "workspace_mcp_connection"

    workspace_slug: Mapped[str]
    name: Mapped[str]


type MCPConnectionRow = UserMCPConnectionRow | WorkspaceMCPConnectionRow


def to_connection(row: MCPConnectionRow) -> MCPConnection:
    """The record this row stores, validated: the ``jsonb`` columns hold whatever was written."""
    return MCPConnection.model_validate(
        {
            "name": row.name,
            "url": row.url,
            "transport": row.transport,
            "enabled": row.enabled,
            "allowed_tools": row.allowed_tools,
            "header_names": row.header_names,
            "oauth": row.oauth,
            "encrypted_headers": row.encrypted_headers,
            "encrypted_client_secret": row.encrypted_client_secret,
            "revision": row.revision,
            "updated_at": _iso(row.updated_at) or "",
        }
    )


def apply_connection(row: MCPConnectionRow, record: MCPConnection) -> None:
    """Write every stored setting and credential onto ``row``."""
    row.url = record.url
    row.transport = record.transport
    row.enabled = record.enabled
    row.allowed_tools = list(record.allowed_tools)
    row.header_names = list(record.header_names)
    row.oauth = record.oauth.model_dump(mode="json") if record.oauth else None
    row.encrypted_headers = record.encrypted_headers
    row.encrypted_client_secret = record.encrypted_client_secret
    row.revision = record.revision
    row.updated_at = _at(record.updated_at) or datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _at(value: str | None) -> datetime | None:
    """A record's ISO timestamp as an aware one; an unreadable value reads as unset.

    Timestamps written before this table existed are whatever string the record
    carried, so a naive one is read as UTC rather than as the connection's time
    zone.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
