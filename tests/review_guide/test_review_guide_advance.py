from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid7

import pytest

from openswe.review_guide import advance as advance_module
from openswe.review_guide.github import PullRequestHead
from openswe.review_guide.walk import Reader, Walk
from openswe.sandbox_scripts.walkthrough import parse_index
from openswe.walkthrough.diff import DiffIndex
from openswe.walkthrough.lines import LineSet
from openswe.walkthrough.plan import Plan, PlanChunk
from openswe.walkthrough.record import Walkthrough

DIFF = (
    "diff --git a/a.py b/a.py\nnew file mode 100644\n--- /dev/null\n+++ b/a.py\n"
    "@@ -0,0 +1,3 @@\n+def one():\n+def two():\n+def three():\n"
)


def _plan(chunks: int) -> Plan:
    plan = Plan.start("head-1", DiffIndex.model_validate({"files": parse_index(DIFF.splitlines())}))
    titles = ["def one():", "def two():", "def three():"]
    for lineno, title in enumerate(titles[:chunks], start=1):
        plan.add_chunk(
            PlanChunk(title=title, lines=LineSet.of("a.py", "+", [(lineno, lineno)]), code="CODE")
        )
    return plan


@dataclass
class _Session:
    walk: Walk
    thread_id: str = "thread-1"
    slack_channel_id: str = "C1"
    closed: bool = False
    paused_message_ts: str = ""
    seen: LineSet = field(default_factory=LineSet)
    pull_request: SimpleNamespace = field(
        default_factory=lambda: SimpleNamespace(id=uuid7(), owner="o", repo="r", number=1)
    )

    async def save_walk(self, walk: Walk) -> None:
        self.walk = walk

    async def mark_seen(self, head_sha: str, merge_base_sha: str, lines: LineSet) -> None:  # noqa: ARG002
        self.seen = self.seen | lines

    async def seen_at(self, head_sha: str) -> LineSet:  # noqa: ARG002
        return self.seen


def _client(*active_runs: str) -> MagicMock:
    client = MagicMock()
    client.runs.list = AsyncMock(
        side_effect=lambda _thread, status, limit: (  # noqa: ARG005
            [{"run_id": run} for run in active_runs] if status == "running" else []
        )
    )
    return client


def _session(monkeypatch: pytest.MonkeyPatch, plan: Plan, *, complete: bool) -> _Session:
    walk = Walk(head_sha="head-1")
    walk.on_screen = Reader.of(walk, plan, LineSet()).show(plan.chunks[0])
    walk.on_screen.message_ts = "1.0"
    session = _Session(walk=walk)
    stored = Walkthrough(
        pull_request_id=session.pull_request.id,
        head_sha="head-1",
        merge_base_sha="base",
        plan_json=plan.model_dump(mode="json"),
        complete=complete,
    )
    head = PullRequestHead.model_validate({"head": {"sha": "head-1"}, "base": {"sha": "base"}})
    monkeypatch.setattr(
        advance_module.ReviewGuideSession, "for_channel", AsyncMock(return_value=session)
    )
    monkeypatch.setattr(advance_module.Walkthrough, "get", AsyncMock(return_value=stored))
    monkeypatch.setattr(advance_module, "fetch_head", AsyncMock(return_value=head))
    monkeypatch.setattr(advance_module, "retire", AsyncMock())
    monkeypatch.setattr(advance_module, "refresh_progress", AsyncMock())
    monkeypatch.setattr(advance_module, "post_with_buttons", AsyncMock(return_value="2.0"))
    monkeypatch.setattr(advance_module, "dispatch_guide_run", AsyncMock())
    monkeypatch.setattr(advance_module, "langgraph_client", _client)
    return session


async def test_looks_good_approves_and_posts_the_next_planned_chunk_without_a_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(monkeypatch, _plan(2), complete=False)

    await advance_module.advance("C1", "1.0")

    post = advance_module.post_with_buttons
    assert isinstance(post, AsyncMock) and post.await_args is not None
    assert post.await_args.args[1].startswith("*2/2 · def two():*")
    assert session.walk.on_screen is not None and session.walk.on_screen.message_ts == "2.0"
    assert [a.title for a in session.walk.approvals] == ["def one():"]
    assert len(session.seen) == 1
    assert isinstance(advance_module.dispatch_guide_run, AsyncMock)
    advance_module.dispatch_guide_run.assert_not_awaited()


async def test_looks_good_ahead_of_the_planner_approves_then_hands_the_guide_a_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(monkeypatch, _plan(1), complete=False)

    await advance_module.advance("C1", "1.0")

    assert session.walk.on_screen is None
    assert len(session.seen) == 1
    dispatch = advance_module.dispatch_guide_run
    assert isinstance(dispatch, AsyncMock) and dispatch.await_args is not None
    assert dispatch.await_args.kwargs["approve_ts"] == ""


async def test_looks_good_during_a_turn_is_recorded_when_its_own_turn_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(monkeypatch, _plan(2), complete=False)
    monkeypatch.setattr(advance_module, "langgraph_client", lambda: _client("r1"))

    await advance_module.advance("C1", "1.0")

    assert session.walk.on_screen is not None and session.walk.on_screen.message_ts == "1.0"
    assert len(session.seen) == 0
    dispatch = advance_module.dispatch_guide_run
    assert isinstance(dispatch, AsyncMock) and dispatch.await_args is not None
    assert dispatch.await_args.kwargs["approve_ts"] == "1.0"
