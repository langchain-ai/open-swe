"""Sets of a pull request's changed lines, held as ranges so their size follows the diff's shape.

Walkthrough plans, a reader's walk and what a person has seen are all sets of
changed lines. Added lines are numbered at the head and deleted lines at the
merge base, so a set is a pair of range lists per file. A 300,000-line generated
file is one range, which keeps every operation proportional to the number of
hunks rather than the number of lines.
"""

import re
from bisect import bisect_right
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Literal, Self

from pydantic import BaseModel, GetCoreSchemaHandler, JsonValue
from pydantic_core import core_schema

Sign = Literal["+", "-"]
SIGNS: tuple[Sign, Sign] = ("+", "-")
LineRange = tuple[int, int]

_LINE_NAME_RE = re.compile(r"([+-])(\d+)")


class LinePoint(BaseModel, frozen=True):
    """One changed line: an added line by its head number, or a deleted one by its merge-base number."""

    path: str
    sign: Sign
    lineno: int

    @property
    def name(self) -> str:
        return f"{self.sign}{self.lineno}"

    @classmethod
    def named(cls, path: str, name: str) -> Self | None:
        """The line ``name`` (``+12`` or ``-40``) names in ``path``, or ``None`` if it names none."""
        match = _LINE_NAME_RE.fullmatch(name)
        if match is None:
            return None
        sign: Sign = "+" if match[1] == "+" else "-"
        return cls(path=path, sign=sign, lineno=int(match[2]))


class Ranges:
    """Sorted, disjoint, non-adjacent inclusive line ranges."""

    __slots__ = ("_count", "_starts", "spans")

    def __init__(self, spans: tuple[LineRange, ...] = ()) -> None:
        self.spans = spans
        self._starts: list[int] | None = None
        self._count = -1

    @classmethod
    def of(cls, spans: Iterable[LineRange]) -> Self:
        merged: list[LineRange] = []
        for start, end in sorted(span for span in spans if span[0] <= span[1]):
            if merged and start <= merged[-1][1] + 1:
                if end > merged[-1][1]:
                    merged[-1] = (merged[-1][0], end)
            else:
                merged.append((start, end))
        return cls(tuple(merged))

    @property
    def starts(self) -> list[int]:
        if self._starts is None:
            self._starts = [start for start, _ in self.spans]
        return self._starts

    def __len__(self) -> int:
        """How many lines the ranges hold."""
        if self._count < 0:
            self._count = sum(end - start + 1 for start, end in self.spans)
        return self._count

    def __bool__(self) -> bool:
        return bool(self.spans)

    def __iter__(self) -> Iterator[LineRange]:
        return iter(self.spans)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Ranges) and self.spans == other.spans

    def __hash__(self) -> int:
        return hash(self.spans)

    def __repr__(self) -> str:
        return f"Ranges({list(self.spans)})"

    def __contains__(self, lineno: object) -> bool:
        if not isinstance(lineno, int):
            return False
        at = bisect_right(self.starts, lineno) - 1
        return at >= 0 and lineno <= self.spans[at][1]

    def _overlapping(self, start: int, end: int) -> range:
        """Indexes of the spans that overlap ``start``-``end``."""
        first = bisect_right(self.starts, start) - 1
        if first < 0 or self.spans[first][1] < start:
            first += 1
        return range(first, bisect_right(self.starts, end))

    def __or__(self, other: Ranges) -> Ranges:
        if not other:
            return self
        if not self:
            return other
        return Ranges.of([*self.spans, *other.spans])

    def __and__(self, other: Ranges) -> Ranges:
        small, large = (self, other) if len(self.spans) <= len(other.spans) else (other, self)
        out: list[LineRange] = []
        for start, end in small.spans:
            for at in large._overlapping(start, end):
                low, high = large.spans[at]
                out.append((max(start, low), min(end, high)))
        return Ranges(tuple(out))

    def __sub__(self, other: Ranges) -> Ranges:
        if not other or not self:
            return self
        out: list[LineRange] = []
        for start, end in self.spans:
            cursor = start
            for at in other._overlapping(start, end):
                low, high = other.spans[at]
                if low > cursor:
                    out.append((cursor, low - 1))
                cursor = max(cursor, high + 1)
            if cursor <= end:
                out.append((cursor, end))
        return Ranges(tuple(out))

    def first(self) -> int:
        return self.spans[0][0]


_EMPTY = Ranges()
_Sides = dict[Sign, Ranges]


