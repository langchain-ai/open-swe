"""Cached LMT listings, held encrypted.

A person's kit tools and servers are theirs alone, so the cache shared by every replica
keeps them only as ciphertext (``TOKEN_ENCRYPTION_KEY``), under that person's own key.
They carry no credential: LMT lists tool definitions and server states, and hands out
consent links and credentials only when a tool is called, so nothing in them expires.
"""

import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from time import time
from typing import TYPE_CHECKING

from openswe.encryption import decrypt_token, encrypt_token

if TYPE_CHECKING:
    from langgraph_api.cache import SWRResult

# Encrypted entries live under their own keys, so a replica still reading the plaintext
# format during a rollout never reads (or overwrites) one.
_KEY_PREFIX = "sealed-v1:"
# When a listing was last read from its source; outlasts any listing begun before then.
_READ_AT_TTL = timedelta(minutes=5)


class SealedListing:
    """One caller's listing in the shared cache: answered from the cached copy, read again
    in the background once ``fresh_for`` old, and kept up to ``max_age``."""

    def __init__(
        self,
        key: str,
        load: Callable[[], Awaitable[object]],
        *,
        fresh_for: timedelta,
        max_age: timedelta,
    ) -> None:
        self.key = _KEY_PREFIX + key
        self._read_at_key = f"{self.key}:read-at"
        self._load = load
        self._fresh_for = fresh_for
        self._max_age = max_age

    async def lookup(self) -> SWRResult[object]:
        """The cache entry as stored: its value is ciphertext, for :meth:`open`."""
        from langgraph_api.cache import swr

        return await swr(
            self.key, self._load_sealed, fresh_for=self._fresh_for, max_age=self._max_age
        )

    async def _load_sealed(self) -> str:
        from langgraph_api.cache import cache_get

        started = time()
        value = await self._load()
        # Each replica guards only its own writes, so a listing begun before the last read
        # from the source, on any replica, reads again rather than store what came before.
        read_at = await cache_get(self._read_at_key)
        if isinstance(read_at, (int, float)) and started < read_at:
            value = await self._load()
        return encrypt_token(json.dumps(value, separators=(",", ":")))

    @staticmethod
    async def open(stored: object) -> object | None:
        """A stored value's listing; None when it can't be read (a retired key, no key, a
        damaged entry), which callers treat as nothing cached."""
        if not isinstance(stored, str):
            return None
        # decrypt_token logs a failure, never the plaintext, and returns "" for it.
        plaintext = decrypt_token(stored)
        if not plaintext:
            return None
        try:
            return json.loads(plaintext)
        except ValueError:
            return None

    async def read(self, *, fresh: bool = False) -> object | None:
        """The listing, from the cached copy unless ``fresh`` (or the copy can't be read),
        when it is read from its source now."""
        if fresh:
            return await self._read_now()
        result = await self.lookup()
        value = await self.open(result.value)
        if value is None:
            value = await self.open(await result.mutate())
        return value

    async def refresh(self) -> None:
        """Read the listing from its source now and keep it."""
        await self._read_now()

    async def _read_now(self) -> object | None:
        """The listing from its source, read after this call began and stored over the
        cached copy; any listing begun earlier reads again rather than overwrite it."""
        from langgraph_api.cache import cache_set

        await cache_set(self._read_at_key, time(), ttl=_READ_AT_TTL)
        result = await self.lookup()
        # A miss or an expired entry was just loaded, by this lookup or one it joined,
        # which read again if it began before the mark.
        if result.status in ("miss", "expired"):
            return await self.open(result.value)
        # Writing a value, rather than joining a listing in flight, also stops an older
        # one on this replica from storing.
        return await self.open(await result.mutate(await self._load_sealed()))
