"""The opaque cursor a windowed transcript read pages backwards with.

It encodes the keyset boundary of a page already delivered — the oldest turn's
``(requested_at, turn_id)`` — and the thread it belongs to. The boundary is
derived from event content rather than from a row id, so it survives a
projection rebuild: replaying the log reproduces the same turn ids and the same
``requested_at``, and every cursor a client is holding stays valid.

The thread id is embedded so a cursor can never be replayed against a different
thread; the read path rejects a foreign one rather than quietly serving that
thread's newest page. Clients treat the string as opaque.
"""

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, kw_only=True)
class TurnPageCursor:
    """Exclusive boundary: the next page holds turns strictly older than this."""

    thread_id: str
    before_requested_at: datetime
    before_turn_id: UUID


def encode_turn_cursor(cursor: TurnPageCursor) -> str:
    payload = json.dumps(
        {
            "t": cursor.thread_id,
            "a": cursor.before_requested_at.isoformat(),
            "i": str(cursor.before_turn_id),
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_turn_cursor(encoded: str) -> TurnPageCursor | None:
    """The cursor, or ``None`` for anything that is not one of ours."""
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        parsed = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    thread_id, requested_at, turn_id = parsed.get("t"), parsed.get("a"), parsed.get("i")
    if not isinstance(thread_id, str) or not thread_id:
        return None
    if not isinstance(requested_at, str) or not isinstance(turn_id, str):
        return None
    try:
        return TurnPageCursor(
            thread_id=thread_id,
            before_requested_at=datetime.fromisoformat(requested_at),
            before_turn_id=UUID(turn_id),
        )
    except ValueError:
        return None
