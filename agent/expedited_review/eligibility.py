"""Which pull requests may be approved from Slack: tiny and fully visible.

Nothing is refused by path. The gates are size and visibility: every changed
file has to arrive as a readable text diff, and the whole change has to fit in
``MAX_CHANGED_LINES``, so the two people voting can read all of it on the card.
"""

import hashlib
from dataclasses import dataclass

import httpx2
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from agent.github.http import GITHUB_API_BASE, github_client, github_request

MAX_CHANGED_LINES = 10
MAX_FILES = 100


class ChangedFile(BaseModel):
    """One entry of GitHub's ``GET /pulls/{n}/files``."""

    model_config = ConfigDict(extra="ignore")

    filename: str
    status: str = ""
    additions: int = 0
    deletions: int = 0
    patch: str | None = None
    previous_filename: str | None = None


_CHANGED_FILES = TypeAdapter(list[ChangedFile])


@dataclass(frozen=True, slots=True)
class EligibleDiff:
    files: list[ChangedFile]
    changed_lines: int
    fingerprint: str


@dataclass(frozen=True, slots=True)
class Ineligible:
    reason: str


def diff_fingerprint(files: list[ChangedFile]) -> str:
    digest = hashlib.sha256()
    for file in sorted(files, key=lambda f: f.filename):
        digest.update(file.filename.encode())
        digest.update(b"\0")
        digest.update((file.patch or "").encode())
        digest.update(b"\0")
    return digest.hexdigest()


def assess_eligibility(files: list[ChangedFile]) -> EligibleDiff | Ineligible:
    if not files:
        return Ineligible("the pull request changes no files")
    if len(files) >= MAX_FILES:
        return Ineligible("the pull request changes too many files")
    for file in files:
        if file.patch is None:
            return Ineligible(
                f"`{file.filename}` has no text diff (binary, oversized, or a rename)"
            )
    changed = sum(file.additions + file.deletions for file in files)
    if changed < 1:
        return Ineligible("the pull request changes no lines")
    if changed > MAX_CHANGED_LINES:
        return Ineligible(
            f"the pull request changes {changed} lines; the limit is {MAX_CHANGED_LINES}"
        )
    return EligibleDiff(
        files=list(files), changed_lines=changed, fingerprint=diff_fingerprint(files)
    )


async def fetch_changed_files(
    *, owner: str, repo: str, pr_number: int, token: str
) -> list[ChangedFile] | None:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/files"
    try:
        async with github_client(token=token) as client:
            response = await github_request(client, "GET", url, params={"per_page": str(MAX_FILES)})
            response.raise_for_status()
            payload: object = response.json()
        return _CHANGED_FILES.validate_python(payload)
    except httpx2.HTTPError, ValueError, ValidationError:
        return None