class LineSet:
    """Changed lines across files, as each file's added and deleted ranges, files in first-seen order."""

    __slots__ = ("by_path",)

    def __init__(self, files: Mapping[str, Mapping[Sign, Ranges]] | None = None) -> None:
        self.by_path: dict[str, _Sides] = {
            path: {sign: ranges for sign, ranges in sides.items() if ranges}
            for path, sides in (files or {}).items()
            if any(sides.values())
        }

    @classmethod
    def of(cls, path: str, sign: Sign, spans: Iterable[LineRange]) -> Self:
        return cls({path: {sign: Ranges.of(spans)}})

    @classmethod
    def point(cls, point: LinePoint) -> Self:
        return cls({point.path: {point.sign: Ranges(((point.lineno, point.lineno),))}})

    @classmethod
    def union(cls, sets: Iterable[LineSet]) -> Self:
        spans: dict[str, dict[Sign, list[LineRange]]] = {}
        for lines in sets:
            for path, sides in lines.by_path.items():
                for sign, ranges in sides.items():
                    spans.setdefault(path, {}).setdefault(sign, []).extend(ranges.spans)
        return cls(
            {
                path: {sign: Ranges.of(found) for sign, found in sides.items()}
                for path, sides in spans.items()
            }
        )

    @property
    def paths(self) -> list[str]:
        return list(self.by_path)

    def side(self, path: str, sign: Sign) -> Ranges:
        return self.by_path.get(path, {}).get(sign, _EMPTY)

    def __len__(self) -> int:
        return sum(len(ranges) for sides in self.by_path.values() for ranges in sides.values())

    def count(self, sign: Sign) -> int:
        return sum(len(sides.get(sign, _EMPTY)) for sides in self.by_path.values())

    def __bool__(self) -> bool:
        return bool(self.by_path)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, LineSet) and self.by_path == other.by_path

    def __hash__(self) -> int:
        return hash(
            tuple(
                (path, tuple(sorted(sides.items(), key=lambda item: item[0])))
                for path, sides in sorted(self.by_path.items())
            )
        )

    def __repr__(self) -> str:
        return f"LineSet({self.spans()})"

    def __contains__(self, point: object) -> bool:
        return isinstance(point, LinePoint) and point.lineno in self.side(point.path, point.sign)

    def _combine(self, other: LineSet, op: Callable[[Ranges, Ranges], Ranges]) -> LineSet:
        paths = dict.fromkeys([*self.by_path, *other.by_path])
        return LineSet(
            {
                path: {sign: op(self.side(path, sign), other.side(path, sign)) for sign in SIGNS}
                for path in paths
            }
        )

    def __or__(self, other: LineSet) -> LineSet:
        if not other:
            return self
        if not self:
            return other
        return self._combine(other, Ranges.__or__)

    def __and__(self, other: LineSet) -> LineSet:
        return LineSet(
            {
                path: {sign: ranges & other.side(path, sign) for sign, ranges in sides.items()}
                for path, sides in self.by_path.items()
                if path in other.by_path
            }
        )

    def __sub__(self, other: LineSet) -> LineSet:
        if not other:
            return self
        return LineSet(
            {
                path: {sign: ranges - other.side(path, sign) for sign, ranges in sides.items()}
                for path, sides in self.by_path.items()
            }
        )

    def only(self, paths: Iterable[str]) -> LineSet:
        keep = set(paths)
        return LineSet({path: sides for path, sides in self.by_path.items() if path in keep})

    def mapped(self, map_ranges: Callable[[str, Sign, Ranges], Ranges]) -> LineSet:
        """Each file's ranges replaced by ``map_ranges(path, sign, ranges)``."""
        return LineSet(
            {
                path: {sign: map_ranges(path, sign, ranges) for sign, ranges in sides.items()}
                for path, sides in self.by_path.items()
            }
        )

    def files(self) -> list[tuple[str, list[LineRange], list[LineRange]]]:
        """Each file's added and deleted ranges, in first-seen order."""
        return [
            (path, list(self.side(path, "+").spans), list(self.side(path, "-").spans))
            for path in self.by_path
        ]

    def spans(self) -> list[str]:
        """One compact entry per file, such as ``a.py +3-9 -4``, in first-seen order."""
        return [
            f"{path} "
            + " ".join(
                f"{sign}{start}" + (f"-{end}" if end != start else "")
                for sign, spans in (("+", added), ("-", deleted))
                for start, end in spans
            )
            for path, added, deleted in self.files()
        ]

    def dump(self) -> dict[str, JsonValue]:
        """Each path's ranges by sign, as ``{"a.py": {"+": [[1, 3]], "-": [[7, 7]]}}``."""
        dumped: dict[str, JsonValue] = {}
        for path, sides in self.by_path.items():
            dumped[path] = {
                sign: [[start, end] for start, end in ranges.spans]
                for sign, ranges in sides.items()
            }
        return dumped

    @classmethod
    def load(cls, value: object) -> Self:
        """A set from ``dump``'s shape, or from a list of single lines as older plans stored them."""
        if isinstance(value, LineSet):
            return cls(value.by_path)
        if isinstance(value, list):
            spans: dict[str, dict[Sign, list[LineRange]]] = {}
            for item in value:
                point = LinePoint.model_validate(item)
                spans.setdefault(point.path, {}).setdefault(point.sign, []).append(
                    (point.lineno, point.lineno)
                )
            return cls(
                {
                    path: {sign: Ranges.of(found) for sign, found in sides.items()}
                    for path, sides in spans.items()
                }
            )
        if not isinstance(value, dict):
            raise ValueError("a line set is a mapping of paths to ranges")
        files: dict[str, dict[Sign, Ranges]] = {}
        for path, sides in value.items():
            if not isinstance(path, str) or not isinstance(sides, dict):
                raise ValueError("a line set maps each path to its ranges by sign")
            files[path] = {}
            for sign in SIGNS:
                raw = sides.get(sign, [])
                if not isinstance(raw, list):
                    raise ValueError("ranges are a list of [start, end] pairs")
                files[path][sign] = Ranges.of(_pair(span) for span in raw)
        return cls(files)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: type[object], handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            cls.load,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda lines: lines.dump()
            ),
        )


def _pair(span: object) -> LineRange:
    if (
        isinstance(span, list | tuple)
        and len(span) == 2
        and isinstance(span[0], int)
        and isinstance(span[1], int)
    ):
        return span[0], span[1]
    raise ValueError("a range is a [start, end] pair of line numbers")
