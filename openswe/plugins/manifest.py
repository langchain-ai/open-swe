"""What an app says about itself, inside its own bundle.

An app is one self-contained HTML file, built anywhere: in a thread's sandbox,
in CI, on a laptop. Its manifest is a JSON script in that file,
``<script type="application/openswe-plugin+json">``, read when a version is
published. The web UI runs the file in a sandboxed frame; none of it runs on
the backend.
"""

import json
import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

MANIFEST_TYPE = "application/openswe-plugin+json"
MAX_BUNDLE_BYTES = 64_000_000

PluginName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$")]

_MANIFEST_SCRIPT = re.compile(
    r"<script\b[^>]*\btype=[\"']" + re.escape(MANIFEST_TYPE) + r"[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


class PluginPermission(StrEnum):
    """What a plugin may ask the web UI to do for the person using it."""

    STORAGE = "storage"
    """Keep documents: private to that person, or shared per GitHub repository."""
    GITHUB_READ = "github:read"
    """Read files, trees, branches, history and pull requests in its repositories."""
    GITHUB_WRITE = "github:write"
    """Create branches, commit files and open pull requests in its repositories."""
    AGENT = "agent"
    """Chat with an agent in a dock beside the plugin, which can call tools the plugin provides."""
    REALTIME = "realtime"
    """Join live rooms: broadcast updates and presence to others with the same access."""


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
    sidebar: SidebarEntry
    permissions: list[PluginPermission] = []


class BundleError(ValueError):
    """A file that is not an app Open SWE can run."""


def read_bundle(html: str) -> PluginManifest:
    """The manifest inside ``html``, once the file is one Open SWE can store and run."""
    if len(html.encode()) > MAX_BUNDLE_BYTES:
        raise BundleError(f"an app is at most {MAX_BUNDLE_BYTES} bytes")
    if "\x00" in html:
        raise BundleError("the file contains NUL characters; escape them as \\u0000")
    found = _MANIFEST_SCRIPT.search(html)
    if found is None:
        raise BundleError(f'the file has no <script type="{MANIFEST_TYPE}"> manifest')
    try:
        return PluginManifest.model_validate(json.loads(found.group(1)))
    except (ValueError, ValidationError) as exc:
        raise BundleError(f"the manifest is not valid: {exc}") from exc
