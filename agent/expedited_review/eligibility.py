"""Which pull requests may be approved from Slack: tiny and fully visible.

The gates are size and visibility: every file the two voters have to read has
to arrive as a readable text diff, and those files together have to fit in
``MAX_CHANGED_LINES``, so all of it is on the card. Test files are outside both
gates — CI judges them — which is what lets a two-line fix arrive with the
tests that prove it.
"""

import hashlib
import re
from dataclasses import dataclass

import httpx2
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from agent.github.http import GITHUB_API_BASE, github_client, github_request

MAX_CHANGED_LINES = 20
MAX_FILES = 100

_TEST_PATH = re.compile(
    r"(?:^|/)(?:tests?|__tests__|testdata|e2e)/"
    r"|(?:^|/)conftest\.py$"
    r"|(?:^|/)test_[^/]+$"
    r"|_test\.[^/.]+$"
    r"|\.(?:test|spec)\.[^/.]+$"
)


class ChangedFile(BaseModel):
    """One entry of GitHub's ``GET /pulls/{n}/files``."""

    model_config = ConfigDict(extra="ignore")

    filename: str
    status: str = ""
    additions: int = 0
    deletions: int = 0
    patch: str | None = None
    previous_filename: str | None = None

    @property
    def is_test(self) -> bool:
        return _TEST_PATH.search(self.filename) is not None

    @classmethod
    def split(cls, files: list[ChangedFile]) -> tuple[list[ChangedFile], list[ChangedFile]]:
        """``(files the voters have to read, test files)``."""
        return (
            [file for file in files if not file.is_test],
            [file for file in files if file.is_test],
        )


_CHANGED_FILES = TypeAdapter(list[ChangedFile])


@dataclass(frozen=True, slots=True)
class EligibleDiff:
    files: list[ChangedFile]
    changed_lines: int
    test_lines: int
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
    reviewed, tests = ChangedFile.split(files)
    for file in reviewed:
        if file.patch is None:
            return Ineligible(
                f"`{file.filename}` has no text diff (binary, oversized, or a rename)"
            )
    changed = sum(file.additions + file.deletions for file in reviewed)
    test_lines = sum(file.additions + file.deletions for file in tests)
    if changed + test_lines < 1:
        return Ineligible("the pull request changes no lines")
    if changed > MAX_CHANGED_LINES:
        return Ineligible(
            f"the pull request changes {changed} lines outside tests; the limit is "
            f"{MAX_CHANGED_LINES}"
        )
    return EligibleDiff(
        files=list(files),
        changed_lines=changed,
        test_lines=test_lines,
        fingerprint=diff_fingerprint(files),
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
