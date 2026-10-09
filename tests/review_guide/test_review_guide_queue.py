from collections import Counter
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from openswe.review_guide import advance as advance_module
from openswe.review_guide.diff import parse, unseen
from openswe.review_guide.github import PullRequestHead
from openswe.review_guide.walk import Group, LineRef, Walk

DIFF = (
    "diff --git a/a.py b/a.py\nnew file mode 100644\n--- /dev/null\n+++ b/a.py\n"
    "@@ -0,0 +1,4 @@\n+import os\n+def one():\n+def two():\n+def three():\n"
)


def _refs(walk_changes: list, *linenos: int) -> list[LineRef]:
    return [LineRef.of(line) for c in walk_changes for line in c.lines if line.lineno in linenos]


def _walk() -> tuple[Walk, list]:
    changes = parse(DIFF)
    walk = Walk.start("head-1", changes)
    walk.groups = [
        Group(title="One", lines=_refs(changes, 2), status="shown", message_ts="1.0"),
        Group(title="Two", lines=_refs(changes, 3), status="queued", message_text="*Two*"),
        Group(title="Three", lines=_refs(changes, 4), status="queued", message_text="*Three*"),
    ]
    return walk, changes


def test_queued_lines_are_held_and_a_pr_update_drops_the_queue() -> None:
    walk, changes = _walk()
    pool = unseen(changes, Counter())

    assert [line.lineno for line in walk.left(pool)] == [1]
    assert "2 prepared chunks have not been shown" in walk.unfinished(pool)

    walk.keep_queue([2])
    assert [g.title for g in walk.queue] == ["Three"]
    assert [line.lineno for line in walk.left(pool)] == [1, 3]

    moved, gone = walk.moved_to("head-2", changes)
    assert moved.queue == []
    assert [g.title for g in gone] == ["Three"]
    assert moved.on_screen() is not None


def test_approving_one_of_two_identical_lines_leaves_the_other() -> None:
    changes = parse(
        "diff --git a/b.py b/b.py\nnew file mode 100644\n--- /dev/null\n+++ b/b.py\n"
        "@@ -0,0 +1,2 @@\n+pass\n+pass\n"
    )
    walk = Walk.start("head-1", changes)
    walk.groups = [Group(title="Second", lines=_refs(changes, 2), status="approved")]

    left = walk.left(walk.unseen(changes, Counter({changes[0].lines[1].key: 1})))

    assert [line.lineno for line in left] == [1]
    assert walk.unfinished(walk.unseen(changes, Counter({changes[0].lines[1].key: 1})))


def _binary(path: str, blob: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\nindex {'0' * 40}..{blob * 40} 100644\n"
        f"Binary files a/{path} and b/{path} differ\n"
    )


def test_a_push_that_changes_a_binary_file_reopens_an_approved_other() -> None:
    walk = Walk.start("head-1", parse(_binary("logo.png", "a")))
    walk.other_status = "approved"

    unchanged, _ = walk.moved_to("head-2", parse(_binary("logo.png", "a")))
    changed, _ = walk.moved_to("head-3", parse(_binary("logo.png", "b")))
    added, _ = walk.moved_to("head-4", parse(_binary("logo.png", "a") + _binary("new.png", "c")))

    assert unchanged.other_status == "approved"
    assert changed.other_status == "open"
    assert added.other_status == "open"


@dataclass
class _Session:
    walk: Walk
    thread_id: str = "thread-1"
    slack_channel_id: str = "C1"
    closed: bool = False
    paused_message_ts: str = ""
    workspace_slug: str | None = None
    seen: Counter[str] = field(default_factory=Counter)

    @property
    def pull_request(self) -> object:
        return type("PR", (), {"owner": "o", "repo": "r", "number": 1})()

    async def save_walk(self, walk: Walk) -> None:
        self.walk = walk

    async def mark_seen(self, lines: Counter[str]) -> None:
        self.seen.update(lines)


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> _Session:
    walk, _ = _walk()
    session = _Session(walk=walk)
    head = PullRequestHead.model_validate({"head": {"sha": "head-1"}, "base": {"sha": "base"}})
    monkeypatch.setattr(
        advance_module.ReviewGuideSession, "for_channel", AsyncMock(return_value=session)
    )
    monkeypatch.setattr(advance_module.ReviewGuideSession, "get", AsyncMock(return_value=session))
    monkeypatch.setattr(advance_module, "fetch_head", AsyncMock(return_value=head))
    monkeypatch.setattr(advance_module, "retire", AsyncMock())
    monkeypatch.setattr(advance_module, "refresh_progress", AsyncMock())
    monkeypatch.setattr(advance_module, "start_prefetch", AsyncMock())
    monkeypatch.setattr(advance_module, "langgraph_client", lambda: AsyncMock())
    monkeypatch.setattr(advance_module, "post_with_buttons", AsyncMock(return_value="2.0"))
    monkeypatch.setattr(advance_module, "dispatch_guide_run", AsyncMock())
    monkeypatch.setattr(advance_module, "_active_runs", AsyncMock(return_value=[]))
    return session


def _statuses(session: _Session) -> list[tuple[str, str]]:
    return [(g.title, g.status) for g in session.walk.groups]


async def test_next_approves_and_shows_the_next_prepared_chunk_without_a_turn(
    session: _Session,
) -> None:
    await advance_module.advance("C1", "1.0")

    post = advance_module.post_with_buttons
    assert isinstance(post, AsyncMock) and post.await_args is not None
    assert post.await_args.args[1] == "*Two*"
    assert _statuses(session) == [("One", "approved"), ("Two", "shown"), ("Three", "queued")]
    assert sum(session.seen.values()) == 1
    assert isinstance(advance_module.dispatch_guide_run, AsyncMock)
    advance_module.dispatch_guide_run.assert_not_awaited()


async def test_next_with_nothing_prepared_approves_then_hands_the_guide_a_turn(
    session: _Session,
) -> None:
    session.walk.keep_queue([])

    await advance_module.advance("C1", "1.0")

    assert _statuses(session) == [("One", "approved")]
    assert sum(session.seen.values()) == 1
    dispatch = advance_module.dispatch_guide_run
    assert isinstance(dispatch, AsyncMock) and dispatch.await_args is not None
    assert dispatch.await_args.kwargs["approve_ts"] == ""


async def test_next_during_a_turn_is_recorded_when_its_own_turn_starts(
    session: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    running = advance_module._Run(run_id="r1")
    monkeypatch.setattr(advance_module, "_active_runs", AsyncMock(return_value=[running]))

    await advance_module.advance("C1", "1.0")

    assert _statuses(session)[0] == ("One", "shown")
    assert sum(session.seen.values()) == 0
    dispatch = advance_module.dispatch_guide_run
    assert isinstance(dispatch, AsyncMock) and dispatch.await_args is not None
    assert dispatch.await_args.kwargs["approve_ts"] == "1.0"
