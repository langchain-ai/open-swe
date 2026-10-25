"""A planner's hold on one pull request: its pinned checkout and the shared plan at that head.

Any agent with a checkout of the PR can plan: the review scout in the
background, or a review guide that got ahead of it. Opening the workspace
carries the stored plan to the checkout's head first, so planners only ever
place hunks that are new there. Only a checkout at the PR's current head may
carry it; one pinned before a push raises ``PlanMovedError``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from typing import Self

from deepagents.backends.protocol import SandboxBackendProtocol

from openswe.github.http import GitHubAppUnavailable
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest
from openswe.walkthrough.checkout import PinnedCheckout
from openswe.walkthrough.diff import FileChange, LineRef
from openswe.walkthrough.plan import (
    MAX_EXPLANATION_CHARS,
    MAX_OTHER_SUMMARY_CHARS,
    MAX_TITLE_CHARS,
    FileHunks,
    FileSplits,
    Hunks,
    Plan,
    PlanChunk,
    PlannedChunk,
    PlanStatus,
    RangeError,
    by_name,
    find_line,
    line_name,
)
from openswe.walkthrough.record import PlanMovedError, Walkthrough

_MAX_UNPLANNED_FILES = 200


class PlannerUnavailableError(RuntimeError):
    """The checkout is not pinned to the pull request yet."""


async def _current_head(pull_request: PullRequest) -> str:
    try:
        async with PullRequestClient.as_app(
            pull_request.owner, pull_request.repo, pull_request.number
        ) as pull:
            return await pull.head_sha()
    except GitHubAppUnavailable as exc:
        raise PlannerUnavailableError("GitHub is unavailable; try again next turn") from exc


@dataclass
class PlanWorkspace:
    pull_request: PullRequest
    checkout: PinnedCheckout
    head_sha: str
    changes: list[FileChange]
    plan: Plan

    @classmethod
    async def open(cls, pull_request: PullRequest, checkout: PinnedCheckout) -> Self:
        pinned = await checkout.pinned()
        if pinned is None:
            raise PlannerUnavailableError("the checkout is not ready; try again next turn")
        head_sha = pinned[1]
        changes = await checkout.changes()
        plan = await cls._carry(pull_request, checkout, head_sha, changes)
        return cls(pull_request, checkout, head_sha, changes, plan)

    @classmethod
    async def locate(
        cls, backend: SandboxBackendProtocol, *, owner: str, repo: str, number: int
    ) -> Self:
        """The workspace for a pull request checked out in ``backend``."""
        checkout = await PinnedCheckout.locate(backend, repo)
        pull_request = await PullRequest(owner=owner, repo=repo, number=number).ensure()
        return await cls.open(pull_request, checkout)

    @staticmethod
    async def _carry(
        pull_request: PullRequest,
        checkout: PinnedCheckout,
        head_sha: str,
        changes: list[FileChange],
    ) -> Plan:
        stored = await Walkthrough.get(pull_request.id)
        if stored is not None and stored.head_sha == head_sha:
            return stored.plan
        # A checkout pinned before a push must not carry a newer plan back to its old head.
        if stored is not None and await _current_head(pull_request) != head_sha:
            raise PlanMovedError("the pull request moved on; start from its new head")
        plan = (
            stored.plan.carried_to(head_sha, changes)
            if stored is not None
            else Plan.start(head_sha, changes)
        )
        codes = await checkout.render_chunks([chunk.lines for chunk in plan.chunks], changes)
        for chunk, code in zip(plan.chunks, codes, strict=True):
            chunk.code = code
        if await Walkthrough.install(
            pull_request.id,
            plan,
            merge_base_sha=await checkout.merge_base(),
            changes=changes,
            replacing=stored.head_sha if stored is not None else None,
        ):
            return plan
        moved = await Walkthrough.get(pull_request.id)
        if moved is None or moved.head_sha != head_sha:
            raise PlanMovedError("another reader moved the plan to a different head")
        return moved.plan

    # Each is computed once per plan: ``_edit`` drops them when it replaces the plan.
    @cached_property
    def hunks(self) -> Hunks:
        return self.plan.hunks(self.changes)

    @cached_property
    def unplanned(self) -> list[LineRef]:
        return self.plan.unplanned(self.changes)

    @property
    def complete(self) -> bool:
        return not self.unplanned

    def status(self) -> PlanStatus:
        hunks, unplanned = self.hunks, self.unplanned
        spans = hunks.spans(unplanned)
        return PlanStatus(
            chunks=[
                PlannedChunk(
                    number=i,
                    title=c.title,
                    hunks=hunks.spans(c.lines),
                )
                for i, c in enumerate(self.plan.chunks, start=1)
            ],
            other_lines=len(self.plan.other),
            unplanned=hunks.summary(unplanned) if unplanned else "nothing",
            unplanned_hunks=spans[:_MAX_UNPLANNED_FILES],
            more_files_unplanned=max(0, len(spans) - _MAX_UNPLANNED_FILES),
        )

    async def _edit(self, change: Callable[[Plan], object]) -> None:
        def apply(plan: Plan) -> Plan:
            change(plan)
            return plan

        self.plan = await Walkthrough.edit(
            self.pull_request.id, head_sha=self.head_sha, changes=self.changes, change=apply
        )
        for derived in ("hunks", "unplanned"):
            self.__dict__.pop(derived, None)

    async def plan_chunk(
        self,
        *,
        title: str,
        show: list[FileHunks],
        explanation: str,
        other: list[FileHunks] | None = None,
        after: int | None = None,
        replace: list[int] | None = None,
    ) -> int:
        """Place a chunk of unplanned hunks; hunks in ``other`` go to Other first. Its number."""
        trimmed = " ".join(title.split())[:MAX_TITLE_CHARS]
        if not trimmed:
            raise RangeError("title must name the chunk")
        numbers = set(replace or [])
        if any(number < 1 or number > len(self.plan.chunks) for number in numbers):
            raise RangeError("replace must name existing chunk numbers")
        replaced = {
            chunk.id: chunk.model_copy(deep=True)
            for number, chunk in enumerate(self.plan.chunks, start=1)
            if number in numbers
        }
        released = {ref for chunk in replaced.values() for ref in chunk.lines}
        planned = self.plan.planned() - released
        unplanned = [
            line.ref for change in self.changes for line in change.lines if line.ref not in planned
        ]
        hunks = self.hunks
        to_other = hunks.claim(other, unplanned) if other else []
        taken = set(to_other)
        lines = hunks.claim(show, [ref for ref in unplanned if ref not in taken])
        chunk = PlanChunk(
            title=trimmed,
            explanation=explanation.strip()[:MAX_EXPLANATION_CHARS],
            lines=lines,
            code=await self.checkout.render(lines, self.changes),
        )
        number = 0

        def place(plan: Plan) -> None:
            nonlocal number
            if any(plan.chunk(chunk_id) != original for chunk_id, original in replaced.items()):
                raise RangeError("another planner changed a chunk to replace; check again")
            planned = plan.planned() - released
            if any(ref in planned for ref in [*to_other, *lines]):
                raise RangeError("another planner just placed some of these lines; check again")
            position = after
            if replaced and position is None:
                position = next(i for i, c in enumerate(plan.chunks) if c.id in replaced)
            plan.chunks = [c for c in plan.chunks if c.id not in replaced]
            plan.add_other(to_other)
            number = plan.add_chunk(chunk, after=position)

        await self._edit(place)
        return number

    async def move_to_other(self, files: list[FileHunks], *, restore: bool = False) -> None:
        if restore:
            other = set(self.plan.other)
            pool = [line.ref for c in self.changes for line in c.lines if line.ref in other]
            refs = self.hunks.claim(files, pool)
            await self._edit(lambda plan: plan.restore_other(refs))
            return
        refs = self.hunks.claim(files, self.unplanned)

        def send(plan: Plan) -> None:
            planned = plan.planned()
            if any(ref in planned for ref in refs):
                raise RangeError("another planner just placed some of these lines; check again")
            plan.add_other(refs)

        await self._edit(send)

    async def split(self, files: list[FileSplits]) -> None:
        """Start a new hunk at each named unplanned line, splitting the hunk it sits in."""
        paths = {split.path for split in files}
        # Only the named files' hunks can change.
        touched = [ref for ref in self.unplanned if ref.path in paths]
        named = by_name(touched)
        hunks = self.hunks
        refs: list[LineRef] = []
        for split in files:
            for name in split.at:
                ref = find_line(split.path, name, named)
                if hunks.key(ref) == (ref.path, line_name(ref)):
                    raise RangeError(f"`{ref.path}` {name} already starts a hunk")
                refs.append(ref)
        if not refs:
            raise RangeError("no lines are named to split at")
        split_hunks = Hunks.of(self.changes, [*self.plan.splits, *refs])
        if fragments := split_hunks.fragments(hunks, touched):
            raise RangeError(
                "these splits leave hunks of only braces, punctuation or blank lines: "
                + ", ".join(f"`{path}` {name}" for path, name in fragments)
                + "; split where real code starts so each block keeps its closing lines"
            )

        def split_at(plan: Plan) -> None:
            planned = plan.planned()
            if any(ref in planned for ref in refs):
                raise RangeError("another planner just placed some of these lines; check again")
            plan.split(refs)

        await self._edit(split_at)

    async def settle(self) -> int:
        """Send every line still unplanned to Other, completing the plan; how many there were."""
        refs = self.unplanned
        if refs:
            await self._edit(lambda plan: plan.add_other(refs))
        return len(refs)

    async def describe_other(self, summary: str) -> None:
        trimmed = summary.strip()[:MAX_OTHER_SUMMARY_CHARS]

        def describe(plan: Plan) -> None:
            plan.other_summary = trimmed

        await self._edit(describe)
