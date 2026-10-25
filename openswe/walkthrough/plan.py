"""A pull request's walkthrough plan: ordered chunks a reader goes through one at a time.

Planners place whole hunks, so a chunk can gather related hunks across files and
no hunk is shared between chunks. A planner may first split a git hunk into
smaller hunks at changed lines it names. Every changed line is in one chunk, in Other,
or not yet planned. Lines and splits are followed by content, in order within their
file, so the plan carries over to a new head: chunks and Other keep the lines that
survive, a new line joins the chunk that holds the rest of its hunk, and the
remaining new or edited lines come back unplanned for a planner to place.
"""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Self
from uuid import uuid7

from pydantic import BaseModel, Field

from openswe.walkthrough.diff import FileChange, LineRange, LineRef, Sign

MAX_TITLE_CHARS = 120
MAX_EXPLANATION_CHARS = 1_200
MAX_OTHER_SUMMARY_CHARS = 1_200
_MAX_ERRORS = 20
_LINE_NAME_RE = re.compile(r"[+-]\d+")
_WORD_RE = re.compile(r"\w")


class RangeError(ValueError):
    """The ranges do not name lines that are still available."""


class FileRanges(BaseModel):
    """Lines of one file: ``added`` in head numbering, ``deleted`` in merge-base numbering."""

    path: str
    added: list[LineRange] = []
    deleted: list[LineRange] = []


class FileSplits(BaseModel):
    """Changed lines of one file to start a new hunk at, such as ``+12`` or ``-40``."""

    path: str
    at: list[str]


class FileHunks(BaseModel):
    """Hunks of one file, each named by its first changed line, such as ``+12`` or ``-40``."""

    path: str
    hunks: list[str]


def line_name(line: LineRef) -> str:
    return f"{line.sign}{line.lineno}"


def by_name(refs: Iterable[LineRef]) -> dict[tuple[str, str], LineRef]:
    """``refs`` keyed by path and name, for ``find_line``."""
    return {(ref.path, line_name(ref)): ref for ref in refs}


def find_line(path: str, name: str, candidates: dict[tuple[str, str], LineRef]) -> LineRef:
    """The changed line ``name`` (``+12`` or ``-40``) names in ``path``."""
    if not _LINE_NAME_RE.fullmatch(name):
        raise RangeError(f"`{name}` is not a changed line; write it as `+12` or `-40`")
    found = candidates.get((path, name))
    if found is None:
        raise RangeError(f"`{path}` has no available changed line {name}")
    return found


