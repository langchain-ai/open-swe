"""A pull request's walkthrough plan: ordered chunks a reader goes through one at a time.

Planners place whole hunks, so a chunk can gather related hunks across files and
no hunk is shared between chunks. A planner may first split a git hunk into
smaller hunks at changed lines it names. Every changed line is in one chunk, in Other,
or not yet planned. Chunks and Other are line sets, carried to a new head by
how git moved each line: they keep the lines that survive, a new line joins the
chunk that holds the rest of its hunk, and the remaining new or edited lines
come back unplanned for a planner to place.
"""

from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property
from typing import Self
from uuid import uuid7

from pydantic import BaseModel, Field

from openswe.walkthrough.diff import Block, Carry, DiffIndex, Segment, segments
from openswe.walkthrough.lines import SIGNS, LinePoint, LineRange, LineSet, Ranges, Sign

MAX_TITLE_CHARS = 120
MAX_EXPLANATION_CHARS = 1_200
MAX_OTHER_SUMMARY_CHARS = 1_200
_MAX_ERRORS = 20


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


def find_line(path: str, name: str, candidates: LineSet) -> LinePoint:
    """The changed line ``name`` (``+12`` or ``-40``) names in ``path``, among ``candidates``."""
    point = LinePoint.named(path, name)
    if point is None:
        raise RangeError(f"`{name}` is not a changed line; write it as `+12` or `-40`")
    if point not in candidates:
        raise RangeError(f"`{path}` has no available changed line {name}")
    return point


@dataclass(frozen=True)
class Hunk:
    path: str
    # Its first changed line in diff order, such as ``+12``.
    name: str
    # Its changed lines in diff order.
    segments: list[Segment]

    @cached_property
    def lines(self) -> LineSet:
        # A sign's segments in one hunk are in order and never touch, so they are ranges as they are.
        return LineSet(
            {
                self.path: {
                    sign: Ranges(
                        tuple((start, end) for s, start, end in self.segments if s == sign)
                    )
                    for sign in SIGNS
                }
            }
        )

    @classmethod
    def starting(cls, path: str, segments: list[Segment]) -> Self:
        sign, lineno, _ = segments[0]
        return cls(path, f"{sign}{lineno}", segments)


def _pieces(path: str, blocks: list[Block], cuts: set[tuple[Sign, int]]) -> list[Hunk]:
    """One git hunk, cut into smaller hunks before each of its lines in ``cuts``."""
    if not cuts:
        return [Hunk.starting(path, segments(blocks))]
    pieces: list[list[Segment]] = [[]]
    for sign, start, end in segments(blocks):
        if pieces[-1] and (sign, start) in cuts:
            pieces.append([])
        for cut_sign, lineno in sorted(cuts):
            if cut_sign == sign and start < lineno <= end:
                pieces[-1].append((sign, start, lineno - 1))
                pieces.append([])
                start = lineno
        pieces[-1].append((sign, start, end))
    return [Hunk.starting(path, piece) for piece in pieces]


