"""Tracks Slack threads the bot has already replied in.

Lets follow-up messages in those threads bypass the @mention gate, so users
don't have to re-tag the bot on every reply. Mirrors openclaw's
`sent-thread-cache.ts` with a 24h TTL and a simple size bound.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict

_TTL_SECONDS = 24 * 60 * 60
_MAX_ENTRIES = 5000

_lock = threading.Lock()
_entries: OrderedDict[str, float] = OrderedDict()


def _make_key(channel_id: str, thread_ts: str) -> str:
    return f"{channel_id}:{thread_ts}"


def _prune_locked(now: float) -> None:
    expired = [key for key, expires_at in _entries.items() if expires_at <= now]
    for key in expired:
        _entries.pop(key, None)
    while len(_entries) > _MAX_ENTRIES:
        _entries.popitem(last=False)


def record_slack_thread_participation(channel_id: str, thread_ts: str) -> None:
    if not channel_id or not thread_ts:
        return
    key = _make_key(channel_id, thread_ts)
    now = time.time()
    with _lock:
        _prune_locked(now)
        _entries[key] = now + _TTL_SECONDS
        _entries.move_to_end(key)


def has_slack_thread_participation(channel_id: str, thread_ts: str) -> bool:
    if not channel_id or not thread_ts:
        return False
    key = _make_key(channel_id, thread_ts)
    now = time.time()
    with _lock:
        expires_at = _entries.get(key)
        if expires_at is None:
            return False
        if expires_at <= now:
            _entries.pop(key, None)
            return False
        return True


def clear_slack_thread_participation_cache() -> None:
    with _lock:
        _entries.clear()
