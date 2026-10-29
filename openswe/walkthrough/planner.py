"""A planner's hold on one pull request: its pinned checkout and the shared plan at that head.

Any agent with a checkout of the PR can plan: the review scout in the
background, or a review guide that got ahead of it. Opening the workspace
carries the stored plan to the checkout's head first, so planners only ever
place hunks that are new there. Only a checkout at the PR's current head may
carry it; one pinned before a push raises ``PlanMovedError``.

The plan and the diff's index are both line ranges, so planning costs what the
hunks cost, never what the lines do. The checkout's sandbox indexes the diff
once per head and renders chunks; the backend never reads the diff's lines.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from typing import Self

from deepagents.backends.protocol import SandboxBackendProtocol

from openswe.github.http import GitHubAppUnavailable
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest
from openswe.walkthrough.checkout import PinnedCheckout
from openswe.walkthrough.diff import DiffIndex
from openswe.walkthrough.lines import LinePoint, LineSet
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
    find_line,
)
from openswe.walkthrough.record import PlanMovedError, Walkthrough

logger = logging.getLogger(__name__)

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
    merge_base_sha: str
    index: DiffIndex
    plan: Plan
    _hunks: dict[tuple[LinePoint, ...], Hunks] = field(default_factory=dict, repr=False)

    @classmethod
    async def open(cls, pull_request: PullRequest, checkout: PinnedCheckout) -> Self:
        pinned = await checkout.pinned()
        if pinned is None:
            raise PlannerUnavailableError("the checkout is not ready; try again next turn")
        merge_base_sha, head_sha = pinned
        stored = await Walkthrough.get(pull_request.id, with_index=True)
        # A plan carried to this head from an older one keeps the older merge base.
        if stored is not None and (stored.merge_base_sha, stored.head_sha) == pinned:
            index = stored.diff_index
            if index is None:
                index = await checkout.index()
                await Walkthrough.save_index(pull_request.id, head_sha, index)
            return cls(pull_request, checkout, head_sha, stored.merge_base_sha, index, stored.plan)
        # A checkout pinned before a push must not carry a newer plan back to its old head.
        if stored is not None and await _current_head(pull_request) != head_sha:
            raise PlanMovedError("the pull request moved on; start from its new head")
        index = await checkout.index()
        plan = await cls._carry(stored, checkout, head_sha, index)
        if not await Walkthrough.install(
            pull_request.id,
            plan,
            merge_base_sha=merge_base_sha,
            index=index,
            replacing=stored.head_sha if stored is not None else None,
        ):
            moved = await Walkthrough.get(pull_request.id)
            if moved is None or moved.head_sha != head_sha:
                raise PlanMovedError("another reader moved the plan to a different head")
            plan = moved.plan
        return cls(pull_request, checkout, head_sha, merge_base_sha, index, plan)

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
        stored: Walkthrough | None, checkout: PinnedCheckout, head_sha: str, index: DiffIndex
    ) -> Plan:
        if stored is None:
            return Plan.start(head_sha, index)
        carry = await checkout.carry(stored.head_sha, stored.merge_base_sha, index)
        if carry is None:
            logger.warning(
                "Could not read a walkthrough's earlier head; planning its new head afresh",
                extra={"walkthrough_head_sha": stored.head_sha, "pr_head_sha": head_sha},
            )
            return Plan.start(head_sha, index)
        plan = stored.plan.carried_to(head_sha, index, carry)
        codes = await checkout.render_chunks([chunk.lines for chunk in plan.chunks])
        for chunk, code in zip(plan.chunks, codes, strict=True):
            chunk.code = code
        return plan

    @property
    def hunks(self) -> Hunks:
        # The index never changes, so the hunks only change when the plan's splits do.
        splits = tuple(self.plan.splits)
        if splits not in self._hunks:
            self._hunks[splits] = self.plan.hunks(self.index)
        return self._hunks[splits]

    # Computed once per plan: ``_edit`` drops it when it replaces the plan.
    @cached_property
    def unplanned(self) -> LineSet:
        return self.plan.unplanned(self.index)

    @property
    def complete(self) -> bool:
        return not self.unplanned

    def status(self) -> PlanStatus:
        hunks, unplanned = self.hunks, self.unplanned
        spans = hunks.spans(unplanned)
        return PlanStatus(
            chunks=[
                PlannedChunk(number=i, title=c.title, hunks=hunks.spans(c.lines))
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
            self.pull_request.id, head_sha=self.head_sha, index=self.index, change=apply
        )
        self.__dict__.pop("unplanned", None)

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
        released = LineSet.union(chunk.lines for chunk in replaced.values())
        unplanned = self.index.lines - (self.plan.planned() - released)
        to_other = self.hunks.claim(other, unplanned) if other else LineSet()
        lines = self.hunks.claim(show, unplanned - to_other)
        chunk = PlanChunk(
            title=trimmed,
            explanation=explanation.strip()[:MAX_EXPLANATION_CHARS],
            lines=lines,
            code=await self.checkout.render(lines),
        )
        number = 0

        def place(plan: Plan) -> None:
            nonlocal number
            if any(plan.chunk(chunk_id) != original for chunk_id, original in replaced.items()):
                raise RangeError("another planner changed a chunk to replace; check again")
            if (to_other | lines) & (plan.planned() - released):
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
            lines = self.hunks.claim(files, self.plan.other)
            await self._edit(lambda plan: plan.restore_other(lines))
            return
        lines = self.hunks.claim(files, self.unplanned)

        def send(plan: Plan) -> None:
            if lines & plan.planned():
                raise RangeError("another planner just placed some of these lines; check again")
            plan.add_other(lines)

        await self._edit(send)

    async def split(self, files: list[FileSplits]) -> None:
        """Start a new hunk at each named unplanned line, splitting the hunk it sits in."""
        unplanned = self.unplanned
        points: list[LinePoint] = []
        for split in files:
            for name in split.at:
                point = find_line(split.path, name, unplanned)
                hunk = self.hunks.holding(point)
                if hunk is not None and hunk.name == point.name:
                    raise RangeError(f"`{point.path}` {name} already starts a hunk")
                points.append(point)
        if not points:
            raise RangeError("no lines are named to split at")
        if fragments := await self._fragments(points):
            raise RangeError(
                "these splits leave hunks of only braces, punctuation or blank lines: "
                + ", ".join(f"`{path}` {name}" for path, name in fragments)
                + "; split where real code starts so each block keeps its closing lines"
            )

        def split_at(plan: Plan) -> None:
            planned = plan.planned()
            if any(point in planned for point in points):
                raise RangeError("another planner just placed some of these lines; check again")
            plan.split(points)

        await self._edit(split_at)

    async def _fragments(self, points: list[LinePoint]) -> list[tuple[str, str]]:
        """Hunks the split at ``points`` would make that hold only braces, punctuation or blank lines."""
        touched = self.unplanned.only(point.path for point in points)
        before = {part for _, part in self.hunks.within(touched)}
        after = Hunks.of(self.index, [*self.plan.splits, *points]).within(touched)
        new = [(hunk, part) for hunk, part in after if part not in before]
        wordless = await self.checkout.wordless([part for _, part in new])
        return [
            (hunk.path, hunk.name) for (hunk, _), empty in zip(new, wordless, strict=True) if empty
        ]

    async def settle(self) -> int:
        """Send every line still unplanned to Other, completing the plan; how many there were."""
        lines = self.unplanned
        if lines:
            await self._edit(lambda plan: plan.add_other(lines))
        return len(lines)

    async def describe_other(self, summary: str) -> None:
        trimmed = summary.strip()[:MAX_OTHER_SUMMARY_CHARS]

        def describe(plan: Plan) -> None:
            plan.other_summary = trimmed

        await self._edit(describe)
