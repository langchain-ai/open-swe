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
from bisect import bisect_left
from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import pairwise
from typing import Self
from uuid import uuid7

from pydantic import BaseModel, Field

from openswe.walkthrough.diff import ChangedLine, FileChange, Sign

LineRange = tuple[int, int]
MAX_TITLE_CHARS = 120
MAX_EXPLANATION_CHARS = 1_200
MAX_OTHER_SUMMARY_CHARS = 1_200
_MAX_ERRORS = 20
_MAX_UNPLANNED_FILES = 200
_LINE_NAME_RE = re.compile(r"[+-]\d+")
_WORD_RE = re.compile(r"\w")


class RangeError(ValueError):
    """The ranges do not name lines that are still available."""


class LineRef(BaseModel, frozen=True):
    path: str
    sign: Sign
    lineno: int
    # Kept so the plan can follow a line to a new head by content.
    text: str = ""

    @classmethod
    def of(cls, line: ChangedLine) -> Self:
        return cls(path=line.path, sign=line.sign, lineno=line.lineno, text=line.text)

    @classmethod
    def follow(cls, refs: Iterable[Self], changes: list[FileChange]) -> dict[Self, Self]:
        """Each of ``refs`` mapped to its line in ``changes``, matched by content in diff order.

        Matching in order keeps repeated lines such as a lone ``}`` with their neighbours.
        """
        old: dict[tuple[str, Sign], list[Self]] = {}
        for ref in sorted(set(refs), key=lambda r: r.lineno):
            old.setdefault((ref.path, ref.sign), []).append(ref)
        new: dict[tuple[str, Sign], list[Self]] = {}
        for change in changes:
            for line in change.lines:
                new.setdefault((line.path, line.sign), []).append(cls.of(line))
        followed: dict[Self, Self] = {}
        for side, before in old.items():
            followed.update(_align(before, new.get(side, [])))
        return followed

    @classmethod
    def spans(cls, refs: Iterable[Self]) -> list[str]:
        """One compact entry per file, such as ``a.py +3-9 -4``, in first-seen order."""
        entries: list[str] = []
        for path, (added, deleted) in cls.ranges(refs).items():
            parts = [
                f"{sign}{start}" + (f"-{end}" if end != start else "")
                for sign, spans in (("+", added), ("-", deleted))
                for start, end in spans
            ]
            entries.append(f"{path} {' '.join(parts)}")
        return entries

    @classmethod
    def ranges(cls, refs: Iterable[Self]) -> dict[str, tuple[list[LineRange], list[LineRange]]]:
        """Each file's added and deleted lines as inclusive ranges, files in first-seen order."""
        by_path: dict[str, list[Self]] = {}
        for ref in refs:
            by_path.setdefault(ref.path, []).append(ref)

        def runs(numbers: list[int]) -> list[LineRange]:
            out: list[LineRange] = []
            for number in sorted(numbers):
                if out and number == out[-1][1] + 1:
                    out[-1] = (out[-1][0], number)
                else:
                    out.append((number, number))
            return out

        return {
            path: (
                runs([r.lineno for r in lines if r.sign == "+"]),
                runs([r.lineno for r in lines if r.sign == "-"]),
            )
            for path, lines in by_path.items()
        }


def _align[T: LineRef](before: list[T], after: list[T]) -> dict[T, T]:
    """Pair lines of equal text in order, anchored on lines that appear once on each side.

    A patience-diff alignment: linear apart from sorting, so a large file cannot stall the
    event loop, and repeated lines such as ``}`` pair in order between their anchors.
    """
    counts = Counter(r.text for r in before)
    later = Counter(r.text for r in after)
    where = {r.text: j for j, r in enumerate(after) if later[r.text] == 1}
    unique = [
        (i, where[r.text]) for i, r in enumerate(before) if counts[r.text] == 1 and r.text in where
    ]
    anchors = _increasing(unique)
    pairs: dict[T, T] = {}
    edges = [(-1, -1), *anchors, (len(before), len(after))]
    for (a0, b0), (a1, b1) in pairwise(edges):
        pool: dict[str, deque[T]] = {}
        for ref in after[b0 + 1 : b1]:
            pool.setdefault(ref.text, deque()).append(ref)
        for ref in before[a0 + 1 : a1]:
            if bucket := pool.get(ref.text):
                pairs[ref] = bucket.popleft()
        if a1 < len(before):
            pairs[before[a1]] = after[b1]
    return pairs


