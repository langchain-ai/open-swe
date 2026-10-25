"""A pull request's changed lines, parsed from its diff of merge base to head.

The diff keeps git's default context, so its hunks are the ones GitHub and the
review page show, and a walkthrough step owns whole hunks. Added lines carry
their head line number and deleted lines their merge-base line number. A line's
``key`` is its path, sign and text, never its position, so a line a person
approved is recognized again after a rebase or force-push.
"""

import hashlib
import re
from bisect import bisect_left
from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Literal, Self

from pydantic import BaseModel, TypeAdapter

Sign = Literal["+", "-"]
LineRange = tuple[int, int]

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def line_key(path: str, sign: str, text: str) -> str:
    return hashlib.sha256(f"{path}\0{sign}\0{text}".encode()).hexdigest()


class LineRef(BaseModel, frozen=True):
    path: str
    sign: Sign
    lineno: int
    # Kept so the plan can follow a line to a new head by content.
    text: str = ""

    @staticmethod
    def follow(refs: Iterable[LineRef], changes: list[FileChange]) -> dict[LineRef, LineRef]:
        """Each of ``refs`` mapped to its line in ``changes``, matched by content in diff order.

        Matching in order keeps repeated lines such as a lone ``}`` with their neighbours.
        """
        old: dict[tuple[str, Sign], list[LineRef]] = {}
        for ref in sorted(set(refs), key=lambda r: r.lineno):
            old.setdefault((ref.path, ref.sign), []).append(ref)
        new: dict[tuple[str, Sign], list[LineRef]] = {}
        for change in changes:
            for line in change.lines:
                new.setdefault((line.path, line.sign), []).append(line.ref)
        followed: dict[LineRef, LineRef] = {}
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


@dataclass(frozen=True)
class ChangedLine:
    ref: LineRef
    # The head line the hunk's ``@@ … +N`` header starts at, which names the hunk in its file.
    hunk: int
    # The head line just above where this line sits, for context around deletions.
    anchor: int

    @property
    def path(self) -> str:
        return self.ref.path

    @property
    def sign(self) -> Sign:
        return self.ref.sign

    @property
    def lineno(self) -> int:
        return self.ref.lineno

    @property
    def text(self) -> str:
        return self.ref.text

    @property
    def key(self) -> str:
        return line_key(self.path, self.sign, self.text)


@dataclass
class FileChange:
    path: str
    lines: list[ChangedLine] = field(default_factory=list)
    # Binary, mode-only and empty-file changes have no lines to show.
    textless: bool = False
    deleted: bool = False
    # Hash of the file's diff header, whose blob ids and modes change with its content.
    fingerprint: str = ""

    def stat(self, lines: list[ChangedLine] | None = None) -> str:
        chosen = self.lines if lines is None else lines
        added = sum(1 for line in chosen if line.sign == "+")
        removed = sum(1 for line in chosen if line.sign == "-")
        if self.textless:
            return f"{self.path} (no text changes)"
        return f"{self.path} +{added} -{removed}"


_LINE_REFS = TypeAdapter(list[LineRef])


def parse(diff: str) -> list[FileChange]:
    files: list[FileChange] = []
    header: list[str] = []
    # The file's changed lines as (sign, lineno, text, hunk, anchor), until it ends.
    pending: list[tuple[Sign, int, str, int, int]] = []
    hunk = -1
    old = new = 0

    def finish() -> None:
        if not header:
            return
        change = files[-1]
        # Validating the file's refs in one call is several times faster than one at a time.
        refs = _LINE_REFS.validate_python(
            [
                {"path": change.path, "sign": sign, "lineno": lineno, "text": text}
                for sign, lineno, text, _, _ in pending
            ]
        )
        change.lines = [
            ChangedLine(ref, hunk, anchor)
            for ref, (_, _, _, hunk, anchor) in zip(refs, pending, strict=True)
        ]
        pending.clear()
        change.textless = not change.lines
        change.deleted = any(line.startswith("deleted file mode") for line in header)
        change.fingerprint = hashlib.sha256("\n".join(header).encode()).hexdigest()

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            finish()
            header = [line]
            hunk = -1
            files.append(FileChange(path=line.rpartition(" b/")[2]))
            continue
        if not files:
            continue
        if match := _HUNK_RE.match(line):
            old, hunk = int(match[1]), int(match[3])
            # A hunk that only deletes numbers its head side from the line before it.
            new = hunk if match[4] != "0" else hunk + 1
            continue
        if hunk < 0:
            header.append(line)
            if line.startswith("+++ b/"):
                files[-1].path = line[6:]
            continue
        if line.startswith("-"):
            pending.append(("-", old, line[1:], hunk, new - 1))
            old += 1
        elif line.startswith("+"):
            pending.append(("+", new, line[1:], hunk, new - 1))
            new += 1
        elif not line.startswith("\\"):
            old += 1
            new += 1
    finish()
    return files
