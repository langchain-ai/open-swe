"""Which pull requests may be approved from Slack: tiny and fully visible.

The gates are size and visibility: every file the two voters have to read has
to arrive as a readable text diff, and those files together have to fit in
``MAX_CHANGED_LINES``, so all of it is on the card. Test files are outside both
gates — CI judges them — which is what lets a two-line fix arrive with the
tests that prove it. So are hunks the agent excludes as qualifying under the
target repository's ``.open-swe/APPROVALS.md``; the card summarizes them.
"""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypedDict

import httpx2
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from openswe.github.http import GITHUB_API_BASE, github_client, github_request

MAX_CHANGED_LINES = 20
# What is actually enforced. A change that lands a few lines over is no harder to
# read than one that lands under, and bouncing it costs the asker more than the
# slack costs the voters; the agent is told 20 so it aims there.
ACCEPTED_CHANGED_LINES = 25
MAX_FILES = 100

_TEST_PATH = re.compile(
    r"(?:^|/)(?:tests?|__tests__|testdata|e2e)/"
    r"|(?:^|/)conftest\.py$"
    r"|(?:^|/)test_[^/]+$"
    r"|_test\.[^/.]+$"
    r"|\.(?:test|spec)\.[^/.]+$"
)
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(frozen=True, slots=True)
class Hunk:
    header: str
    body: tuple[str, ...]

    @property
    def new_start(self) -> int:
        match = _HUNK_HEADER.match(self.header)
        return int(match[1]) if match else 0

    @property
    def additions(self) -> int:
        return sum(1 for line in self.body if line.startswith("+"))

    @property
    def deletions(self) -> int:
        return sum(1 for line in self.body if line.startswith("-"))

    @property
    def digest(self) -> str:
        """Leaves out the header, so a hunk stays excluded when an earlier one shifts its lines."""
        return hashlib.sha256("\n".join(self.body).encode()).hexdigest()

    @property
    def text(self) -> str:
        return "\n".join((self.header, *self.body))


class ChangedFile(BaseModel):
    """One entry of GitHub's ``GET /pulls/{n}/files``."""

    model_config = ConfigDict(extra="ignore")

    filename: str
    status: str = ""
    additions: int = 0
    deletions: int = 0
    patch: str | None = None
    previous_filename: str | None = None
    sha: str = ""

    @property
    def hunks(self) -> list[Hunk]:
        hunks: list[Hunk] = []
        header = ""
        body: list[str] = []
        for line in (self.patch or "").splitlines():
            if _HUNK_HEADER.match(line):
                if header:
                    hunks.append(Hunk(header, tuple(body)))
                header, body = line, []
            elif header:
                body.append(line)
        if header:
            hunks.append(Hunk(header, tuple(body)))
        return hunks

    @property
    def whole_digest(self) -> str:
        """Identity of a file GitHub shows no patch for."""
        return f"file:{self.status}:{self.previous_filename or ''}:{self.sha}"

    def keeping(self, hunks: list[Hunk]) -> ChangedFile:
        return self.model_copy(
            update={
                "patch": "\n".join(hunk.text for hunk in hunks),
                "additions": sum(hunk.additions for hunk in hunks),
                "deletions": sum(hunk.deletions for hunk in hunks),
            }
        )

    @property
    def is_test(self) -> bool:
        """A file crossing into or out of the tests tree is a production change either way."""
        if _TEST_PATH.search(self.filename) is None:
            return False
        return (
            self.previous_filename is None or _TEST_PATH.search(self.previous_filename) is not None
        )

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions

    @classmethod
    def split(cls, files: list[ChangedFile]) -> tuple[list[ChangedFile], list[ChangedFile]]:
        """``(files the voters have to read, test files)``."""
        return (
            [file for file in files if not file.is_test],
            [file for file in files if file.is_test],
        )

    @classmethod
    def total_lines(cls, files: list[ChangedFile]) -> int:
        return sum(file.changed_lines for file in files)


_CHANGED_FILES = TypeAdapter(list[ChangedFile])


class ExcludedHunk(TypedDict):
    """A hunk, or a whole patchless file when ``header`` is empty, left off the card."""

    path: str
    digest: str
    header: str
    additions: int
    deletions: int
    guideline: str
    reason: str


class Exclusion(BaseModel):
    """Changes the agent judged to qualify under the target repository's APPROVALS.md."""

    path: str
    hunks: list[int] = []
    guideline: str
    reason: str

    def resolve(self, files: list[ChangedFile]) -> list[ExcludedHunk]:
        """The hunks this names; ``ValueError`` with a message for the agent when it names none."""
        guideline, reason = self.guideline.strip(), self.reason.strip()
        if not guideline or not reason:
            raise ValueError(f"Give a `guideline` and a `reason` for excluding `{self.path}`")
        file = next((file for file in files if file.filename == self.path), None)
        if file is None:
            raise ValueError(f"`{self.path}` is not changed by the pull request")
        if file.is_test:
            return []
        if file.patch is None:
            if self.hunks:
                raise ValueError(
                    f"`{self.path}` has no text diff; omit `hunks` to exclude the whole file"
                )
            return [
                ExcludedHunk(
                    path=self.path,
                    digest=file.whole_digest,
                    header="",
                    additions=file.additions,
                    deletions=file.deletions,
                    guideline=guideline,
                    reason=reason,
                )
            ]
        by_start = {hunk.new_start: hunk for hunk in file.hunks}
        missing = [start for start in self.hunks if start not in by_start]
        if missing:
            raise ValueError(
                f"`{self.path}` has no hunk starting at new line {', '.join(map(str, missing))}; "
                f"its hunks start at {', '.join(map(str, sorted(by_start)))}"
            )
        chosen = [by_start[start] for start in self.hunks] if self.hunks else file.hunks
        return [
            ExcludedHunk(
                path=self.path,
                digest=hunk.digest,
                header=hunk.header,
                additions=hunk.additions,
                deletions=hunk.deletions,
                guideline=guideline,
                reason=reason,
            )
            for hunk in chosen
        ]