class Hunks:
    """The diff's hunks as the plan splits them, each named by its first changed line."""

    def __init__(self, index: DiffIndex, by_path: dict[str, list[Hunk]]) -> None:
        self._index = index
        self._by_path = by_path
        self._named = {(h.path, h.name): h for hunks in by_path.values() for h in hunks}
        # Each file's segments per sign, by first line, with the hunk each belongs to.
        self._segments: dict[tuple[str, Sign], tuple[list[int], list[tuple[int, int, int]]]] = {}
        for path, hunks in by_path.items():
            # Hunks are in diff order, so each sign's segments come out sorted.
            for sign in SIGNS:
                found = [
                    (start, end, at)
                    for at, hunk in enumerate(hunks)
                    for s, start, end in hunk.segments
                    if s == sign
                ]
                if found:
                    self._segments[path, sign] = ([start for start, _, _ in found], found)

    @classmethod
    def of(cls, index: DiffIndex, splits: Iterable[LinePoint]) -> Self:
        """Git's hunks, with a new hunk starting at every line in ``splits``."""
        cuts: dict[str, set[tuple[Sign, int]]] = {}
        for point in splits:
            cuts.setdefault(point.path, set()).add((point.sign, point.lineno))
        return cls(
            index,
            {
                file.path: [
                    hunk
                    for blocks in file.hunks
                    for hunk in _pieces(file.path, blocks, cuts.get(file.path, set()))
                ]
                for file in index.files
            },
        )

    def get(self, path: str, name: str) -> Hunk | None:
        return self._named.get((path, name))

    def holding(self, point: LinePoint) -> Hunk | None:
        parts = self.within(LineSet.point(point))
        return parts[0][0] if parts else None

    def within(self, lines: LineSet) -> list[tuple[Hunk, LineSet]]:
        """Each hunk holding some of ``lines``, with those lines, in diff order."""
        parts: list[tuple[Hunk, LineSet]] = []
        for path in self._index.in_order(lines.paths):
            found: dict[int, dict[Sign, list[LineRange]]] = {}
            for sign in SIGNS:
                starts, segments_ = self._segments.get((path, sign), ([], []))
                for start, end in lines.side(path, sign):
                    at = max(bisect_right(starts, start) - 1, 0)
                    while at < len(segments_) and segments_[at][0] <= end:
                        low, high, hunk = segments_[at]
                        if max(low, start) <= min(high, end):
                            found.setdefault(hunk, {}).setdefault(sign, []).append(
                                (max(low, start), min(high, end))
                            )
                        at += 1
            hunks = self._by_path[path] if found else []
            for at in sorted(found):
                # Found in line order, and two pieces of one hunk never touch.
                sides = {sign: Ranges(tuple(spans)) for sign, spans in found[at].items()}
                parts.append((hunks[at], LineSet({path: sides})))
        return parts

    def summary(self, lines: LineSet) -> str:
        parts = self.within(lines)
        files = {hunk.path for hunk, _ in parts}
        count = sum(len(part) for _, part in parts)
        return (
            f"{len(parts)} hunk{'s' if len(parts) != 1 else ''} ({count} changed lines) "
            f"in {len(files)} file{'s' if len(files) != 1 else ''}"
        )

    def spans(self, lines: LineSet) -> list[str]:
        """One entry per file naming its hunks and their size, such as ``a.py +12 (+3/-1), -40 (-5)``.

        Lines no longer in the diff are left out.
        """
        by_path: dict[str, list[str]] = {}
        for hunk, part in self.within(lines):
            size = "/".join(f"{sign}{n}" for sign in SIGNS if (n := part.count(sign)))
            by_path.setdefault(hunk.path, []).append(f"{hunk.name} ({size})")
        return [f"{path} " + ", ".join(entries) for path, entries in by_path.items()]

    def claim(self, files: list[FileHunks], candidates: LineSet) -> LineSet:
        """Every one of the ``candidates`` in the named hunks."""
        errors: list[str] = []
        named: list[LineSet] = []
        for hunks in files:
            for name in hunks.hunks:
                hunk = self.get(hunks.path, name)
                if hunk is not None and (part := hunk.lines & candidates):
                    named.append(part)
                else:
                    errors.append(f"`{hunks.path}` has no available hunk {name}")
        if not named and not errors:
            errors.append("no hunks are named")
        _raise(errors)
        return LineSet.union(named)


def _raise(errors: list[str]) -> None:
    if errors:
        more = len(errors) - _MAX_ERRORS
        raise RangeError(
            "\n".join(errors[:_MAX_ERRORS]) + (f"\n…and {more} more" if more > 0 else "")
        )


