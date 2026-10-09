"""Where a walkthrough stands: what the reader has seen, what is on screen, and what is left.

There is no plan. The guide picks each chunk as the conversation goes, and
moves lines it judges not worth reading into Other as it notices them. Every
changed line is either left, on screen, approved (and so remembered as seen),
in Other, or skipped at the reader's request; the walkthrough cannot finish
while any line is left or on screen, or while Other has not been shown.
"""

from collections import Counter
from collections.abc import Iterable
from typing import Literal, Self

from pydantic import BaseModel

from openswe.review_guide.diff import ChangedLine, FileChange, Sign
from openswe.review_guide.diff import unseen as unseen_lines

GroupStatus = Literal["queued", "shown", "approved", "skipped"]
OtherStatus = Literal["open", "shown", "approved", "skipped"]
LineRange = tuple[int, int]
_MAX_ERRORS = 20
_MAX_LEFT_FILES = 200


class RangeError(ValueError):
    """The ranges do not name lines that are still left to review."""


class LineRef(BaseModel, frozen=True):
    path: str
    sign: Sign
    lineno: int
    # Kept so the walkthrough can follow a line to a new head by content.
    text: str = ""

    @classmethod
    def of(cls, line: ChangedLine) -> Self:
        return cls(path=line.path, sign=line.sign, lineno=line.lineno, text=line.text)

    @property
    def content(self) -> tuple[str, str, str]:
        return self.path, self.sign, self.text

    @classmethod
    def spans(cls, refs: Iterable[Self]) -> list[str]:
        """One compact entry per file, such as ``a.py +3-9 -4``, in first-seen order."""
        by_path: dict[str, list[Self]] = {}
        for ref in refs:
            by_path.setdefault(ref.path, []).append(ref)
        entries: list[str] = []
        for path, lines in by_path.items():
            parts: list[str] = []
            for sign in ("+", "-"):
                runs: list[list[int]] = []
                for number in sorted(r.lineno for r in lines if r.sign == sign):
                    if runs and number == runs[-1][-1] + 1:
                        runs[-1].append(number)
                    else:
                        runs.append([number])
                parts += [f"{sign}{r[0]}" + (f"-{r[-1]}" if len(r) > 1 else "") for r in runs]
            entries.append(f"{path} {' '.join(parts)}")
        return entries


class FileRanges(BaseModel):
    """Lines of one file: ``added`` in head numbering, ``deleted`` in merge-base numbering."""

    path: str
    added: list[LineRange] = []
    deleted: list[LineRange] = []


class Group(BaseModel):
    """A chunk prepared for the reader or shown to them, or lines they chose to skip.

    A queued chunk is fully rendered ahead of time, so "Next" can show it
    without waiting on the model.
    """

    title: str
    lines: list[LineRef]
    status: GroupStatus
    reason: str = ""
    # The Slack message showing the chunk, kept to take its button away once it is settled.
    message_ts: str = ""
    message_text: str = ""
    run_id: str = ""


class QueuedChunk(BaseModel):
    position: int
    title: str
    lines: list[str]


class WalkStatus(BaseModel):
    """Where the walkthrough stands, as the guide is told at every turn and after every tool."""

    on_screen: str | None
    queue: list[QueuedChunk]
    other_lines: int
    other_status: OtherStatus
    left: str
    left_lines: list[str]
    more_files_left: int = 0


