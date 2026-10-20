# /// script
# requires-python = ">=3.14"
# dependencies = ["pydantic>=2.12"]
# ///
"""Select the preview tree's SDK commit, or the published CLI, for ``GITHUB_OUTPUT``."""

import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, ValidationError

SDK_REPOSITORY: Final = "langchain-ai/managed-deepagents-sdk"
SDK_GIT_URL: Final = f"https://github.com/{SDK_REPOSITORY}"
SDK_PACKAGE_DIR: Final = "packages/pypi"
RUNTIME_PACKAGE: Final = "managed-deepagents"
_FULL_SHA: Final = re.compile(r"[0-9a-f]{40}")


class SourceError(Exception):
    """The tree names an MDA source the preview must not build."""


class GitSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    git: str
    rev: str
    subdirectory: str


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def selected_source(pyproject: Path) -> str | None:
    """The SDK commit the tree's ``mda/`` project builds with, or ``None``."""
    with pyproject.open("rb") as file:
        manifest = tomllib.load(file)
    sources = manifest.get("tool", {}).get("uv", {}).get("sources", {})
    # uv matches sources by normalized name, so `managed_deepagents` names it too.
    raw = next((v for k, v in sources.items() if _normalized(k) == RUNTIME_PACKAGE), None)
    if raw is None:
        return None
    try:
        source = GitSource.model_validate(raw)
    except ValidationError as exc:
        raise SourceError(
            f"{RUNTIME_PACKAGE} must come from a git commit (git, rev, subdirectory only)"
        ) from exc
    if source.git.removesuffix(".git") != SDK_GIT_URL:
        raise SourceError(f"{RUNTIME_PACKAGE} may only come from {SDK_GIT_URL}")
    if source.subdirectory != SDK_PACKAGE_DIR:
        raise SourceError(f"{RUNTIME_PACKAGE} must come from {SDK_PACKAGE_DIR}")
    if not _FULL_SHA.fullmatch(source.rev):
        raise SourceError(f"{RUNTIME_PACKAGE} must name a full lowercase commit SHA")
    return source.rev


def main() -> int:
    [pyproject] = sys.argv[1:]
    try:
        sha = selected_source(Path(pyproject))
    except SourceError as exc:
        print(f"::error::{exc}")
        return 1
    if sha is None:
        print("mda/ deploys with the published managed-deepagents CLI its lockfile pins.")
    else:
        print(f"mda/ deploys with the compiler and runtime built from {SDK_REPOSITORY}@{sha}.")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"sha={sha or ''}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