@dataclass(frozen=True)
class Hunks:
    """The diff's hunks as the plan splits them, each named by its first changed line."""

    names: dict[LineRef, str]

    @classmethod
    def of(cls, changes: list[FileChange], splits: Iterable[LineRef]) -> Self:
        """Git's hunks, with a new hunk starting at every line in ``splits``."""
        starts = set(splits)
        split_paths = {ref.path for ref in starts}
        names: dict[LineRef, str] = {}
        for change in changes:
            split = change.path in split_paths
            name, hunk = "", None
            for line in change.lines:
                ref = line.ref
                if line.hunk != hunk or (split and ref in starts):
                    name, hunk = line_name(ref), line.hunk
                names[ref] = name
        return cls(names)

    def key(self, ref: LineRef) -> tuple[str, str]:
        return ref.path, self.names.get(ref) or line_name(ref)

    def groups(self, refs: Iterable[LineRef]) -> dict[tuple[str, str], list[LineRef]]:
        grouped: dict[tuple[str, str], list[LineRef]] = {}
        for ref in refs:
            grouped.setdefault(self.key(ref), []).append(ref)
        return grouped

    def fragments(self, before: Self, refs: list[LineRef]) -> list[tuple[str, str]]:
        """Hunks new since ``before`` that hold only braces, punctuation or blank lines."""
        existing = {frozenset(hunk) for hunk in before.groups(refs).values()}
        return [
            key
            for key, hunk in self.groups(refs).items()
            if frozenset(hunk) not in existing
            and not any(_WORD_RE.search(ref.text) for ref in hunk)
        ]

    def summary(self, refs: list[LineRef]) -> str:
        files = {ref.path for ref in refs}
        hunks = {self.key(ref) for ref in refs}
        return (
            f"{len(hunks)} hunk{'s' if len(hunks) != 1 else ''} ({len(refs)} changed lines) "
            f"in {len(files)} file{'s' if len(files) != 1 else ''}"
        )

    def spans(self, refs: Iterable[LineRef]) -> list[str]:
        """One entry per file naming its hunks and their size, such as ``a.py +12 (+3/-1), -40 (-5)``.

        Lines no longer in the diff are left out.
        """
        counts: Counter[tuple[str, str, Sign]] = Counter()
        for ref in refs:
            if name := self.names.get(ref):
                counts[ref.path, name, ref.sign] += 1
        files: dict[str, dict[str, None]] = {}
        for path, name, _ in counts:
            files.setdefault(path, {})[name] = None
        return [
            f"{path} "
            + ", ".join(
                f"{name} ("
                + "/".join(
                    f"{sign}{counts[path, name, sign]}"
                    for sign in ("+", "-")
                    if counts[path, name, sign]
                )
                + ")"
                for name in names
            )
            for path, names in files.items()
        ]

    def claim(self, files: list[FileHunks], candidates: list[LineRef]) -> list[LineRef]:
        """Every one of the ``candidates`` in the named hunks, in diff order."""
        errors: list[str] = []
        named: set[tuple[str, str]] = set()
        available = {self.key(ref) for ref in candidates}
        for hunks in files:
            for name in hunks.hunks:
                if (hunks.path, name) in available:
                    named.add((hunks.path, name))
                else:
                    errors.append(f"`{hunks.path}` has no available hunk {name}")
        if not named and not errors:
            errors.append("no hunks are named")
        _raise(errors)
        return [ref for ref in candidates if self.key(ref) in named]


def _new(refs: list[LineRef], existing: list[LineRef]) -> list[LineRef]:
    have = set(existing)
    return [ref for ref in refs if ref not in have]


def _raise(errors: list[str]) -> None:
    if errors:
        more = len(errors) - _MAX_ERRORS
        raise RangeError(
            "\n".join(errors[:_MAX_ERRORS]) + (f"\n…and {more} more" if more > 0 else "")
        )


def claim(files: list[FileRanges], candidates: list[LineRef]) -> list[LineRef]:
    """The ``candidates`` the ranges name, in diff order; every range must name at least one."""
    order = {ref: i for i, ref in enumerate(candidates)}
    sides: dict[tuple[str, str], list[LineRef]] = {}
    for ref in order:
        sides.setdefault((ref.path, ref.sign), []).append(ref)
    paths = {ref.path for ref in candidates}
    errors: list[str] = []
    refs: set[LineRef] = set()
    for ranges in files:
        if ranges.path not in paths:
            errors.append(f"`{ranges.path}` has no changed lines available here")
            continue
        for sign, spans in (("+", ranges.added), ("-", ranges.deleted)):
            side = sides.get((ranges.path, sign), [])
            for start, end in spans:
                hits = [ref for ref in side if start <= ref.lineno <= end]
                if not hits:
                    kind = "added" if sign == "+" else "deleted"
                    errors.append(f"`{ranges.path}` {kind} {start}-{end} holds no available line")
                refs.update(hits)
    if not refs and not errors:
        errors.append("the ranges name no lines")
    _raise(errors)
    return sorted(refs, key=order.__getitem__)


class PlanChunk(BaseModel):
    id: str = Field(default_factory=lambda: uuid7().hex)
    title: str
    explanation: str = ""
    lines: list[LineRef]
    # The chunk's code as a reader sees it, rendered at the plan's head.
    code: str = ""


class PlannedChunk(BaseModel):
    number: int
    title: str
    hunks: list[str]


