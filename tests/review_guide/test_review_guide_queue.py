from collections import Counter
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from agent.review_guide import advance as advance_module
from agent.review_guide.diff import parse, unseen
from agent.review_guide.github import PullRequestHead
from agent.review_guide.walk import Group, LineRef, Walk

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
    return session


async def test_looks_good_shows_the_next_prepared_chunk_without_a_turn(
    session: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    post = AsyncMock(return_value="2.0")
    monkeypatch.setattr(advance_module, "post_with_buttons", post)
    monkeypatch.setattr(advance_module, "_active_runs", AsyncMock(return_value=[]))
    fallback = AsyncMock()

    await advance_module.advance("C1", fallback)

    fallback.assert_not_awaited()
    assert post.await_args is not None and post.await_args.args[1] == "*Two*"
    assert [(g.title, g.status) for g in session.walk.groups] == [
        ("One", "approved"),
        ("Two", "shown"),
        ("Three", "queued"),
    ]
    assert sum(session.seen.values()) == 1


async def test_looks_good_waits_for_a_turn_already_running(
    session: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    post = AsyncMock(return_value="2.0")
    monkeypatch.setattr(advance_module, "post_with_buttons", post)
    running = advance_module._Run(run_id="r1")
    monkeypatch.setattr(advance_module, "_active_runs", AsyncMock(return_value=[running]))
    fallback = AsyncMock()

    await advance_module.advance("C1", fallback)

    fallback.assert_awaited_once()
    post.assert_not_awaited()
    assert session.walk.on_screen() is not None and session.walk.on_screen().title == "One"
