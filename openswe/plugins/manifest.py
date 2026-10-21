"""The files a plugin repository carries.

A plugin is a directory with ``.openswe-plugin/plugin.json``. A marketplace is a
repository with ``.openswe-plugin/marketplace.json`` listing plugins and where
each one lives. The manifest names a self-contained HTML entry, the plugin's UI,
which the dashboard runs in a sandboxed frame; the plugin never runs on the
backend.
"""

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

MANIFEST_PATH = ".openswe-plugin/plugin.json"
MARKETPLACE_PATH = ".openswe-plugin/marketplace.json"

PluginName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$")]


class PluginPermission(StrEnum):
    """What a plugin may ask the dashboard to do for the person using it."""

    STORAGE = "storage"
    """Keep documents: private to that person, or shared per GitHub repository."""
    GITHUB_READ = "github:read"
    """Read files, trees, branches, history and pull requests in its repositories."""
    GITHUB_WRITE = "github:write"
    """Create branches, commit files and open pull requests in its repositories."""
    THREADS = "threads"
    """Start agent threads, each one only after the person approves its request."""
    REALTIME = "realtime"
    """Join live rooms: broadcast updates and presence to others with the same access."""


def _relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("must be a relative path inside the plugin")
    return str(path)


RelativePath = Annotated[str, AfterValidator(_relative_path)]

SidebarIcon = Literal[
    "puzzle-piece",
    "notebook",
    "file-text",
    "book-open",
    "kanban",
    "chart-bar",
    "chat-circle",
    "calendar",
    "lightbulb",
]


class SidebarEntry(BaseModel):
    label: str = Field(min_length=1, max_length=24)
    icon: SidebarIcon = "puzzle-piece"


class PluginManifest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: PluginName
    display_name: str = Field(alias="displayName", min_length=1, max_length=48)
    version: str = Field(min_length=1, max_length=32)
    description: str = Field(default="", max_length=280)
    entry: RelativePath
    sidebar: SidebarEntry
    permissions: list[PluginPermission] = []


class GitHubSource(BaseModel):
    source: Literal["github"]
    repo: str
    ref: str | None = None
    sha: str | None = None


class GitSubdirSource(BaseModel):
    source: Literal["git-subdir"]
    url: str
    path: RelativePath
    ref: str | None = None
    sha: str | None = None


class MarketplaceEntry(BaseModel):
    name: PluginName
    source: RelativePath | GitHubSource | GitSubdirSource = Field(union_mode="left_to_right")
    description: str = ""


class MarketplaceOwner(BaseModel):
    name: str


class MarketplaceManifest(BaseModel):
    name: str
    owner: MarketplaceOwner
    description: str = ""
    plugins: list[MarketplaceEntry]
