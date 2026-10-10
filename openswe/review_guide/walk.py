"""Where one reader stands in the pull request's shared walkthrough plan.

The plan says what to read and in what order; the walk says what this reader
did with it. A line counts as reviewed only when the reader clicked "Looks
good" on a chunk showing it. That is remembered per person by content, so it
stays reviewed across rebases and across channels. Having a chunk on screen,
or skipping it, never counts.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Self

from pydantic import BaseModel

from openswe.walkthrough.diff import ChangedLine, FileChange, line_key
from openswe.walkthrough.plan import Hunks, LineRef, Plan, PlanChunk
from openswe.walkthrough.render import fenced

OTHER = "other"
CUSTOM = "custom"
_MAX_LEFT_FILES = 200


class OnScreen(BaseModel):
    # The plan chunk's id, ``OTHER``, or ``CUSTOM`` for a cut made for this reader.
    chunk_id: str
    title: str
    # Exactly the lines the message offers to approve.
    lines: list[LineRef]
    other_files: list[str] = []
    message_ts: str = ""
    message_text: str = ""
    run_id: str = ""


class Approval(BaseModel):
    title: str
    lines: list[LineRef]
    other: bool = False


class Skip(BaseModel):
    reason: str
    lines: list[LineRef]


class Walk(BaseModel):
    head_sha: str
    on_screen: OnScreen | None = None
    approvals: list[Approval] = []
    skips: list[Skip] = []
    other_skipped: bool = False
    # Fingerprints of Other's textless files the reader approved.
    other_files_seen: list[str] = []
    # Chunk ids the reader asked to see next, ahead of the plan's order.
    order: list[str] = []
    # The run that last put something on screen: one per turn, then the reader.
    shown_by_run: str = ""

    def moved_to(self, head_sha: str, changes: list[FileChange]) -> tuple[Self, OnScreen | None]:
        """This walk on a new head, following every line by content.

        What is on screen stays only if all of its lines are still there.
        Returns the new walk and what was on screen if it had to go.
        """
        pool: dict[tuple[str, str, str], list[LineRef]] = {}
        for change in changes:
            for line in change.lines:
                ref = LineRef.of(line)
                pool.setdefault(ref.content, []).append(ref)

        def take(refs: list[LineRef]) -> list[LineRef]:
            return [bucket.pop(0) for ref in refs if (bucket := pool.get(ref.content))]

        moved = self.model_copy(
            update={
                "head_sha": head_sha,
                "on_screen": None,
                "approvals": [
                    a.model_copy(update={"lines": lines})
                    for a in self.approvals
                    if (lines := take(a.lines))
                ],
                "skips": [
                    s.model_copy(update={"lines": lines})
                    for s in self.skips
                    if (lines := take(s.lines))
                ],
            }
        )
        current = self.on_screen
        if current is None:
            return moved, None
        lines = take(current.lines)
        if len(lines) != len(current.lines):
            return moved, current
        moved.on_screen = current.model_copy(update={"lines": lines})
        return moved, None

    def approve(self, message_ts: str) -> Approval | None:
        """Approve what is on screen if ``message_ts`` is its message; returns the approval."""
        current = self.on_screen
        if current is None or not message_ts or current.message_ts != message_ts:
            return None
        approval = Approval(
            title=current.title, lines=current.lines, other=current.chunk_id == OTHER
        )
        self.approvals.append(approval)
        self.other_files_seen += current.other_files
        self.on_screen = None
        return approval

    def withdraw(self) -> OnScreen | None:
        """Take what is on screen down unapproved; returns it."""
        current, self.on_screen = self.on_screen, None
        return current

    def skip(self, reason: str, lines: list[LineRef], *, other: bool = False) -> None:
        if lines:
            self.skips.append(Skip(reason=reason.strip(), lines=lines))
        if other:
            self.other_skipped = True

    @property
    def skipped(self) -> set[LineRef]:
        return {ref for s in self.skips for ref in s.lines}

    def coverage(self) -> str:
        """A one-line account of what the reader saw, for the closing message and the approval."""
        approved = [a for a in self.approvals if not a.other]
        parts = [
            f"Walked through {len(approved)} chunks "
            f"({sum(len(a.lines) for a in approved)} changed lines)"
        ]
        if self.skips:
            reasons = "; ".join(f"“{s.reason}”" for s in self.skips if s.reason)
            parts.append(
                f"skipped {sum(len(s.lines) for s in self.skips)} lines at the reader's request"
                + (f": {reasons}" if reasons else "")
            )
        other = next((a for a in self.approvals if a.other), None)
        if other is not None:
            parts.append(f"Other, {len(other.lines)} lines, summarized")
        elif self.other_skipped:
            parts.append("Other skipped")
        return "; ".join(parts) + "."


class ReaderChunk(BaseModel):
    number: int
    title: str
    state: str
    lines: list[str]


class WalkStatus(BaseModel):
    """Where the reader stands, as the guide is told at every turn and after every tool."""

    on_screen: str | None
    chunks: list[ReaderChunk]
    next: int | None
    # Chunk numbers the reader asked to see next, in order.
    asked: list[int] = []
    other_lines: int
    other_state: str
    unplanned: str
    unplanned_hunks: list[str]
    more_files_unplanned: int = 0


@dataclass
class Reader:
    """One reader's walk over the plan, with the lines they have reviewed."""

    walk: Walk
    plan: Plan
    unseen: set[LineRef]

    @classmethod
    def of(cls, walk: Walk, plan: Plan, seen: Counter[str], extra: Iterable[LineRef] = ()) -> Self:
        """``extra`` adds lines the plan does not hold yet, so they are judged too."""
        refs = [ref for chunk in plan.chunks for ref in chunk.lines] + plan.other + [*extra]
        return cls(walk, plan, _unseen(refs, seen, walk))

    def remaining(self, lines: Iterable[LineRef]) -> list[LineRef]:
        skipped = self.walk.skipped
        return [ref for ref in lines if ref in self.unseen and ref not in skipped]

    def chunk(self, number: int) -> PlanChunk | None:
        return self.plan.chunks[number - 1] if 1 <= number <= len(self.plan.chunks) else None

    def next_chunk(self) -> PlanChunk | None:
        """The first chunk the reader asked for that is still left, else the plan's next."""
        current = self.walk.on_screen.chunk_id if self.walk.on_screen else None
        asked = [c for chunk_id in self.walk.order if (c := self.plan.chunk(chunk_id))]
        return next(
            (c for c in [*asked, *self.plan.chunks] if c.id != current and self.remaining(c.lines)),
            None,
        )

    def custom(self, title: str, explanation: str, lines: list[LineRef], code: str) -> OnScreen:
        """A chunk cut for this reader alone, outside the plan."""
        return OnScreen(
            chunk_id=CUSTOM,
            title=title,
            lines=self.remaining(lines),
            message_text=f"*{title}*\n{explanation}\n\n{code}".strip(),
        )

    @property
    def other_files_left(self) -> list[str]:
        seen = set(self.walk.other_files_seen)
        return [
            path
            for path, fingerprint in zip(
                self.plan.other_files, self.plan.other_file_fingerprints, strict=True
            )
            if fingerprint not in seen
        ]

    @property
    def other_pending(self) -> bool:
        if self.walk.other_skipped:
            return False
        return bool(self.remaining(self.plan.other) or self.other_files_left)

    def show(self, chunk: PlanChunk) -> OnScreen:
        lines = self.remaining(chunk.lines)
        number = self.plan.number_of(chunk.id)
        header = f"*{number}/{len(self.plan.chunks)} · {chunk.title}*"
        return OnScreen(
            chunk_id=chunk.id,
            title=chunk.title,
            lines=lines,
            message_text=f"{header}\n{chunk.explanation}\n\n{chunk.code}".strip(),
        )

    def show_other(self) -> OnScreen:
        lines = self.remaining(self.plan.other)
        files = self.other_files_left
        stat = _other_stat(lines, files)
        text = f"*Other · {len(lines)} lines*\n{self.plan.other_summary}\n\n{fenced(stat)}"
        seen_files = [
            fingerprint
            for path, fingerprint in zip(
                self.plan.other_files, self.plan.other_file_fingerprints, strict=True
            )
            if path in files
        ]
        return OnScreen(
            chunk_id=OTHER,
            title="Other",
            lines=lines,
            other_files=seen_files,
            message_text=text.strip(),
        )

    def unfinished(self, unplanned: list[LineRef], *, other: bool = True) -> list[str]:
        """What still stands between the reader and the end of the walkthrough, or Other."""
        reasons: list[str] = []
        left = [c for c in self.plan.chunks if self.remaining(c.lines)]
        if left:
            reasons.append(f"{len(left)} chunks are not yet approved or skipped")
        if self.remaining(unplanned):
            reasons.append("some changed lines are not planned yet")
        if self.walk.on_screen is not None:
            reasons.append(f"“{self.walk.on_screen.title}” is on screen and not yet approved")
        if other and self.other_pending:
            reasons.append("Other has not been approved or skipped")
        return reasons

    def status(self, unplanned: list[ChangedLine], hunks: Hunks) -> WalkStatus:
        current = self.walk.on_screen
        upcoming = self.next_chunk()
        chunks: list[ReaderChunk] = []
        for number, chunk in enumerate(self.plan.chunks, start=1):
            left = self.remaining(chunk.lines)
            state = (
                "on screen"
                if current is not None and current.chunk_id == chunk.id
                else "left"
                if left
                else "done"
            )
            chunks.append(
                ReaderChunk(
                    number=number, title=chunk.title, state=state, lines=LineRef.spans(left)
                )
            )
        other_state = (
            "on screen"
            if current is not None and current.chunk_id == OTHER
            else "skipped"
            if self.walk.other_skipped
            else "left"
            if self.other_pending
            else "done"
        )
        left = set(self.remaining(LineRef.of(line) for line in unplanned))
        open_lines = [line for line in unplanned if LineRef.of(line) in left]
        spans = hunks.spans(open_lines)
        return WalkStatus(
            on_screen=current.title if current else None,
            chunks=chunks,
            next=self.plan.number_of(upcoming.id) if upcoming else None,
            asked=[
                number
                for chunk_id in self.walk.order
                if (chunk := self.plan.chunk(chunk_id))
                and self.remaining(chunk.lines)
                and (number := self.plan.number_of(chunk_id))
            ],
            other_lines=len(self.remaining(self.plan.other)),
            other_state=other_state,
            unplanned=f"{len(open_lines)} lines" if open_lines else "nothing",
            unplanned_hunks=spans[:_MAX_LEFT_FILES],
            more_files_unplanned=max(0, len(spans) - _MAX_LEFT_FILES),
        )

    def progress_line(self) -> str:
        left = sum(1 for c in self.plan.chunks if self.remaining(c.lines))
        return f"{left} of {len(self.plan.chunks)} chunks left." if left else ""


