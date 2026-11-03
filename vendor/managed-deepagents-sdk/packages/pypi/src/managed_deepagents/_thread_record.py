"""The thread record: where a channel thread came from and who takes part in it.

Managed ingress keeps it in thread metadata, which the Agent Server copies into
every run on the thread, so a run that no channel message started (a Studio
resume, a task notification, a cron) still knows its conversation. Thread
metadata reaches traces, so the record holds routing and principal ids only,
never message text or provider events.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, JsonValue, ValidationError

from managed_deepagents._log import logger

if TYPE_CHECKING:
    from managed_deepagents._channels.binding import ChannelBinding

THREAD_RECORD_METADATA = "ls_mda_thread"
THREAD_PARTICIPANTS_METADATA = "ls_mda_participants"
#: The source thread key of a conversation a scheduled run started, on the run's own
#: thread. Ingress runs the conversation's events there.
THREAD_CONVERSATION_METADATA = "ls_mda_conversation"
#: Every metadata key with this prefix belongs to MDA; only managed ingress writes one.
THREAD_RECORD_METADATA_PREFIX = "ls_mda_"
_RECORD_VERSION = 1

ParticipantRole = Literal["owner", "member"]


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must be nonblank")
    return value


_Nonblank = Annotated[str, AfterValidator(_nonblank)]
_STRICT = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class StoredChannel(BaseModel):
    """A channel binding without its event, with its target stored as ``address``."""

    model_config = _STRICT
    name: _Nonblank
    provider: _Nonblank
    transport: Literal["trigger_server", "direct"]
    source_thread_key: _Nonblank
    address: JsonValue
    delivery_id: _Nonblank


class StoredOrigin(BaseModel):
    model_config = _STRICT
    channel: StoredChannel
    sender: str | None = None


class _StoredParticipant(BaseModel):
    model_config = _STRICT
    role: ParticipantRole


class _StoredRecord(BaseModel):
    model_config = _STRICT
    v: Literal[1]
    origin: object = None
    participants: dict[str, _StoredParticipant] = {}


@dataclass(frozen=True)
class ThreadRecord:
    """A parsed record; ``participants`` maps principal ids to roles in join order."""

    origin: StoredOrigin | None = None
    participants: Mapping[str, ParticipantRole] = field(default_factory=dict)


def is_person_principal(principal_id: object) -> bool:
    """Whether a principal id names a person: ``mda:*`` ids are MDA's own services."""
    return (
        isinstance(principal_id, str)
        and bool(principal_id.strip())
        and not principal_id.startswith("mda:")
    )


def origin_from_binding(binding: ChannelBinding, sender: str | None) -> StoredOrigin:
    """Store a binding without its event, keeping a Trigger channel id in ``address``."""
    target = binding["target"]
    address: object = target
    if binding["transport"] == "trigger_server" and isinstance(target, Mapping):
        trigger_address = target.get("address")
        address = {
            "channel_id": target.get("channel_id"),
            **(dict(trigger_address) if isinstance(trigger_address, Mapping) else {}),
        }
    # Validates the address as JSON, as the stored record must be.
    return StoredOrigin.model_validate(
        {
            "channel": {
                "name": binding["name"],
                "provider": binding["provider"],
                "transport": binding["transport"],
                "source_thread_key": binding["source_thread_key"],
                "address": address,
                "delivery_id": binding["delivery_id"],
            },
            "sender": sender if is_person_principal(sender) else None,
        }
    )


def target_from_origin(channel: StoredChannel) -> JsonValue:
    """The binding target an origin was stored from (the inverse of its ``address``)."""
    if channel.transport != "trigger_server" or not isinstance(channel.address, dict):
        return channel.address
    address = {key: value for key, value in channel.address.items() if key != "channel_id"}
    return {
        "channel_id": channel.address.get("channel_id"),
        "address": address,
        "source_thread_key": channel.source_thread_key,
    }


