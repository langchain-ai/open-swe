"""Where a plan under review lives, from the agent's side.

Plan mode publishes a plan for humans to review and then implements the version
they approved. The agent only needs four operations on it; where the document,
its status and the reviewers' comments are actually stored is the platform's.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

# Plans are mirrored into the sandbox outside cloned repositories.
PLAN_FILE_DIRECTORY = "/workspace/plans"


class PlanNotApprovable(Exception):
    """The plan cannot be approved. The message reaches the model."""


@dataclass(frozen=True, slots=True)
class ApprovedPlan:
    """The plan the reviewers approved, and what they said about it."""

    document: str
    reviewer_feedback: str = ""


class PlanStore(Protocol):
    """One thread's plan."""

    async def begin(self) -> None:
        """Record that the thread has entered plan mode."""

    async def publish(self, *, document: str, source_path: str, plan_mode: bool) -> None:
        """Publish the document for review, or share it when not planning."""

    async def approve(self) -> ApprovedPlan:
        """Approve the reviewed plan and leave plan mode.

        The store resolves who approved it: the agent has no say in that.
        """

    async def is_active(self) -> bool:
        """Whether the store still considers plan mode active for the thread."""


type PlanStoreFactory = Callable[[str], PlanStore]
