"""An in-memory stand-in for the ``repository`` table in tests without PostgreSQL."""

from collections.abc import Sequence
from typing import Self
from uuid import UUID

import pytest

from agent.github.repositories import Repository
from agent.review.styles import normalize_repo_full_name


class FakeRepositories:
    def __init__(self) -> None:
        self.rows: dict[str, Repository] = {}

    def add(self, full_name: str, *, private: bool | None = None) -> Repository:
        key = normalize_repo_full_name(full_name).lower()
        row = self.rows.get(key)
        if row is None:
            row = Repository(full_name=full_name, private=private)
            self.rows[key] = row
        elif private is not None:
            row.private = private
        return row

    async def ensure(self, full_name: str, *, private: bool | None = None) -> Repository:
        return self.add(full_name, private=private)

    async def get(self, full_name: str) -> Repository | None:
        return self.rows.get(normalize_repo_full_name(full_name).lower())

    async def get_many(self, ids: Sequence[UUID]) -> list[Repository]:
        by_id = {row.id: row for row in self.rows.values()}
        return [by_id[id] for id in ids if id in by_id]

    def install(self, monkeypatch: pytest.MonkeyPatch) -> Self:
        """Route every ``Repository`` lookup and upsert through this fake."""
        rows = self.rows

        async def save(row: Repository, session: object | None = None) -> Repository:
            rows[row.key] = row
            return row

        monkeypatch.setattr(Repository, "ensure", self.ensure)
        monkeypatch.setattr(Repository, "get", self.get)
        monkeypatch.setattr(Repository, "get_many", self.get_many)
        monkeypatch.setattr(Repository, "save", save)
        return self
