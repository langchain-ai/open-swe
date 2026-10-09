"""A planner's hold on one pull request: its pinned checkout and the shared plan at that head.

Any agent with a checkout of the PR can plan: the review scout in the
background, or a review guide that got ahead of it. Opening the workspace
carries the stored plan to the checkout's head first, so planners only ever
place lines that are new there. Only a checkout at the PR's current head may
carry it; one pinned before a push raises ``PlanMovedError``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Self

from deepagents.backends.protocol import SandboxBackendProtocol

from openswe.github.http import GitHubAppUnavailable
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest
from openswe.walkthrough.checkout import PinnedCheckout
from openswe.walkthrough.diff import FileChange
from openswe.walkthrough.plan import (
    MAX_EXPLANATION_CHARS,
    MAX_OTHER_SUMMARY_CHARS,
    MAX_TITLE_CHARS,
    FileRanges,
    LineRef,
    Plan,
    PlanChunk,
    PlanStatus,
    RangeError,
    claim,
)
from openswe.walkthrough.record import PlanMovedError, Walkthrough


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
        head_files: dict[str, list[str]] = {}
        for chunk in plan.chunks:
            chunk.code = await checkout.render(chunk.lines, changes, head_files=head_files)
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

    @property
    def complete(self) -> bool:
        return not self.plan.unplanned(self.changes)

    def status(self) -> PlanStatus:
        return self.plan.status(self.changes)

    async def _edit(self, change: Callable[[Plan], object]) -> None:
        def apply(plan: Plan) -> Plan:
            change(plan)
            return plan

        self.plan = await Walkthrough.edit(
            self.pull_request.id, head_sha=self.head_sha, changes=self.changes, change=apply
        )

    async def plan_chunk(
        self,
        *,
        title: str,
        show: list[FileRanges],
        explanation: str,
        other: list[FileRanges] | None = None,
        after: int | None = None,
    ) -> int:
        """Place a chunk of unplanned lines; lines in ``other`` go to Other first. Its number."""
        trimmed = " ".join(title.split())[:MAX_TITLE_CHARS]
        if not trimmed:
            raise RangeError("title must name the chunk")
        unplanned = self.plan.unplanned(self.changes)
        to_other = claim(other, unplanned) if other else []
        taken = set(to_other)
        lines = claim(show, [line for line in unplanned if LineRef.of(line) not in taken])
        chunk = PlanChunk(
            title=trimmed,
            explanation=explanation.strip()[:MAX_EXPLANATION_CHARS],
            lines=lines,
            code=await self.checkout.render(lines, self.changes),
        )
        number = 0

        def place(plan: Plan) -> None:
            nonlocal number
            planned = plan.planned()
            if any(ref in planned for ref in [*to_other, *lines]):
                raise RangeError("another planner just placed some of these lines; check again")
            plan.add_other(to_other)
            number = plan.add_chunk(chunk, after=after)

        await self._edit(place)
        return number

    async def move_to_other(self, files: list[FileRanges], *, restore: bool = False) -> None:
        if restore:
            other = set(self.plan.other)
            pool = [line for c in self.changes for line in c.lines if LineRef.of(line) in other]
            refs = claim(files, pool)
            await self._edit(lambda plan: plan.restore_other(refs))
            return
        refs = claim(files, self.plan.unplanned(self.changes))

        def send(plan: Plan) -> None:
            planned = plan.planned()
            if any(ref in planned for ref in refs):
                raise RangeError("another planner just placed some of these lines; check again")
            plan.add_other(refs)

        await self._edit(send)

    async def settle(self) -> int:
        """Send every line still unplanned to Other, completing the plan; how many there were."""
        refs = [LineRef.of(line) for line in self.plan.unplanned(self.changes)]
        if refs:
            await self._edit(lambda plan: plan.add_other(refs))
        return len(refs)

    async def describe_other(self, summary: str) -> None:
        trimmed = summary.strip()[:MAX_OTHER_SUMMARY_CHARS]

        def describe(plan: Plan) -> None:
            plan.other_summary = trimmed

        await self._edit(describe)
