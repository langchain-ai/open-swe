"""Cached LMT listings, held encrypted.

A person's kit tools and servers are theirs alone, so the cache shared by every replica
keeps them only as ciphertext (``TOKEN_ENCRYPTION_KEY``), under that person's own key.
They carry no credential: LMT lists tool definitions and server states, and hands out
consent links and credentials only when a tool is called, so nothing in them expires.
"""

import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TYPE_CHECKING

from openswe.encryption import decrypt_token, encrypt_token

if TYPE_CHECKING:
    from langgraph_api.cache import SWRResult

# Encrypted entries live under their own keys, so a replica still reading the plaintext
# format during a rollout never reads (or overwrites) one.
_KEY_PREFIX = "sealed-v1:"


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
        result = await self.lookup()
        value = await self.open(result.value)
        # A miss or an expired entry was just loaded; only a cached copy is read again.
        if value is None or (fresh and result.status in ("fresh", "stale")):
            value = await self.open(await result.mutate())
        return value

    async def refresh(self) -> None:
        """Read the listing from its source now, if a copy is cached."""
        result = await self.lookup()
        if result.status in ("fresh", "stale"):
            await result.mutate()
