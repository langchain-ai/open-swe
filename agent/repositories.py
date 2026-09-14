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


class Repository(BaseModel):
    model_config = ConfigDict(extra="ignore")

    full_name: str
    owner: str = ""
    name: str = ""
    private: bool | None = None
    default_branch: str = ""
    first_seen_at: str = ""
    last_activity_at: str = ""

    @field_validator("full_name", mode="before")
    @classmethod
    def _normalize_full_name(cls, value: str) -> str:
        return normalize_repo_full_name(value)

    @classmethod
    def key(cls, full_name: str) -> str:
        return normalize_repo_full_name(full_name).lower()

    @classmethod
    def store(cls) -> TypedStore[Self]:
        return TypedStore(REPOSITORIES_NAMESPACE, cls)

    @classmethod
    def seed(cls, full_name: str) -> Self:
        normalized = normalize_repo_full_name(full_name)
        owner, name = normalized.split("/", 1)
        timestamp = now_iso()
        return cls(
            full_name=normalized,
            owner=owner,
            name=name,
            first_seen_at=timestamp,
            last_activity_at=timestamp,
        )

    @classmethod
    async def get(cls, full_name: str) -> Self | None:
        return await cls.store().get(cls.key(full_name))

    @classmethod
    async def all(cls) -> list[Self]:
        return await cls.store().search_all()

    @classmethod
    async def record(
        cls,
        full_name: str,
        *,
        private: bool | None = None,
        default_branch: str | None = None,
    ) -> Self:
        """Upsert the repository record and stamp it as active."""
        key = cls.key(full_name)
        record = await cls.store().get(key) or cls.seed(full_name)
        if private is not None:
            record.private = private
        if default_branch:
            record.default_branch = default_branch
        record.last_activity_at = now_iso()
        return await cls.store().put(key, record)