def claim(files: list[FileRanges], candidates: LineSet) -> LineSet:
    """The ``candidates`` the ranges name; every range must name at least one."""
    paths = set(candidates.paths)
    errors: list[str] = []
    picked: list[LineSet] = []
    for ranges in files:
        if ranges.path not in paths:
            errors.append(f"`{ranges.path}` has no changed lines available here")
            continue
        sides: tuple[tuple[Sign, list[LineRange]], ...] = (
            ("+", ranges.added),
            ("-", ranges.deleted),
        )
        for sign, spans in sides:
            for start, end in spans:
                hit = LineSet.of(ranges.path, sign, [(start, end)]) & candidates
                if not hit:
                    kind = "added" if sign == "+" else "deleted"
                    errors.append(f"`{ranges.path}` {kind} {start}-{end} holds no available line")
                picked.append(hit)
    lines = LineSet.union(picked)
    if not lines and not errors:
        errors.append("the ranges name no lines")
    _raise(errors)
    return lines


class PlanChunk(BaseModel):
    id: str = Field(default_factory=lambda: uuid7().hex)
    title: str
    explanation: str = ""
    lines: LineSet
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
    other: LineSet = LineSet()
    # Binary, mode-only and empty-file changes: nothing to show, so always Other.
    other_files: list[str] = []
    # Their diff fingerprints, so a reader who approved Other sees a changed one again.
    other_file_fingerprints: list[str] = []
    other_summary: str = ""
    # Changed lines a planner chose to start a new hunk at, splitting git's hunk in two.
    splits: list[LinePoint] = []

    @classmethod
    def start(cls, head_sha: str, index: DiffIndex) -> Self:
        return cls(
            head_sha=head_sha,
            other_files=[file.path for file in index.textless],
            other_file_fingerprints=[file.fingerprint for file in index.textless],
        )

    def carried_to(self, head_sha: str, index: DiffIndex, carry: Carry) -> Self:
        """This plan on a new head, keeping every line git kept."""
        moved = type(self).start(head_sha, index)
        for chunk in self.chunks:
            if lines := carry.lines(chunk.lines):
                moved.chunks.append(chunk.model_copy(update={"lines": lines, "code": ""}))
        moved.other = carry.lines(self.other)
        moved.other_summary = self.other_summary
        moved.splits = [point for s in self.splits if (point := carry.point(s)) is not None]
        moved.gather_hunks(index)
        return moved

    def hunks(self, index: DiffIndex) -> Hunks:
        return Hunks.of(index, self.splits)

    def gather_hunks(self, index: DiffIndex) -> None:
        """Move each unplanned line into the chunk that holds the rest of its hunk."""
        for hunk, part in self.hunks(index).within(self.unplanned(index)):
            owner = next((c for c in self.chunks if c.lines & hunk.lines), None)
            if owner is not None:
                owner.lines = owner.lines | part

    def planned(self) -> LineSet:
        return LineSet.union([*(chunk.lines for chunk in self.chunks), self.other])

    def unplanned(self, index: DiffIndex) -> LineSet:
        """The changed lines in no chunk and not in Other."""
        return index.lines - self.planned()

    def complete(self, index: DiffIndex) -> bool:
        return not self.unplanned(index)

    def number_of(self, chunk_id: str) -> int | None:
        return next((i for i, c in enumerate(self.chunks, start=1) if c.id == chunk_id), None)

    def chunk(self, chunk_id: str) -> PlanChunk | None:
        return next((c for c in self.chunks if c.id == chunk_id), None)

    def add_chunk(self, chunk: PlanChunk, *, after: int | None = None) -> int:
        """Insert ``chunk`` after chunk number ``after`` (0 for first), or last; returns its number."""
        position = len(self.chunks) if after is None else max(0, min(after, len(self.chunks)))
        self.chunks.insert(position, chunk)
        return position + 1

    def add_other(self, lines: LineSet) -> None:
        self.other = self.other | lines

    def restore_other(self, lines: LineSet) -> None:
        self.other = self.other - lines

    def split(self, points: list[LinePoint]) -> None:
        have = set(self.splits)
        self.splits += [point for point in points if point not in have]
