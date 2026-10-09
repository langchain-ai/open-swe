"""Which pull requests may be approved from Slack: tiny and fully visible.

The gates are size and visibility: every file the two voters have to read has
to arrive as a readable text diff, and those files together have to fit in
``MAX_CHANGED_LINES``, so all of it is on the card. Test files are outside both
gates — CI judges them — which is what lets a two-line fix arrive with the
tests that prove it. So are hunks the agent excludes as qualifying under the
target repository's ``.open-swe/APPROVALS.md``; the card summarizes them.
"""

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypedDict

import httpx2
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from openswe.github.pull_request_status import PullRequestClient

logger = logging.getLogger(__name__)

MAX_CHANGED_LINES = 20
# What is actually enforced. A change that lands a few lines over is no harder to
# read than one that lands under, and bouncing it costs the asker more than the
# slack costs the voters; the agent is told 20 so it aims there.
ACCEPTED_CHANGED_LINES = 25
MAX_FILES = 100

# Kinds of change that must be read, so no APPROVALS.md guideline can hide them.
_UNEXCLUDABLE_PATH = re.compile(
    r"(?:^|/)\.github/"
    r"|(?:^|/)(?:migrations|alembic)/"
    r"|(?:^|/)(?:package(?:-lock)?\.json|pnpm-lock\.yaml|yarn\.lock|pyproject\.toml|uv\.lock"
    r"|poetry\.lock|Pipfile(?:\.lock)?|requirements[^/]*\.txt|go\.(?:mod|sum)"
    r"|Cargo\.(?:toml|lock)|Gemfile(?:\.lock)?|composer\.(?:json|lock))$"
    r"|(?:^|/)\.env(?:\.[^/]*)?$"
    r"|(?:^|/)[^/]*(?:auth|credential|secret|password|token)[^/]*(?:/|$)",
    re.IGNORECASE,
)

_TEST_PATH = re.compile(
    r"(?:^|/)(?:tests?|__tests__|testdata|e2e)/"
    r"|(?:^|/)conftest\.py$"
    r"|(?:^|/)test_[^/]+$"
    r"|_test\.[^/.]+$"
    r"|\.(?:test|spec)\.[^/.]+$"
)
# Declarative output derived from the PR's source, so it never decides anything on its own.
# Generated code (bundles, protobuf stubs) can run, so it is drawn like any other code.
_GENERATED_PATH = re.compile(r"(?:^|/)(?:swagger|openapi)\.(?:json|ya?ml)$|\.snap$")
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

    def keeping(self, hunks: list[Hunk]) -> ChangedFile:
        return self.model_copy(
            update={
                "patch": "\n".join(hunk.text for hunk in hunks),
                "additions": sum(hunk.additions for hunk in hunks),
                "deletions": sum(hunk.deletions for hunk in hunks),
            }
        )

    def _stays_within(self, pattern: re.Pattern[str]) -> bool:
        """A file crossing into or out of a category is a production change either way."""
        if pattern.search(self.filename) is None:
            return False
        return self.previous_filename is None or pattern.search(self.previous_filename) is not None

    @property
    def is_test(self) -> bool:
        return self._stays_within(_TEST_PATH)

    @property
    def is_generated(self) -> bool:
        return not self.is_test and not self.must_be_read and self._stays_within(_GENERATED_PATH)

    @property
    def must_be_read(self) -> bool:
        return any(
            _UNEXCLUDABLE_PATH.search(name)
            for name in (self.filename, self.previous_filename or "")
        )

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions

    @classmethod
    def split(
        cls, files: list[ChangedFile]
    ) -> tuple[list[ChangedFile], list[ChangedFile], list[ChangedFile]]:
        """``(files the voters have to read, test files, generated files)``."""
        return (
            [file for file in files if not file.is_test and not file.is_generated],
            [file for file in files if file.is_test],
            [file for file in files if file.is_generated],
        )

    @classmethod
    def total_lines(cls, files: list[ChangedFile]) -> int:
        return sum(file.changed_lines for file in files)

    @classmethod
    async def of_pull(cls, pull: PullRequestClient) -> list[ChangedFile] | None:
        """The PR's first ``MAX_FILES`` changed files, or ``None`` when GitHub could not say."""
        try:
            return _CHANGED_FILES.validate_python(await pull.files())
        except httpx2.HTTPError, ValueError, ValidationError:
            logger.warning(
                "Could not read the pull request's changed files",
                extra={"repo_full_name": pull.repo.full_name, "pr_number": pull.number},
                exc_info=True,
            )
            return None


_CHANGED_FILES = TypeAdapter(list[ChangedFile])


class ExcludedHunk(TypedDict):
    """A hunk left off the card."""

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
        if file.is_test or file.is_generated:
            return []
        if file.must_be_read:
            raise ValueError(
                f"`{self.path}` cannot be excluded: CI workflows, migrations, dependency "
                "manifests, environment files, and auth, credential, secret or token code "
                "must always be read"
            )
        if file.patch is None:
            raise ValueError(f"`{self.path}` has no text diff, so it cannot be excluded")
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
    """The diff as the card splits it: drawn, tests, generated, and still-present exclusions.

    An exclusion whose content changed no longer matches, so its hunk is drawn again.
    """

    __slots__ = ("excluded", "generated", "partially_shown", "shown", "tests", "total_lines")

    def __init__(self, files: list[ChangedFile], exclusions: Sequence[ExcludedHunk] = ()) -> None:
        reviewed, self.tests, self.generated = ChangedFile.split(files)
        self.total_lines = ChangedFile.total_lines(files)
        by_path: dict[str, list[ExcludedHunk]] = {}
        for entry in exclusions:
            by_path.setdefault(entry["path"], []).append(entry)
        self.shown: list[ChangedFile] = []
        self.excluded: list[ExcludedHunk] = []
        partial: set[str] = set()
        for file in reviewed:
            wanted = list(by_path.get(file.filename, ()))
            if not wanted or file.patch is None:
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
        """Drawn, excluded, test and generated lines; anything short of the total fell through."""
        return (
            ChangedFile.total_lines(self.shown)
            + self.excluded_lines
            + ChangedFile.total_lines(self.tests)
            + ChangedFile.total_lines(self.generated)
        )


@dataclass(frozen=True, slots=True)
class EligibleDiff:
    files: list[ChangedFile]
    changed_lines: int
    test_lines: int
    generated_lines: int
    excluded_lines: int
    fingerprint: str


@dataclass(frozen=True, slots=True)
class Ineligible:
    reason: str


def diff_fingerprint(files: list[ChangedFile]) -> str:
    """A hash of the reviewed diff, excluded hunks included, so a commit touching only tests or
    generated files keeps the votes."""
    digest = hashlib.sha256()
    for file in sorted(ChangedFile.split(files)[0], key=lambda f: f.filename):
        digest.update(file.filename.encode())
        digest.update(b"\0")
        digest.update((file.patch or "").encode())
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
    generated_lines = ChangedFile.total_lines(diff.generated)
    if diff.total_lines < 1:
        return Ineligible("the pull request changes no lines")
    if changed > ACCEPTED_CHANGED_LINES:
        outside = (
            "tests, generated files and exclusions"
            if diff.excluded
            else "tests and generated files"
        )
        return Ineligible(
            f"the pull request changes {changed} lines outside {outside}; the limit is "
            f"{MAX_CHANGED_LINES}"
        )
    return EligibleDiff(
        files=list(files),
        changed_lines=changed,
        test_lines=test_lines,
        generated_lines=generated_lines,
        excluded_lines=diff.excluded_lines,
        fingerprint=diff_fingerprint(files),
    )
