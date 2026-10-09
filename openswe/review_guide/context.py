from dataclasses import dataclass
from typing import Self

from openswe.review_guide.sessions import ReviewGuideSession
from openswe.review_guide.walk import Reader, Walk
from openswe.run_config import RunConfig
from openswe.runtime import get_cached_sandbox_backend
from openswe.users import User
from openswe.walkthrough.checkout import CheckoutError, PinnedCheckout
from openswe.walkthrough.plan import LineRef
from openswe.walkthrough.planner import PlannerUnavailableError, PlanWorkspace
from openswe.walkthrough.record import PlanMovedError


class GuideUnavailableError(RuntimeError):
    """The current run is not a review guide session with a checkout."""


async def requester_login() -> str:
    """The GitHub login of the person whose Slack message started this turn."""
    cfg = RunConfig.from_runtime()
    slack_user_id = cfg.slack_thread.triggering_user_id if cfg.slack_thread else ""
    if not slack_user_id:
        raise GuideUnavailableError(
            "only a person's own message can do this; ask them to confirm first"
        )
    user = await User.for_identity("slack", slack_user_id)
    if user is None or not user.github_login:
        raise GuideUnavailableError("the person asking has no GitHub account linked to Open SWE")
    return user.github_login


@dataclass
class GuideContext:
    session: ReviewGuideSession
    workspace: PlanWorkspace

    @classmethod
    async def current(cls) -> Self:
        thread_id = RunConfig.from_runtime().thread_id
        session = await ReviewGuideSession.get(thread_id) if thread_id else None
        if thread_id is None or session is None:
            raise GuideUnavailableError("this thread is not a review guide session")
        try:
            checkout = await PinnedCheckout.locate(
                get_cached_sandbox_backend(thread_id), session.pull_request.repo
            )
            workspace = await PlanWorkspace.open(session.pull_request, checkout)
        except (CheckoutError, PlannerUnavailableError, PlanMovedError) as exc:
            raise GuideUnavailableError(str(exc)) from exc
        return cls(session=session, workspace=workspace)

    @property
    def head_sha(self) -> str:
        return self.workspace.head_sha

    @property
    def unplanned(self) -> list[LineRef]:
        return [LineRef.of(line) for line in self.workspace.plan.unplanned(self.workspace.changes)]

    def walk(self) -> Walk:
        """The reader's walk at the checkout's head; the middleware carries it there each turn."""
        walk = self.session.walk
        return (
            walk
            if walk is not None and walk.head_sha == self.head_sha
            else Walk(head_sha=self.head_sha)
        )

    async def reader(self, walk: Walk) -> Reader:
        return Reader.of(walk, self.workspace.plan, await self.session.seen_lines(), self.unplanned)
