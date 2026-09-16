"""Workspace rows, and the conversion between them and the domain record.

``workspace_repository`` and ``workspace_slack_channel`` key on the bound
resource rather than on the workspace, so the database — not the application —
is what guarantees that a repository or a Slack channel belongs to exactly one
workspace.

:class:`agent.workspaces.store.Workspace` stays the domain and API shape; these
rows are only how it is stored, which is why the timestamps a record carries as
ISO strings are columns here and the two free-form fields are ``jsonb``. That
module reads these rows, so the record model is imported where it is used
instead of at module scope.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid7

from pydantic import JsonValue
from sqlalchemy import BigInteger, ForeignKey, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agent.database.orm import NOW, Base

if TYPE_CHECKING:
    from agent.workspaces.store import Workspace


class WorkspaceRow(Base):
    __tablename__ = "workspace"

    slug: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    prompt: Mapped[str] = mapped_column(server_default="", default="")
    setup_script: Mapped[str] = mapped_column(server_default="", default="")
    update_script: Mapped[str] = mapped_column(server_default="", default="")
    base_snapshot_id: Mapped[str | None] = mapped_column(default=None)
    mem_bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)
    vcpus: Mapped[int | None] = mapped_column(default=None)
    fs_capacity_bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)
    create_params: Mapped[dict[str, JsonValue]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default_factory=dict
    )
    snapshot_id: Mapped[str | None] = mapped_column(default=None)
    snapshot_name: Mapped[str | None] = mapped_column(default=None)
    # The status columns are plain text with a database check; the record model
    # is where their Literal types live.
    snapshot_status: Mapped[str] = mapped_column(server_default="none", default="none")
    status_message: Mapped[str | None] = mapped_column(default=None)
    snapshot_tag: Mapped[str | None] = mapped_column(default=None)
    source_sandbox_id: Mapped[str | None] = mapped_column(default=None)
    last_captured_at: Mapped[datetime | None] = mapped_column(default=None)
    refresh_status: Mapped[str] = mapped_column(server_default="never", default="never")
    refresh_kind: Mapped[str | None] = mapped_column(default=None)
    refresh_run_id: Mapped[str | None] = mapped_column(default=None)
    refresh_started_at: Mapped[datetime | None] = mapped_column(default=None)
    refresh_finished_at: Mapped[datetime | None] = mapped_column(default=None)
    refresh_log: Mapped[str | None] = mapped_column(default=None)
    refresh_error: Mapped[str | None] = mapped_column(default=None)
    refresh_cron_id: Mapped[str | None] = mapped_column(default=None)
    refresh_steps: Mapped[list[dict[str, JsonValue]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), default_factory=list
    )
    refresh_sandbox_id: Mapped[str | None] = mapped_column(default=None)
    created_by: Mapped[str] = mapped_column(server_default="", default="")
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class WorkspaceRepositoryRow(Base):
    __tablename__ = "workspace_repository"

    repository_id: Mapped[UUID] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), primary_key=True
    )
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"))
    linked_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class WorkspaceSlackChannelRow(Base):
    __tablename__ = "workspace_slack_channel"

    channel_id: Mapped[str] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"))
    linked_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


def to_workspace(row: WorkspaceRow, repos: list[str], channels: list[str]) -> Workspace:
    """The record this row stores, with the bindings that route to it.

    Validated on the way out, as every stored record was when workspaces lived
    in the LangGraph Store: the columns are typed, but ``create_params`` and
    ``refresh_steps`` hold whatever an older release wrote there.
    """
    from agent.workspaces.store import Workspace

    return Workspace.model_validate(
        {
            "slug": row.slug,
            "name": row.name,
            "prompt": row.prompt,
            "setup_script": row.setup_script,
            "update_script": row.update_script,
            "base_snapshot_id": row.base_snapshot_id,
            "repos": repos,
            "slack_channel_ids": channels,
            "mem_bytes": row.mem_bytes,
            "vcpus": row.vcpus,
            "fs_capacity_bytes": row.fs_capacity_bytes,
            "create_params": row.create_params,
            "snapshot_id": row.snapshot_id,
            "snapshot_name": row.snapshot_name,
            "snapshot_status": row.snapshot_status,
            "status_message": row.status_message,
            "snapshot_tag": row.snapshot_tag,
            "source_sandbox_id": row.source_sandbox_id,
            "last_captured_at": _iso(row.last_captured_at),
            "refresh_status": row.refresh_status,
            "refresh_kind": row.refresh_kind,
            "refresh_run_id": row.refresh_run_id,
            "refresh_started_at": _iso(row.refresh_started_at),
            "refresh_finished_at": _iso(row.refresh_finished_at),
            "refresh_log": row.refresh_log,
            "refresh_error": row.refresh_error,
            "refresh_cron_id": row.refresh_cron_id,
            "refresh_steps": row.refresh_steps,
            "refresh_sandbox_id": row.refresh_sandbox_id,
            "created_by": row.created_by,
            "created_at": _iso(row.created_at) or "",
            "updated_at": _iso(row.updated_at) or "",
        }
    )


def apply_workspace(row: WorkspaceRow, record: Workspace) -> None:
    """Write everything but the bindings onto ``row``.

    ``created_at`` is left to the column default when the record carries none,
    so an imported record keeps the timestamp it was created with and a fresh
    row gets the one it was inserted at.
    """
    row.name = record.name
    row.prompt = record.prompt
    row.setup_script = record.setup_script
    row.update_script = record.update_script
    row.base_snapshot_id = record.base_snapshot_id
    row.mem_bytes = record.mem_bytes
    row.vcpus = record.vcpus
    row.fs_capacity_bytes = record.fs_capacity_bytes
    row.create_params = dict(record.create_params)
    row.snapshot_id = record.snapshot_id
    row.snapshot_name = record.snapshot_name
    row.snapshot_status = record.snapshot_status
    row.status_message = record.status_message
    row.snapshot_tag = record.snapshot_tag
    row.source_sandbox_id = record.source_sandbox_id
    row.last_captured_at = _at(record.last_captured_at)
    row.refresh_status = record.refresh_status
    row.refresh_kind = record.refresh_kind
    row.refresh_run_id = record.refresh_run_id
    row.refresh_started_at = _at(record.refresh_started_at)
    row.refresh_finished_at = _at(record.refresh_finished_at)
    row.refresh_log = record.refresh_log
    row.refresh_error = record.refresh_error
    row.refresh_cron_id = record.refresh_cron_id
    row.refresh_steps = [step.model_dump(mode="json") for step in record.refresh_steps]
    row.refresh_sandbox_id = record.refresh_sandbox_id
    row.created_by = record.created_by
    if created_at := _at(record.created_at):
        row.created_at = created_at
    row.updated_at = _at(record.updated_at) or datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _at(value: str | None) -> datetime | None:
    """A record's ISO timestamp as an aware one; an unreadable value reads as unset.

    Timestamps written before this table existed are whatever string the
    record carried, so a naive one is read as UTC rather than as the
    connection's time zone.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
