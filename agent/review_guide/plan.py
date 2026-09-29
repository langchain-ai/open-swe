"""The walkthrough plan: every unreviewed changed line in exactly one chunk, or in Other.

The guide proposes chunks as line ranges; :func:`build_plan` checks them
against the real diff and puts every line no chunk claims into Other, so the
plan always covers the whole pull request. Progress is tracked per chunk, and
the walkthrough cannot finish while a chunk is neither approved nor skipped by
the reader, or while Other has not been shown.
"""

from typing import Literal, Self

from pydantic import BaseModel

from agent.review_guide.diff import ChangedLine, FileChange, Sign

ChunkStatus = Literal["planned", "shown", "approved", "skipped"]
OtherStatus = Literal["planned", "shown", "approved", "skipped"]
LineRange = tuple[int, int]
_MAX_ERRORS = 20


class PlanError(ValueError):
    """The proposed chunks do not match the pull request's unreviewed lines."""


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


class ChunkSpec(BaseModel):
    title: str
    files: list[FileRanges]


class Chunk(BaseModel):
    title: str
    lines: list[LineRef]
    status: ChunkStatus = "planned"
    skip_reason: str = ""


class Plan(BaseModel):
    head_sha: str
    chunks: list[Chunk]
    other: list[LineRef]
    # Binary, mode-only and empty-file changes: nothing to show, so always Other.
    other_files: list[str] = []
    other_status: OtherStatus = "planned"

    @property
    def has_other(self) -> bool:
        return bool(self.other or self.other_files)

    def on_screen(self) -> int | None:
        return next((i for i, chunk in enumerate(self.chunks) if chunk.status == "shown"), None)

    def next_chunk(self) -> int | None:
        current = self.on_screen()
        if current is not None:
            return current
        return next((i for i, chunk in enumerate(self.chunks) if chunk.status == "planned"), None)

    def unfinished(self) -> list[str]:
        """What still stands between the reader and the end of the walkthrough."""
        left = [
            f"chunk {i + 1} “{chunk.title}” is {chunk.status}"
            for i, chunk in enumerate(self.chunks)
            if chunk.status in ("planned", "shown")
        ]
        if self.has_other and self.other_status in ("planned", "shown"):
            left.append(f"Other ({len(self.other)} lines) is {self.other_status}")
        return left

    def coverage(self) -> str:
        """A one-line account of what the reader saw, for the closing message and the approval."""
        approved = [c for c in self.chunks if c.status == "approved"]
        skipped = [c for c in self.chunks if c.status == "skipped"]
        parts = [
            f"Walked through {len(approved)} of {len(self.chunks)} chunks "
            f"({sum(len(c.lines) for c in approved)} changed lines)"
        ]
        if skipped:
            titles = "; ".join(f"“{c.title}”" for c in skipped)
            parts.append(
                f"skipped {len(skipped)} at the reader's request "
                f"({sum(len(c.lines) for c in skipped)} lines): {titles}"
            )
        if self.has_other:
            shown = {"approved": "summarized", "skipped": "skipped"}.get(
                self.other_status, "not yet shown"
            )
            parts.append(f"Other, {len(self.other)} lines, {shown}")
        return "; ".join(parts) + "."


def build_plan(
    head_sha: str, files: list[FileChange], unseen: list[ChangedLine], specs: list[ChunkSpec]
) -> Plan:
    """Chunks as ``specs`` name them, with every unclaimed unseen line in Other."""
    order = {LineRef.of(line): i for i, line in enumerate(unseen)}
    changed = {file.path for file in files}
    claimed: dict[LineRef, int] = {}
    errors: list[str] = []
    chunks: list[Chunk] = []
    for number, spec in enumerate(specs, start=1):
        refs: set[LineRef] = set()
        for ranges in spec.files:
            if ranges.path not in changed:
                errors.append(f"chunk {number}: `{ranges.path}` is not changed by this PR")
                continue
            for sign, spans in (("+", ranges.added), ("-", ranges.deleted)):
                for start, end in spans:
                    hits = [
                        ref
                        for ref in order
                        if ref.path == ranges.path
                        and ref.sign == sign
                        and start <= ref.lineno <= end
                    ]
                    if not hits:
                        kind = "added" if sign == "+" else "deleted"
                        errors.append(
                            f"chunk {number}: `{ranges.path}` {kind} {start}-{end} "
                            "holds no unreviewed changed line"
                        )
                    for ref in hits:
                        owner = claimed.setdefault(ref, number)
                        if owner != number:
                            errors.append(
                                f"chunk {number}: `{ref.path}` {ref.sign}{ref.lineno} "
                                f"is already in chunk {owner}"
                            )
                        refs.add(ref)
        if not refs:
            errors.append(f"chunk {number} “{spec.title}” claims no lines")
        chunks.append(
            Chunk(
                title=" ".join(spec.title.split())[:120], lines=sorted(refs, key=order.__getitem__)
            )
        )
    if errors:
        more = len(errors) - _MAX_ERRORS
        raise PlanError(
            "\n".join(errors[:_MAX_ERRORS]) + (f"\n…and {more} more" if more > 0 else "")
        )
    return Plan(
        head_sha=head_sha,
        chunks=chunks,
        other=[ref for ref in order if ref not in claimed],
        other_files=[file.path for file in files if file.textless],
    )


def other_stat(plan: Plan) -> str:
    """Per-file counts of Other's lines, plus files with nothing to show."""
    by_path: dict[str, list[LineRef]] = {}
    for ref in plan.other:
        by_path.setdefault(ref.path, []).append(ref)
    rows = [
        f"{path}  +{sum(r.sign == '+' for r in refs)} -{sum(r.sign == '-' for r in refs)}"
        for path, refs in by_path.items()
    ]
    rows += [f"{path}  (no text changes)" for path in plan.other_files]
    return "\n".join(rows)
