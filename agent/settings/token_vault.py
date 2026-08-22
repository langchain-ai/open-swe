"""One OAuth token vault, shared by every provider whose user tokens we store.

Providers differ in four things only: where a user's record lives, which
encrypted fields hold the access and refresh tokens, how a refresh is
performed, and which refresh failures are permanent. Everything else is the
same delicate machine — a per-login lock, an expiry check with skew,
double-checked refresh under the lock, dropping an authorization whose refresh
token is permanently dead, and *not* dropping one that a concurrent OAuth
callback replaced while the refresh was in flight — so it lives here once.
"""

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ..encryption import decrypt_token
from ..store import delete_value, get_value, put_value
from ..utils.timestamps import is_expired

logger = logging.getLogger(__name__)

DEFAULT_EXPIRY_SKEW_SECONDS = 300

Location = tuple[Sequence[str], str]


def expires_at_from_response(data: dict[str, Any], *, field: str = "expires_in") -> str | None:
    """Convert an OAuth response's ``*_expires_in`` seconds to an ISO timestamp."""
    raw = data.get(field)
    if not isinstance(raw, int | float) or raw <= 0:
        return None
    return (datetime.now(UTC) + timedelta(seconds=int(raw))).isoformat()


@dataclass(frozen=True)
class TokenFields:
    """Which keys of a stored record hold the encrypted tokens and the expiry."""

    access: str
    refresh: str
    expires_at: str = "token_expires_at"


class RefreshTokens(Protocol):
    async def __call__(
        self, *, login: str, refresh_token: str, record: dict[str, Any]
    ) -> dict[str, Any]:
        """Mint new tokens and return the record to store in place of ``record``."""
        ...


class TokenVault:
    def __init__(
        self,
        provider: str,
        *,
        locate: Callable[[str], Location],
        fields: TokenFields,
        refresh: RefreshTokens,
        is_permanently_dead: Callable[[BaseException], bool],
        expiry_skew_seconds: float = DEFAULT_EXPIRY_SKEW_SECONDS,
    ) -> None:
        self.provider = provider
        self.fields = fields
        self._locate = locate
        self._refresh = refresh
        self._is_permanently_dead = is_permanently_dead
        self._expiry_skew_seconds = expiry_skew_seconds
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_record(self, login: str) -> dict[str, Any] | None:
        namespace, key = self._locate(login)
        return await get_value(namespace, key)

    async def store_record(self, login: str, record: dict[str, Any]) -> None:
        namespace, key = self._locate(login)
        await put_value(namespace, key, record)

    async def delete_record(self, login: str) -> None:
        namespace, key = self._locate(login)
        await delete_value(namespace, key)

    def access_token(self, record: dict[str, Any]) -> str | None:
        return decrypt_token(record.get(self.fields.access) or "") or None

    def refresh_token(self, record: dict[str, Any]) -> str | None:
        return decrypt_token(record.get(self.fields.refresh) or "") or None

    def needs_refresh(self, record: dict[str, Any]) -> bool:
        return is_expired(
            record.get(self.fields.expires_at), skew_seconds=self._expiry_skew_seconds
        )

    async def get_valid(self, login: str, *, force_refresh: bool = False) -> dict[str, Any] | None:
        """The user's record with a usable access token, refreshing near expiry.

        Returns the stored record unchanged when the access token is still
        good, a refresh is impossible, or a refresh failed transiently: an
        expiry is a hint, and the upstream is the authority on whether a token
        still works. ``None`` means the caller must send the user through the
        authorization flow again.
        """
        record = await self.get_record(login)
        if not record or not self.access_token(record):
            return None
        if not force_refresh and not self.needs_refresh(record):
            return record
        refresh_token = self.refresh_token(record)
        if not refresh_token:
            return record

        async with self._lock(login):
            record = await self.get_record(login)
            if not record or not self.access_token(record):
                return None
            if not force_refresh and not self.needs_refresh(record):
                return record
            refreshed, permanently_dead = await self._refreshed_record(login, record)
            if refreshed is not None:
                return refreshed
            if not permanently_dead:
                return record
            return await self._drop_dead_record(login, record)

    def _lock(self, login: str) -> asyncio.Lock:
        lock = self._locks.get(login)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[login] = lock
        return lock

    async def _refreshed_record(
        self, login: str, record: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, bool]:
        """``(stored refreshed record, refresh token is permanently dead)``."""
        refresh_token = self.refresh_token(record)
        if not refresh_token:
            return None, False
        try:
            refreshed = await self._refresh(login=login, refresh_token=refresh_token, record=record)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s token refresh failed for %s", self.provider, login, exc_info=True)
            return None, self._is_permanently_dead(exc)
        await self.store_record(login, refreshed)
        return (refreshed if self.access_token(refreshed) else None), False

    async def _drop_dead_record(self, login: str, failed: dict[str, Any]) -> dict[str, Any] | None:
        """Forget an authorization whose refresh token can never mint again.

        The OAuth callback writes a fresh authorization without taking this
        lock, so it can land while the refresh is in flight. Only drop the
        record if the stored refresh token is still the one that failed.
        """
        latest = await self.get_record(login)
        if latest and latest.get(self.fields.refresh) != failed.get(self.fields.refresh):
            return latest if self.access_token(latest) else None
        logger.info(
            "Dropping dead %s authorization for %s; re-authorization required",
            self.provider,
            login,
        )
        await self.delete_record(login)
        return None