class ExpeditedDiff:
    """The diff as the card splits it: drawn, tests, and still-present exclusions.

    An exclusion whose content changed no longer matches, so its hunk is drawn again.
    """

    __slots__ = ("excluded", "partially_shown", "shown", "tests", "total_lines")

    def __init__(self, files: list[ChangedFile], exclusions: Sequence[ExcludedHunk] = ()) -> None:
        reviewed, self.tests = ChangedFile.split(files)
        self.total_lines = ChangedFile.total_lines(files)
        by_path: dict[str, list[ExcludedHunk]] = {}
        for entry in exclusions:
            by_path.setdefault(entry["path"], []).append(entry)
        self.shown: list[ChangedFile] = []
        self.excluded: list[ExcludedHunk] = []
        partial: set[str] = set()
        for file in reviewed:
            wanted = list(by_path.get(file.filename, ()))
            if not wanted:
                self.shown.append(file)
                continue
            if file.patch is None:
                if any(entry["digest"] == file.whole_digest for entry in wanted):
                    self.excluded.append(wanted[0])
                else:
                    self.shown.append(file)
                continue
            hunks = file.hunks
            hits = self._match(hunks, wanted)
            kept = [hunk for index, hunk in enumerate(hunks) if index not in hits]
            self.excluded.extend(hits.values())
            if len(kept) == len(hunks):
                self.shown.append(file)
            elif kept:
                self.shown.append(file.keeping(kept))
                partial.add(file.filename)
        self.partially_shown = frozenset(partial)

    @staticmethod
    def _match(hunks: list[Hunk], wanted: list[ExcludedHunk]) -> dict[int, ExcludedHunk]:
        """Each exclusion hides one hunk: its own position first, else a shifted identical body."""
        hits: dict[int, ExcludedHunk] = {}
        for exact in (True, False):
            for index, hunk in enumerate(hunks):
                if index in hits:
                    continue
                for position, entry in enumerate(wanted):
                    if entry["digest"] == hunk.digest and (
                        not exact or entry["header"] == hunk.header
                    ):
                        hits[index] = wanted.pop(position)
                        break
        return hits

    @property
    def excluded_lines(self) -> int:
        return sum(entry["additions"] + entry["deletions"] for entry in self.excluded)

    @property
    def accounted_lines(self) -> int:
        """Drawn, excluded and test lines; anything short of the PR's total fell through."""
        return (
            ChangedFile.total_lines(self.shown)
            + self.excluded_lines
            + ChangedFile.total_lines(self.tests)
        )


@dataclass(frozen=True, slots=True)
class EligibleDiff:
    files: list[ChangedFile]
    changed_lines: int
    test_lines: int
    excluded_lines: int
    fingerprint: str


@dataclass(frozen=True, slots=True)
class Ineligible:
    reason: str


def diff_fingerprint(files: list[ChangedFile]) -> str:
    """A hash of the non-test diff, excluded hunks included, so a commit touching only tests keeps the votes."""
    digest = hashlib.sha256()
    for file in sorted(ChangedFile.split(files)[0], key=lambda f: f.filename):
        digest.update(file.filename.encode())
        digest.update(b"\0")
        digest.update((file.whole_digest if file.patch is None else file.patch).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def fingerprint_matches(files: list[ChangedFile], fingerprint: str) -> bool:
    return diff_fingerprint(files) == fingerprint


def assess_eligibility(
    files: list[ChangedFile], exclusions: Sequence[ExcludedHunk] = ()
) -> EligibleDiff | Ineligible:
    if not files:
        return Ineligible("the pull request changes no files")
    if len(files) >= MAX_FILES:
        return Ineligible("the pull request changes too many files")
    diff = ExpeditedDiff(files, exclusions)
    for file in diff.shown:
        if file.patch is None:
            return Ineligible(
                f"`{file.filename}` has no text diff (binary, oversized, or a rename)"
            )
    if diff.accounted_lines != diff.total_lines:
        return Ineligible(
            f"the card would account for {diff.accounted_lines} of the pull request's "
            f"{diff.total_lines} changed lines, so some change would be neither drawn nor "
            "listed as excluded"
        )
    changed = ChangedFile.total_lines(diff.shown)
    test_lines = ChangedFile.total_lines(diff.tests)
    if changed + test_lines + diff.excluded_lines < 1:
        return Ineligible("the pull request changes no lines")
    if changed > ACCEPTED_CHANGED_LINES:
        outside = "tests and exclusions" if diff.excluded else "tests"
        return Ineligible(
            f"the pull request changes {changed} lines outside {outside}; the limit is "
            f"{MAX_CHANGED_LINES}"
        )
    return EligibleDiff(
        files=list(files),
        changed_lines=changed,
        test_lines=test_lines,
        excluded_lines=diff.excluded_lines,
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