def _unseen(refs: list[LineRef], seen: Counter[str], walk: Walk) -> set[LineRef]:
    """The lines not yet approved, each approval consuming one line with the same content.

    This walk's own approvals settle first, so of two identical lines the one the
    reader approved is the one that counts as seen.
    """
    remaining = Counter(seen)
    approved = {ref for a in walk.approvals for ref in a.lines}
    settled: set[LineRef] = set()
    for ref in refs:
        key = line_key(ref.path, ref.sign, ref.text)
        if ref in approved and remaining[key] > 0:
            remaining[key] -= 1
            settled.add(ref)
    out: set[LineRef] = set()
    for ref in refs:
        if ref in settled:
            continue
        key = line_key(ref.path, ref.sign, ref.text)
        if remaining[key] > 0:
            remaining[key] -= 1
        else:
            out.add(ref)
    return out


def _other_stat(lines: list[LineRef], files: list[str]) -> str:
    by_path: dict[str, list[LineRef]] = {}
    for ref in lines:
        by_path.setdefault(ref.path, []).append(ref)
    rows = [
        f"{path}  +{sum(r.sign == '+' for r in refs)} -{sum(r.sign == '-' for r in refs)}"
        for path, refs in by_path.items()
    ]
    rows += [f"{path}  (no text changes)" for path in files]
    return "\n".join(rows)
