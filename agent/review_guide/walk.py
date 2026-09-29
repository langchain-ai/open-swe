"""Where a walkthrough stands: what the reader has seen, what is on screen, and what is left.

There is no plan. The guide picks each chunk as the conversation goes, and
moves lines it judges not worth reading into Other as it notices them. Every
changed line is either left, on screen, approved (and so remembered as seen),
in Other, or skipped at the reader's request; the walkthrough cannot finish
while any line is left or on screen, or while Other has not been shown.
"""

from typing import Literal, Self

from pydantic import BaseModel

from agent.review_guide.diff import ChangedLine, FileChange, Sign

GroupStatus = Literal["shown", "approved", "skipped"]
OtherStatus = Literal["open", "shown", "approved", "skipped"]
LineRange = tuple[int, int]
_MAX_ERRORS = 20


class RangeError(ValueError):
    """The ranges do not name lines that are still left to review."""


class LineRef(BaseModel, frozen=True):
    path: str
    sign: Sign
    lineno: int

    @classmethod
    def of(cls, line: ChangedLine) -> Self:
        return cls(path=line.path, sign=line.sign, lineno=line.lineno)


class FileRanges(BaseModel):
    """Lines of one file: ``added`` in head numbering, ``deleted`` in merge-base numbering."""

    path: str
    added: list[LineRange] = []
    deleted: list[LineRange] = []


class Group(BaseModel):
    """A chunk the reader was shown, or lines they chose to skip."""

    title: str
    lines: list[LineRef]
    status: GroupStatus
    reason: str = ""


class Walk(BaseModel):
    head_sha: str
    groups: list[Group] = []
    other: list[LineRef] = []
    # Binary, mode-only and empty-file changes: nothing to show, so always Other.
    other_files: list[str] = []
    other_status: OtherStatus = "open"

    @classmethod
    def start(cls, head_sha: str, changes: list[FileChange]) -> Self:
        return cls(head_sha=head_sha, other_files=[c.path for c in changes if c.textless])

    @property
    def has_other(self) -> bool:
        return bool(self.other or self.other_files)

    def on_screen(self) -> Group | None:
        return next((g for g in self.groups if g.status == "shown"), None)

    def left(self, unseen: list[ChangedLine]) -> list[ChangedLine]:
        """Unseen lines that are not on screen, in Other, or skipped."""
        taken = set(self.other) | {ref for g in self.groups for ref in g.lines}
        return [line for line in unseen if LineRef.of(line) not in taken]

    def withdraw(self) -> None:
        """Put an unapproved chunk back, so its lines are left again."""
        self.groups = [g for g in self.groups if g.status != "shown"]

    def unfinished(self, unseen: list[ChangedLine]) -> list[str]:
        """What still stands between the reader and the end of the walkthrough."""
        reasons: list[str] = []
        if left := self.left(unseen):
            reasons.append(summary(left))
        if (current := self.on_screen()) is not None:
            reasons.append(f"“{current.title}” is on screen and not yet approved")
        if self.has_other and self.other_status in ("open", "shown"):
            reasons.append(f"Other ({len(self.other)} lines) has not been approved")
        return reasons

    def coverage(self) -> str:
        """A one-line account of what the reader saw, for the closing message and the approval."""
        approved = [g for g in self.groups if g.status == "approved"]
        skipped = [g for g in self.groups if g.status == "skipped"]
        parts = [
            f"Walked through {len(approved)} chunks "
            f"({sum(len(g.lines) for g in approved)} changed lines)"
        ]
        if skipped:
            reasons = "; ".join(f"“{g.reason or g.title}”" for g in skipped)
            parts.append(
                f"skipped {sum(len(g.lines) for g in skipped)} lines at the reader's request: "
                f"{reasons}"
            )
        if self.has_other:
            shown = {"approved": "summarized", "skipped": "skipped"}.get(
                self.other_status, "not yet shown"
            )
            parts.append(f"Other, {len(self.other)} lines, {shown}")
        return "; ".join(parts) + "."


def summary(lines: list[ChangedLine]) -> str:
    files = {line.path for line in lines}
    return f"{len(lines)} changed lines left in {len(files)} file{'s' if len(files) != 1 else ''}"


def claim(
    files: list[FileRanges], candidates: list[ChangedLine], changes: list[FileChange]
) -> list[LineRef]:
    """The ``candidates`` the ranges name, in diff order; every range must name at least one."""
    order = {LineRef.of(line): i for i, line in enumerate(candidates)}
    changed = {change.path for change in changes}
    errors: list[str] = []
    refs: set[LineRef] = set()
    for ranges in files:
        if ranges.path not in changed:
            errors.append(f"`{ranges.path}` is not changed by this PR")
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
                    errors.append(
                        f"`{ranges.path}` {kind} {start}-{end} holds no line that is still left"
                    )
                refs.update(hits)
    if not refs and not errors:
        errors.append("the ranges name no lines")
    if errors:
        more = len(errors) - _MAX_ERRORS
        raise RangeError(
            "\n".join(errors[:_MAX_ERRORS]) + (f"\n…and {more} more" if more > 0 else "")
        )
    return sorted(refs, key=order.__getitem__)


def other_stat(walk: Walk) -> str:
    """Per-file counts of Other's lines, plus files with nothing to show."""
    by_path: dict[str, list[LineRef]] = {}
    for ref in walk.other:
        by_path.setdefault(ref.path, []).append(ref)
    rows = [
        f"{path}  +{sum(r.sign == '+' for r in refs)} -{sum(r.sign == '-' for r in refs)}"
        for path, refs in by_path.items()
    ]
    rows += [f"{path}  (no text changes)" for path in walk.other_files]
    return "\n".join(rows)
