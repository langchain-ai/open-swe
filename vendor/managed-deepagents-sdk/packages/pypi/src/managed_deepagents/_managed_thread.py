"""``runtime.thread``: the conversation a run belongs to, from the thread record.

A run's channel is the live binding of the message that started it, else (an
old cron) the binding the cron captured, else the thread record's origin. The
record comes from the run's metadata, which the Agent Server copies from the
thread. The runtime's auth keeps every ``ls_mda_*`` key out of client writes,
and in-process calls that skip it are agent code, which is trusted.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from langgraph.config import get_config

from managed_deepagents._channels.binding import (
    ChannelBinding,
    origin_binding,
    resolve_channel_binding,
    runtime_channel,
)
from managed_deepagents._channels.runtime import RuntimeChannel  # noqa: TC001 - dataclass field
from managed_deepagents._identity_runtime import _field
from managed_deepagents._thread_record import ThreadRecord, parse_thread_record

if TYPE_CHECKING:
    from managed_deepagents._channels.types import ChannelEvent


@dataclass(frozen=True)
class ThreadParticipant:
    """A person on the thread."""

    #: The principal id, as ``runtime.server_info.principal.id`` shows it.
    id: str
    role: Literal["owner", "member"]


@dataclass(frozen=True)
class ThreadOrigin:
    """The channel conversation the thread came from, and who sent its latest message."""

    channel: RuntimeChannel
    #: ``None`` when the sender is not a participant (not a person).
    sender: ThreadParticipant | None


@dataclass(frozen=True)
class ManagedThread:
    """The thread a run belongs to: an async subagent's is its lead's."""

    #: The LangGraph thread id.
    id: str
    origin: ThreadOrigin | None
    participants: list[ThreadParticipant] = field(default_factory=list)


@dataclass(frozen=True)
class RunThread:
    """``runtime.thread`` with the route its origin posts to, for delivery."""

    thread: ManagedThread
    #: The binding the origin channel posts to; ``None`` without an origin.
    origin: ChannelBinding | None = None


#: The run's thread, set by ``ManagedCompiledAgent`` for each step of a run.
_current_thread: ContextVar[RunThread | None] = ContextVar("mda_thread", default=None)
#: The lead thread an async subagent run may read, with its metadata, set once
#: per run by ``parent_run`` after its owner check.
_verified_lead_metadata: ContextVar[tuple[str, Mapping[str, object]] | None] = ContextVar(
    "mda_verified_lead_metadata", default=None
)


def no_thread() -> ManagedThread:
    """The thread of a runtime outside a run."""
    return ManagedThread(id="", origin=None)


def build_run_thread(
    *,
    thread_id: object,
    live: ChannelBinding | None,
    metadata: object,
    configurable: Mapping[str, object] | None,
    lead_thread_id: str | None = None,
) -> RunThread:
    """Build a run's thread once; an async subagent run (``lead_thread_id``) gets its lead's."""
    if lead_thread_id is not None:
        verified = _verified_lead_metadata.get()
        thread_id = lead_thread_id
        metadata = verified[1] if verified is not None and verified[0] == lead_thread_id else None
    record = parse_thread_record(metadata)
    binding = live
    if binding is None and record is not None and record.origin is not None:
        binding = origin_binding(record.origin.channel, _schedule_event(metadata, configurable))
    participants = _participants(record)
    origin = None
    if binding is not None:
        sender_id = record.origin.sender if record is not None and record.origin else None
        sender = next((person for person in participants if person.id == sender_id), None)
        origin = ThreadOrigin(channel=runtime_channel(binding), sender=sender)
    thread = ManagedThread(
        id=thread_id if isinstance(thread_id, str) else "",
        origin=origin,
        participants=participants,
    )
    return RunThread(thread=thread, origin=binding)


def current_run_thread(runtime: object, configurable: Mapping[str, object] | None) -> RunThread:
    """The run's thread: the one its graph set, else read from what ``runtime`` carries."""
    current = _current_thread.get()
    if current is not None:
        return current
    return build_run_thread(
        thread_id=(configurable or {}).get("thread_id"),
        live=resolve_channel_binding(getattr(runtime, "context", None), configurable),
        metadata=_run_metadata(runtime),
        configurable=configurable,
    )


def on_channel_thread() -> bool:
    """Whether the running graph continues a channel's conversation: its thread has an origin.

    That conversation holds content from outside the workspace, also on a
    Studio resume, which reads the thread record from its run's metadata.
    ``False`` outside a run.
    """
    configurable = None
    if _current_thread.get() is None:
        try:
            configurable = get_config().get("configurable")
        except RuntimeError:
            # Called outside a runnable context.
            return False
    return current_run_thread(None, configurable).thread.origin is not None


def _participants(record: ThreadRecord | None) -> list[ThreadParticipant]:
    if record is None:
        return []
    return [ThreadParticipant(id=key, role=role) for key, role in record.participants.items()]


def _schedule_event(
    metadata: object, configurable: Mapping[str, object] | None
) -> ChannelEvent | None:
    """A scheduled run's event: whether its schedule posts in a new provider thread.

    The cron's metadata, copied into the run, holds the schedule's spec.
    """
    if _field(_field(configurable, "langgraph_auth_user"), "mda_scheduled_run") is not True:
        return None
    spec = _field(metadata, "mda_schedule_spec")
    if not isinstance(spec, Mapping):
        return None
    return {"type": "schedule", "new_thread": spec.get("new_thread") is True}


def _run_metadata(runtime: object) -> object:
    config = getattr(runtime, "config", None)
    if isinstance(config, Mapping):
        return config.get("metadata")
    try:
        return get_config().get("metadata")
    except RuntimeError:
        # Outside a runnable context.
        return None


__all__ = [
    "ManagedThread",
    "RunThread",
    "ThreadOrigin",
    "ThreadParticipant",
    "build_run_thread",
    "current_run_thread",
    "no_thread",
    "on_channel_thread",
]