def _increasing(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The longest run of ``pairs`` whose second items increase, in order."""
    tails: list[int] = []
    ends: list[int] = []
    previous: list[int] = []
    for k, (_, j) in enumerate(pairs):
        at = bisect_left(tails, j)
        previous.append(ends[at - 1] if at else -1)
        if at == len(tails):
            tails.append(j)
            ends.append(k)
        else:
            tails[at] = j
            ends[at] = k
    run: list[tuple[int, int]] = []
    k = ends[-1] if ends else -1
    while k >= 0:
        run.append(pairs[k])
        k = previous[k]
    return run[::-1]


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


def line_name(line: ChangedLine | LineRef) -> str:
    return f"{line.sign}{line.lineno}"


def find_line(path: str, name: str, candidates: Iterable[ChangedLine]) -> ChangedLine:
    """The changed line ``name`` (``+12`` or ``-40``) names in ``path``."""
    if not _LINE_NAME_RE.fullmatch(name):
        raise RangeError(f"`{name}` is not a changed line; write it as `+12` or `-40`")
    found = next((c for c in candidates if c.path == path and line_name(c) == name), None)
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
        names: dict[LineRef, str] = {}
        for change in changes:
            name, hunk = "", None
            for line in change.lines:
                ref = LineRef.of(line)
                if line.hunk != hunk or ref in starts:
                    name, hunk = line_name(line), line.hunk
                names[ref] = name
        return cls(names)

    def key(self, line: ChangedLine) -> tuple[str, str]:
        return line.path, self.names.get(LineRef.of(line), line_name(line))

    def groups(self, lines: Iterable[ChangedLine]) -> dict[tuple[str, str], list[ChangedLine]]:
        grouped: dict[tuple[str, str], list[ChangedLine]] = {}
        for line in lines:
            grouped.setdefault(self.key(line), []).append(line)
        return grouped

    def fragments(self, before: Self, lines: list[ChangedLine]) -> list[tuple[str, str]]:
        """Hunks new since ``before`` that hold only braces, punctuation or blank lines."""
        existing = {
            frozenset(LineRef.of(line) for line in hunk) for hunk in before.groups(lines).values()
        }
        return [
            key
            for key, hunk in self.groups(lines).items()
            if frozenset(LineRef.of(line) for line in hunk) not in existing
            and not any(_WORD_RE.search(line.text) for line in hunk)
        ]

    def summary(self, lines: list[ChangedLine]) -> str:
        files = {line.path for line in lines}
        hunks = {self.key(line) for line in lines}
        return (
            f"{len(hunks)} hunk{'s' if len(hunks) != 1 else ''} ({len(lines)} changed lines) "
            f"in {len(files)} file{'s' if len(files) != 1 else ''}"
        )

    def spans(self, lines: Iterable[ChangedLine]) -> list[str]:
        """One entry per file naming its hunks and their size, such as ``a.py +12 (+3/-1), -40 (-5)``."""
        counts: dict[str, dict[str, Counter[Sign]]] = {}
        for line in lines:
            path, name = self.key(line)
            counts.setdefault(path, {}).setdefault(name, Counter())[line.sign] += 1
        return [
            f"{path} "
            + ", ".join(
                f"{name} ({'/'.join(f'{sign}{signs[sign]}' for sign in ('+', '-') if signs[sign])})"
                for name, signs in hunks.items()
            )
            for path, hunks in counts.items()
        ]

    def claim(self, files: list[FileHunks], candidates: list[ChangedLine]) -> list[LineRef]:
        """Every one of the ``candidates`` in the named hunks, in diff order."""
        errors: list[str] = []
        named: set[tuple[str, str]] = set()
        available = {self.key(line) for line in candidates}
        for hunks in files:
            for name in hunks.hunks:
                if (hunks.path, name) in available:
                    named.add((hunks.path, name))
                else:
                    errors.append(f"`{hunks.path}` has no available hunk {name}")
        if not named and not errors:
            errors.append("no hunks are named")
        _raise(errors)
        return [LineRef.of(line) for line in candidates if self.key(line) in named]


def _raise(errors: list[str]) -> None:
    if errors:
        more = len(errors) - _MAX_ERRORS
        raise RangeError(
            "\n".join(errors[:_MAX_ERRORS]) + (f"\n…and {more} more" if more > 0 else "")
        )


def claim(files: list[FileRanges], candidates: list[ChangedLine]) -> list[LineRef]:
    """The ``candidates`` the ranges name, in diff order; every range must name at least one."""
    order = {LineRef.of(line): i for i, line in enumerate(candidates)}
    paths = {line.path for line in candidates}
    errors: list[str] = []
    refs: set[LineRef] = set()
    for ranges in files:
        if ranges.path not in paths:
            errors.append(f"`{ranges.path}` has no changed lines available here")
            continue
        for sign, spans in (("+", ranges.added), ("-", ranges.deleted)):
            for start, end in spans:
                hits = [
                    ref
                    for ref in order
                    if ref.path == ranges.path and ref.sign == sign and start <= ref.lineno <= end
                ]
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
        index = {LineRef.of(line): line for change in changes for line in change.lines}
        for chunk in self.chunks:
            for ref in chunk.lines:
                if line := index.get(ref):
                    owner.setdefault(hunks.key(line), chunk)
        for line in self.unplanned(changes):
            if chunk := owner.get(hunks.key(line)):
                chunk.lines.append(LineRef.of(line))
        order = {ref: i for i, ref in enumerate(index)}
        for chunk in self.chunks:
            chunk.lines.sort(key=order.__getitem__)

    def planned(self) -> set[LineRef]:
        return {ref for chunk in self.chunks for ref in chunk.lines} | set(self.other)

    def unplanned(self, changes: list[FileChange]) -> list[ChangedLine]:
        planned = self.planned()
        return [
            line for change in changes for line in change.lines if LineRef.of(line) not in planned
        ]

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
        self.other += [ref for ref in refs if ref not in set(self.other)]

    def restore_other(self, refs: list[LineRef]) -> None:
        taken = set(refs)
        self.other = [ref for ref in self.other if ref not in taken]

    def split(self, refs: list[LineRef]) -> None:
        self.splits += [ref for ref in refs if ref not in set(self.splits)]

    def status(self, changes: list[FileChange]) -> PlanStatus:
        unplanned = self.unplanned(changes)
        hunks = self.hunks(changes)
        spans = hunks.spans(unplanned)
        index = {LineRef.of(line): line for change in changes for line in change.lines}
        return PlanStatus(
            chunks=[
                PlannedChunk(
                    number=i,
                    title=c.title,
                    hunks=hunks.spans(index[ref] for ref in c.lines if ref in index),
                )
                for i, c in enumerate(self.chunks, start=1)
            ],
            other_lines=len(self.other),
            unplanned=hunks.summary(unplanned) if unplanned else "nothing",
            unplanned_hunks=spans[:_MAX_UNPLANNED_FILES],
            more_files_unplanned=max(0, len(spans) - _MAX_UNPLANNED_FILES),
        )
