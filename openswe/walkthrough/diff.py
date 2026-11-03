"""A pull request's changed lines at one head: each file's git hunks, indexed in the sandbox.

The sandbox reads the diff of merge base to head
(``openswe/sandbox_scripts/walkthrough.py``) and returns every hunk as the
blocks of ``git diff -U0``, grouped as git's default context groups them, so
hunks match what GitHub and the review page show. The backend never reads the
diff's lines: an index is a few numbers per block, whatever the size of the
change. Added lines are numbered at the head and deleted lines at the merge base.
"""

from bisect import bisect_right
from functools import cached_property

from pydantic import BaseModel

from openswe.walkthrough.lines import LinePoint, LineRange, LineSet, Ranges, Sign

# ``[old_start, old_len, new_start, new_len]`` as a ``git diff -U0`` header says; with a
# length of zero, the start is the line just before the change.
Block = tuple[int, int, int, int]
Segment = tuple[Sign, int, int]


def segments(blocks: list[Block]) -> list[Segment]:
    """``blocks``' changed lines in diff order: each block's deletions, then its additions."""
    out: list[Segment] = []
    for old_start, old_len, new_start, new_len in blocks:
        if old_len:
            out.append(("-", old_start, old_start + old_len - 1))
        if new_len:
            out.append(("+", new_start, new_start + new_len - 1))
    return out


class FileDiff(BaseModel):
    path: str
    # Git's hunks in order, each a list of blocks.
    hunks: list[list[Block]] = []
    # Binary, mode-only and empty-file changes have no lines to show.
    textless: bool = False
    deleted: bool = False
    # Hash of the file's diff header, whose blob ids and modes change with its content.
    fingerprint: str = ""

    def sides(self) -> dict[Sign, Ranges]:
        """The file's added and deleted lines."""
        spans: dict[Sign, list[LineRange]] = {"+": [], "-": []}
        for hunk in self.hunks:
            for sign, start, end in segments(hunk):
                spans[sign].append((start, end))
        return {sign: Ranges.of(found) for sign, found in spans.items()}


class DiffIndex(BaseModel):
    files: list[FileDiff] = []

    @cached_property
    def lines(self) -> LineSet:
        """Every changed line."""
        return LineSet({file.path: file.sides() for file in self.files})

    @cached_property
    def order(self) -> dict[str, int]:
        return {file.path: i for i, file in enumerate(self.files)}

    @property
    def textless(self) -> list[FileDiff]:
        return [file for file in self.files if file.textless]

    def in_order(self, paths: list[str]) -> list[str]:
        """``paths`` in diff order."""
        return sorted(paths, key=lambda path: self.order.get(path, len(self.order)))


def _kept(blocks: list[Block]) -> tuple[list[int], list[tuple[int, int, int]]]:
    """The old side's unchanged stretches as ``(first, last, shift)``, and their first lines."""
    kept: list[tuple[int, int, int]] = []
    shift = 0
    next_line = 1
    for old_start, old_len, _, new_len in sorted(blocks):
        first = old_start if old_len else old_start + 1
        if first > next_line:
            kept.append((next_line, first - 1, shift))
        next_line = first + old_len
        shift += new_len - old_len
    kept.append((next_line, 1 << 62, shift))
    return [first for first, _, _ in kept], kept


class LineMaps(BaseModel):
    """How lines moved from an earlier head and merge base to the current ones, per file.

    Each file's blocks are those of ``git diff -U0`` between the two commits;
    a file missing from both is unchanged between them. ``deleted`` is ``None``
    when the earlier merge base is unknown, so deleted lines cannot be carried.
    """

    added: dict[str, list[Block]] = {}
    deleted: dict[str, list[Block]] | None = {}


class Carry:
    """Line sets from an earlier head, carried to ``into``'s head by how git moved each line.

    A line keeps its place when git kept it unchanged and it is still a changed
    line of the new diff; an edited, removed or moved line is dropped.
    """

    def __init__(self, maps: LineMaps, into: DiffIndex) -> None:
        self._into = into
        self._kept = {
            sign: {path: _kept(blocks) for path, blocks in by_path.items()}
            for sign, by_path in (("+", maps.added), ("-", maps.deleted))
            if by_path is not None
        }

    def _map(self, path: str, sign: Sign, ranges: Ranges) -> Ranges:
        if sign not in self._kept:
            return Ranges()
        found = self._kept[sign].get(path)
        if found is None:
            return ranges
        firsts, kept = found
        out: list[LineRange] = []
        for start, end in ranges:
            at = max(bisect_right(firsts, start) - 1, 0)
            while at < len(kept) and kept[at][0] <= end:
                first, last, shift = kept[at]
                low, high = max(start, first), min(end, last)
                if low <= high:
                    out.append((low + shift, high + shift))
                at += 1
        return Ranges.of(out)

    def lines(self, lines: LineSet) -> LineSet:
        return lines.mapped(self._map) & self._into.lines

    def point(self, point: LinePoint) -> LinePoint | None:
        moved = self.lines(LineSet.point(point)).side(point.path, point.sign)
        return point.model_copy(update={"lineno": moved.first()}) if moved else None
