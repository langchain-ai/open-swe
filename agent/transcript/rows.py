"""ORM mappings for the transcript tables, one mapped dataclass per table.

``0023_thread_transcript`` is the source of truth for the schema; these classes
match it column for column. The engine and the read path write and read through
raw SQL — a projection is an upsert whose conflict clause is the behaviour, not
something an ORM flush can express — so these mappings exist to keep the schema
described in Python and to let other code join against the tables.

``ThreadRow`` maps the ``metadata`` column under the attribute name
``thread_metadata`` because ``metadata`` is reserved on a declarative class.
"""

from datetime import datetime
from uuid import UUID, uuid7

from pydantic import JsonValue
from sqlalchemy import ARRAY, BigInteger, ForeignKey, SmallInteger, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agent.database.orm import NOW, Base

_EMPTY_OBJECT = text("'{}'::jsonb")
_EMPTY_ARRAY = text("'{}'::text[]")
_ZERO = text("0")
_ONE = text("1")
# Bound here rather than in a class body: ``ThreadMessageRow`` maps a column
# named ``text``, which would shadow the imported construct inside it.
_FALSE = text("false")


class ThreadRow(Base):
    __tablename__ = "thread"

    thread_id: Mapped[str] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(server_default="agent", default="agent")
    version: Mapped[int] = mapped_column(BigInteger, server_default=_ZERO, default=0)
    status: Mapped[str] = mapped_column(server_default="idle", default="idle")
    active_run_id: Mapped[str | None] = mapped_column(default=None)
    title: Mapped[str | None] = mapped_column(default=None)
    thread_metadata: Mapped[dict[str, JsonValue]] = mapped_column(
        "metadata", JSONB, server_default=_EMPTY_OBJECT, default_factory=dict
    )
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class ThreadEventRow(Base):
    __tablename__ = "thread_event"

    thread_id: Mapped[str] = mapped_column(
        ForeignKey("thread.thread_id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_type: Mapped[str]
    actor_kind: Mapped[str]
    payload: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    event_id: Mapped[UUID] = mapped_column(unique=True, default_factory=uuid7)
    schema_version: Mapped[int] = mapped_column(SmallInteger, server_default=_ONE, default=1)
    run_id: Mapped[str | None] = mapped_column(default=None)
    turn_id: Mapped[UUID | None] = mapped_column(default=None)
    command_id: Mapped[str | None] = mapped_column(default=None)
    occurred_at: Mapped[datetime | None] = mapped_column(server_default=NOW, default=None)


class ThreadCommandReceiptRow(Base):
    __tablename__ = "thread_command_receipt"

    command_id: Mapped[str] = mapped_column(primary_key=True)
    thread_id: Mapped[str]
    status: Mapped[str]
    result_version: Mapped[int | None] = mapped_column(BigInteger, default=None)
    error: Mapped[str | None] = mapped_column(default=None)
    accepted_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class ThreadTurnRow(Base):
    __tablename__ = "thread_turn"

    turn_id: Mapped[UUID] = mapped_column(primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("thread.thread_id", ondelete="CASCADE"))
    state: Mapped[str]
    requested_at: Mapped[datetime]
    run_id: Mapped[str | None] = mapped_column(default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    completed_at: Mapped[datetime | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(default=None)
    base_commit: Mapped[str | None] = mapped_column(default=None)
    head_commit: Mapped[str | None] = mapped_column(default=None)
    changed_files: Mapped[list[str] | None] = mapped_column(JSONB, default=None)


class ThreadMessageRow(Base):
    __tablename__ = "thread_message"

    message_id: Mapped[str] = mapped_column(primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("thread.thread_id", ondelete="CASCADE"))
    turn_id: Mapped[UUID]
    version: Mapped[int] = mapped_column(BigInteger)
    role: Mapped[str]
    created_at: Mapped[datetime]
    text: Mapped[str] = mapped_column(server_default="", default="")
    reasoning: Mapped[str] = mapped_column(server_default="", default="")
    streaming: Mapped[bool] = mapped_column(server_default=_FALSE, default=False)
    namespace: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=_EMPTY_ARRAY, default_factory=list
    )
    sender: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB, default=None)
    images: Mapped[list[dict[str, JsonValue]] | None] = mapped_column(JSONB, default=None)


class ThreadToolCallRow(Base):
    __tablename__ = "thread_tool_call"

    tool_call_id: Mapped[str] = mapped_column(primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("thread.thread_id", ondelete="CASCADE"))
    turn_id: Mapped[UUID]
    version: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str]
    input: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    status: Mapped[str]
    started_at: Mapped[datetime]
    message_id: Mapped[str | None] = mapped_column(default=None)
    output_preview: Mapped[str | None] = mapped_column(default=None)
    output: Mapped[str | None] = mapped_column(default=None)
    output_truncated: Mapped[bool] = mapped_column(server_default=_FALSE, default=False)
    namespace: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=_EMPTY_ARRAY, default_factory=list
    )
    ended_at: Mapped[datetime | None] = mapped_column(default=None)
