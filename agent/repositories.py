"""Repositories as records: the owner every pull request record belongs to.

Keyed by the lowercased ``owner/name`` because GitHub resolves repository paths
case-insensitively; ``full_name`` keeps the casing GitHub reported for display.
"""

import logging
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator

from agent.review.styles import normalize_repo_full_name
from agent.store import TypedStore, now_iso

logger = logging.getLogger(__name__)

REPOSITORIES_NAMESPACE: list[str] = ["repositories"]

_IDENTITY_FIELDS = frozenset({"full_name", "first_seen_at", "last_activity_at"})


class Repository(BaseModel):
    model_config = ConfigDict(extra="ignore")

    full_name: str
    private: bool | None = None
    default_branch: str = ""
    first_seen_at: str = ""
    last_activity_at: str = ""

    @field_validator("full_name", mode="before")
    @classmethod
    def _normalize_full_name(cls, value: str) -> str:
        return normalize_repo_full_name(value)

    @classmethod
    def store(cls) -> TypedStore[Self]:
        return TypedStore(REPOSITORIES_NAMESPACE, cls)

    @classmethod
    async def get(cls, full_name: str) -> Self | None:
        return await cls.store().get(cls(full_name=full_name).key)

    @classmethod
    async def all(cls) -> list[Self]:
        return await cls.store().search_all()

    @property
    def key(self) -> str:
        return self.full_name.lower()

    @property
    def owner(self) -> str:
        return self.full_name.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.full_name.split("/", 1)[1]

    async def save(self) -> Self:
        """Merge into the stored record: fields set on this instance win, unknowns don't."""
        stored = await self.get(self.full_name) or type(self)(
            full_name=self.full_name, first_seen_at=now_iso()
        )
        for field in self.model_fields_set - _IDENTITY_FIELDS:
            value = getattr(self, field)
            if value is not None:
                setattr(stored, field, value)
        stored.last_activity_at = now_iso()
        return await self.store().put(self.key, stored)
