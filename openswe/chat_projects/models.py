"""Validated personal project configuration and dashboard responses."""

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from sqlalchemy import RowMapping


class LocalFolder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    deviceId: str = Field(min_length=1, max_length=128)

    @field_validator("path")
    @classmethod
    def absolute_path(cls, value: str) -> str:
        if "\x00" in value or not (
            PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()
        ):
            raise ValueError("path must be an absolute local folder path")
        return value

    @field_validator("deviceId")
    @classmethod
    def nonempty_device(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("deviceId must be a nonempty opaque device identifier")
        return value


class ProjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    repo: str | None = Field(default=None, max_length=256)
    localFolder: LocalFolder | None = None

    @field_validator("name", mode="before")
    @classmethod
    def trimmed_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("repo")
    @classmethod
    def repository_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}", value):
            raise ValueError("repo must be owner/name")
        if value.split("/")[1] in {".", ".."}:
            raise ValueError("repo must be owner/name")
        return value

    @model_validator(mode="after")
    def one_context(self) -> Self:
        if self.repo is not None and self.localFolder is not None:
            raise ValueError("repo and localFolder are mutually exclusive")
        return self

    def context_columns(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "repo": self.repo,
            "local_path": self.localFolder.path if self.localFolder else None,
            "device_id": self.localFolder.deviceId if self.localFolder else None,
        }


class ChatProject(ProjectBody):
    id: UUID

    @classmethod
    def from_row(cls, row: RowMapping) -> Self:
        return cls.model_validate(
            {
                "id": row["id"],
                "name": row["name"],
                "repo": row["repo"],
                "localFolder": (
                    {"path": row["local_path"], "deviceId": row["device_id"]}
                    if row["local_path"] is not None
                    else None
                ),
            }
        )


class ProjectMembershipBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projectId: UUID | None


class ProjectCollection(BaseModel):
    projects: list[ChatProject]
    memberships: dict[str, str]


class ProjectThreadsPage(BaseModel):
    items: list[dict[str, JsonValue]]
    limit: int
    offset: int
    hasMore: bool