class Walk(BaseModel):
    head_sha: str
    groups: list[Group] = []
    other: list[LineRef] = []
    # Binary, mode-only and empty-file changes: nothing to show, so always Other.
    other_files: list[str] = []
    # Their diff fingerprints, so a push that changes one reopens an approved Other.
    other_file_fingerprints: list[str] = []
    other_status: OtherStatus = "open"
    other_message_ts: str = ""
    other_message_text: str = ""
    # The run that last put something on screen: one chunk per turn, then the reader.
    shown_by_run: str = ""

    @classmethod
    def start(cls, head_sha: str, changes: list[FileChange]) -> Self:
        textless = [c for c in changes if c.textless]
        return cls(
            head_sha=head_sha,
            other_files=[c.path for c in textless],
            other_file_fingerprints=[c.fingerprint for c in textless],
        )

    def moved_to(self, head_sha: str, changes: list[FileChange]) -> tuple[Self, list[Group]]:
        """This walkthrough on a new head, following every line by content.

        A chunk on screen stays only if all of its lines are still there; any
        other group, and Other, keeps whatever lines survive. Returns the new
        walkthrough and the groups that are gone.
        """
        pool: dict[tuple[str, str, str], list[LineRef]] = {}
        for change in changes:
            for line in change.lines:
                ref = LineRef.of(line)
                pool.setdefault(ref.content, []).append(ref)

        def take(refs: list[LineRef], *, whole: bool) -> list[LineRef]:
            picked: list[LineRef] = []
            for ref in refs:
                bucket = pool.get(ref.content)
                if bucket:
                    picked.append(bucket.pop(0))
                elif whole:
                    for back in reversed(picked):
                        pool[back.content].insert(0, back)
                    return []
            return picked

        moved = type(self).start(head_sha, changes)
        gone: list[Group] = []
        for group in self.groups:
            # A queued chunk was rendered at the old line numbers; it is prepared again.
            if group.status == "queued":
                gone.append(group)
                continue
            lines = take(group.lines, whole=group.status == "shown")
            if lines:
                moved.groups.append(group.model_copy(update={"lines": lines}))
            else:
                gone.append(group)
        moved.other = take(self.other, whole=False)
        moved.other_status = self.other_status
        if self.other_status == "approved" and not set(moved.other_file_fingerprints) <= set(
            self.other_file_fingerprints
        ):
            moved.other_status = "open"
        moved.other_message_ts = self.other_message_ts
        moved.other_message_text = self.other_message_text
        return moved, gone

    @property
    def has_other(self) -> bool:
        return bool(self.other or self.other_files)

    def on_screen(self) -> Group | None:
        return next((g for g in self.groups if g.status == "shown"), None)

    @property
    def queue(self) -> list[Group]:
        """Chunks prepared ahead of the reader, in the order they will be shown."""
        return [g for g in self.groups if g.status == "queued"]

    def keep_queue(self, positions: list[int]) -> None:
        """Keep only these queued chunks (1-based positions), in this order."""
        queue = self.queue
        kept = [queue[p - 1] for p in positions]
        self.groups = [g for g in self.groups if g.status != "queued"] + kept

    def unseen(self, changes: list[FileChange], seen: Counter[str]) -> list[ChangedLine]:
        """The changed lines not yet approved, this walkthrough's own approvals by position."""
        refs = [ref for g in self.groups if g.status == "approved" for ref in g.lines]
        if self.other_status == "approved":
            refs += self.other
        return unseen_lines(changes, seen, {(ref.path, ref.sign, ref.lineno) for ref in refs})

    def left(self, unseen: list[ChangedLine]) -> list[ChangedLine]:
        """Unseen lines that are not queued, on screen, in Other, or skipped."""
        taken = set(self.other) | {ref for g in self.groups for ref in g.lines}
        return [line for line in unseen if LineRef.of(line) not in taken]

    def approve(self, message_ts: str) -> Group | None:
        """Approve the chunk or Other whose "Next" was clicked, if it is still on screen.

        Returns what was approved, with Other as a group of its lines.
        """
        current = self.on_screen()
        if current is not None and current.message_ts == message_ts:
            current.status = "approved"
            return current
        if self.other_status == "shown" and self.other_message_ts == message_ts:
            self.other_status = "approved"
            return Group(
                title="Other",
                lines=self.other,
                status="approved",
                message_ts=self.other_message_ts,
                message_text=self.other_message_text,
            )
        return None

    def withdraw(self) -> Group | None:
        """Put an unapproved chunk back, so its lines are left again; returns it."""
        current = self.on_screen()
        self.groups = [g for g in self.groups if g.status != "shown"]
        return current

    def add_other(self, refs: list[LineRef]) -> None:
        """New lines in Other need the reader's look again, even if Other was approved."""
        if refs:
            self.other += refs
            if self.other_status == "approved":
                self.other_status = "open"

    def status(self, unseen: list[ChangedLine]) -> WalkStatus:
        left = self.left(unseen)
        spans = LineRef.spans(LineRef.of(line) for line in left)
        current = self.on_screen()
        return WalkStatus(
            on_screen=current.title if current else None,
            queue=[
                QueuedChunk(position=i, title=g.title, lines=LineRef.spans(g.lines))
                for i, g in enumerate(self.queue, start=1)
            ],
            other_lines=len(self.other),
            other_status=self.other_status,
            left=summary(left) if left else "nothing left but Other",
            left_lines=spans[:_MAX_LEFT_FILES],
            more_files_left=max(0, len(spans) - _MAX_LEFT_FILES),
        )

    def unfinished(self, unseen: list[ChangedLine]) -> list[str]:
        """What still stands between the reader and the end of the walkthrough."""
        reasons: list[str] = []
        if left := self.left(unseen):
            reasons.append(summary(left))
        if queue := self.queue:
            reasons.append(f"{len(queue)} prepared chunks have not been shown")
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
