"""Inbox items a person has snoozed, one ``user_record`` per item."""

import re
from dataclasses import dataclass
from typing import Self

from openswe.store import now_ms
from openswe.users.records import UserRecords
from openswe.utils.json_types import JsonObject

# A snooze is kept a day past its wake time, then dropped the next time the list is read.
_EXPIRED_GRACE_MS = 24 * 60 * 60 * 1000
MAX_SNOOZE_MS = 90 * 24 * 60 * 60 * 1000
_ITEM_KEY = re.compile(r"cloud:[\w-]+|review:[\w.-]+/[\w.-]+#\d+")


def is_inbox_item_key(key: str) -> bool:
    return len(key) <= 256 and _ITEM_KEY.fullmatch(key) is not None


@dataclass(frozen=True)
class InboxSnooze:
    key: str
    until_ms: int
    snoozed_at_ms: int

    @classmethod
    def parse(cls, key: str, value: JsonObject) -> Self | None:
        until_ms, snoozed_at_ms = value.get("until_ms"), value.get("snoozed_at_ms")
        if not isinstance(until_ms, int) or not isinstance(snoozed_at_ms, int):
            return None
        return cls(key=key, until_ms=until_ms, snoozed_at_ms=snoozed_at_ms)

    def as_dict(self) -> JsonObject:
        return {"key": self.key, "until_ms": self.until_ms, "snoozed_at_ms": self.snoozed_at_ms}


class InboxSnoozes:
    """One person's snoozed inbox items."""

    _records = UserRecords("inbox_snooze")

    def __init__(self, login: str) -> None:
        self.login = login

    async def current(self) -> list[InboxSnooze]:
        cutoff = now_ms() - _EXPIRED_GRACE_MS
        snoozes: list[InboxSnooze] = []
        for key, value in (await self._records.list(self.login)).items():
            snooze = InboxSnooze.parse(key, value)
            if snooze is None or snooze.until_ms < cutoff:
                await self._records.delete(self.login, key)
                continue
            snoozes.append(snooze)
        return snoozes

    async def snooze(self, key: str, until_ms: int) -> InboxSnooze:
        snooze = InboxSnooze(key=key, until_ms=until_ms, snoozed_at_ms=now_ms())
        await self._records.put(
            self.login,
            {"until_ms": snooze.until_ms, "snoozed_at_ms": snooze.snoozed_at_ms},
            key=key,
        )
        return snooze

    async def wake(self, key: str) -> None:
        await self._records.delete(self.login, key)