def parse_thread_record(metadata: object, *, exact: bool = False) -> ThreadRecord | None:
    """Read a record from thread or run metadata; ``None`` when there is none or it is invalid.

    A malformed origin keeps valid participants. With ``exact``, anything
    malformed (the origin, or a participant list that is not the participants'
    ids in order) rejects the whole record, as a write must be.
    """
    if not isinstance(metadata, Mapping):
        return None
    raw = metadata.get(THREAD_RECORD_METADATA)
    if raw is None:
        return None
    try:
        stored = _StoredRecord.model_validate(raw)
    except ValidationError:
        logger.warning("ignoring a malformed thread record")
        return None
    origin = _parsed_origin(stored.origin)
    listed = metadata.get(THREAD_PARTICIPANTS_METADATA)
    if exact and (
        (stored.origin is not None and origin is None) or listed != list(stored.participants)
    ):
        return None
    roles = {key: value.role for key, value in stored.participants.items()}
    # JSONB keeps no key order, so the participant list is what records who joined first.
    if (
        isinstance(listed, list)
        and all(isinstance(key, str) for key in listed)
        and sorted(listed) == sorted(roles)
    ):
        roles = {key: roles[key] for key in listed}
    return ThreadRecord(origin=origin, participants=roles)


def _parsed_origin(raw: object) -> StoredOrigin | None:
    if raw is None:
        return None
    try:
        return StoredOrigin.model_validate(raw)
    except ValidationError:
        logger.warning("ignoring a malformed thread record origin")
        return None


def next_thread_record(
    current: ThreadRecord | None, binding: ChannelBinding, sender: str
) -> ThreadRecord:
    """The record after a channel message: its origin, and the sender as a participant.

    The first person on a thread owns it; later people join as members and no
    one's role changes.
    """
    participants = dict(current.participants) if current is not None else {}
    if is_person_principal(sender) and sender not in participants:
        participants[sender] = "member" if participants else "owner"
    return ThreadRecord(origin=origin_from_binding(binding, sender), participants=participants)


def thread_record_metadata(record: ThreadRecord) -> dict[str, JsonValue]:
    """The metadata keys that store a record."""
    stored: dict[str, JsonValue] = {
        "v": _RECORD_VERSION,
        "participants": {key: {"role": role} for key, role in record.participants.items()},
    }
    if record.origin is not None:
        stored["origin"] = record.origin.model_dump(mode="json")
    return {
        THREAD_RECORD_METADATA: stored,
        THREAD_PARTICIPANTS_METADATA: list(record.participants),
    }


def strip_thread_record_keys(
    metadata: object, *, keep_record: bool = False, keep_conversation: bool = False
) -> None:
    """Remove every ``ls_mda_*`` key from a metadata dict, in place.

    With ``keep_record``, the two keys a record is stored in stay; with
    ``keep_conversation``, the conversation key stays too.
    """
    if not isinstance(metadata, dict):
        return
    kept = {
        *(_RECORD_KEYS if keep_record else ()),
        *((THREAD_CONVERSATION_METADATA,) if keep_conversation else ()),
    }
    for key in [key for key in metadata if str(key).startswith(THREAD_RECORD_METADATA_PREFIX)]:
        if key not in kept:
            del metadata[key]


_RECORD_KEYS = (THREAD_RECORD_METADATA, THREAD_PARTICIPANTS_METADATA)


def names_thread_record_key(metadata: object) -> bool:
    """Whether metadata names any ``ls_mda_*`` key."""
    return isinstance(metadata, Mapping) and any(
        str(key).startswith(THREAD_RECORD_METADATA_PREFIX) for key in metadata
    )


__all__ = [
    "THREAD_CONVERSATION_METADATA",
    "THREAD_PARTICIPANTS_METADATA",
    "THREAD_RECORD_METADATA",
    "THREAD_RECORD_METADATA_PREFIX",
    "StoredChannel",
    "StoredOrigin",
    "ThreadRecord",
    "is_person_principal",
    "names_thread_record_key",
    "next_thread_record",
    "origin_from_binding",
    "parse_thread_record",
    "strip_thread_record_keys",
    "target_from_origin",
    "thread_record_metadata",
]
