"""User-authenticated pull request label management."""

from typing import Self

from fastapi import HTTPException
from pydantic import BaseModel, TypeAdapter

from openswe.github.http import GitHubError
from openswe.github.pull_request_status import PullRequestClient


class PullRequestLabel(BaseModel):
    name: str
    color: str
    description: str | None = None


_LABELS = TypeAdapter(list[PullRequestLabel])


class PullRequestLabels(BaseModel):
    available: list[PullRequestLabel]
    selected: list[PullRequestLabel]

    @classmethod
    async def read(cls, pull: PullRequestClient) -> Self:
        return cls(
            available=_LABELS.validate_python(await pull.repo.labels()),
            selected=_LABELS.validate_python(await pull.labels()),
        )


class LabelChange(BaseModel):
    name: str
    selected: bool

    async def apply(self, pull: PullRequestClient) -> None:
        try:
            if self.selected:
                await pull.add_label(self.name)
            else:
                await pull.remove_label(self.name)
        except GitHubError as exc:
            raise HTTPException(
                exc.response.status_code, "Could not update label; check your GitHub permissions"
            ) from exc