class PlanStatus(BaseModel):
    """Where the plan stands, as planners are told after every planning tool."""

    chunks: list[PlannedChunk]
    other_lines: int
    unplanned: str
    unplanned_hunks: list[str]
    more_files_unplanned: int = 0


class Plan(BaseModel):
    head_sha: str
    chunks: list[PlanChunk] = []
    other: list[LineRef] = []
    # Binary, mode-only and empty-file changes: nothing to show, so always Other.
    other_files: list[str] = []
    # Their diff fingerprints, so a reader who approved Other sees a changed one again.
    other_file_fingerprints: list[str] = []
    other_summary: str = ""
    # Changed lines a planner chose to start a new hunk at, splitting git's hunk in two.
    splits: list[LineRef] = []

    @classmethod
    def start(cls, head_sha: str, changes: list[FileChange]) -> Self:
        textless = [c for c in changes if c.textless]
        return cls(
            head_sha=head_sha,
            other_files=[c.path for c in textless],
            other_file_fingerprints=[c.fingerprint for c in textless],
        )

    def carried_to(self, head_sha: str, changes: list[FileChange]) -> Self:
        """This plan on a new head, keeping every line that survives by content."""

        followed = LineRef.follow([*self.planned(), *self.splits], changes)

        def take(refs: list[LineRef]) -> list[LineRef]:
            return [followed[ref] for ref in refs if ref in followed]

        moved = type(self).start(head_sha, changes)
        for chunk in self.chunks:
            if lines := take(chunk.lines):
                moved.chunks.append(chunk.model_copy(update={"lines": lines, "code": ""}))
        moved.other = take(self.other)
        moved.other_summary = self.other_summary
        moved.splits = take(self.splits)
        moved.gather_hunks(changes)
        return moved

    def hunks(self, changes: list[FileChange]) -> Hunks:
        return Hunks.of(changes, self.splits)

    def gather_hunks(self, changes: list[FileChange]) -> None:
        """Move each unplanned line into the chunk that holds the rest of its hunk."""
        hunks = self.hunks(changes)
        owner: dict[tuple[str, str], PlanChunk] = {}
        for chunk in self.chunks:
            for ref in chunk.lines:
                if ref in hunks.names:
                    owner.setdefault(hunks.key(ref), chunk)
        for ref in self.unplanned(changes):
            if chunk := owner.get(hunks.key(ref)):
                chunk.lines.append(ref)
        order = {ref: i for i, ref in enumerate(hunks.names)}
        for chunk in self.chunks:
            chunk.lines.sort(key=order.__getitem__)

    def planned(self) -> set[LineRef]:
        return {ref for chunk in self.chunks for ref in chunk.lines} | set(self.other)

    def unplanned(self, changes: list[FileChange]) -> list[LineRef]:
        """The changed lines in no chunk and not in Other, in diff order."""
        planned = self.planned()
        return [line.ref for change in changes for line in change.lines if line.ref not in planned]

    def complete(self, changes: list[FileChange]) -> bool:
        """Whether every changed line is planned, stopping at the first that is not."""
        planned = self.planned()
        return all(line.ref in planned for change in changes for line in change.lines)

    def number_of(self, chunk_id: str) -> int | None:
        return next((i for i, c in enumerate(self.chunks, start=1) if c.id == chunk_id), None)

    def chunk(self, chunk_id: str) -> PlanChunk | None:
        return next((c for c in self.chunks if c.id == chunk_id), None)

    def add_chunk(self, chunk: PlanChunk, *, after: int | None = None) -> int:
        """Insert ``chunk`` after chunk number ``after`` (0 for first), or last; returns its number."""
        position = len(self.chunks) if after is None else max(0, min(after, len(self.chunks)))
        self.chunks.insert(position, chunk)
        return position + 1

    def add_other(self, refs: list[LineRef]) -> None:
        self.other += _new(refs, self.other)

    def restore_other(self, refs: list[LineRef]) -> None:
        taken = set(refs)
        self.other = [ref for ref in self.other if ref not in taken]

    def split(self, refs: list[LineRef]) -> None:
        self.splits += _new(refs, self.splits)
