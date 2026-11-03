from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from openswe.database import postgres
from openswe.github.pull_requests import PullRequest
from openswe.jobs import queue
from openswe.jobs.kinds import WORKER
from openswe.review_guide import sessions
from openswe.review_guide.github import PullRequestHead
from openswe.review_guide.sessions import ARCHIVE_JOB, ReviewGuideSession
from openswe.review_guide.walk import Walk
from openswe.slack import code_channels
from openswe.slack.http import SlackRequestError
from openswe.source_context import SlackThreadRef
from openswe.users.models import User
from openswe.walkthrough.plan import Plan
from openswe.walkthrough.record import Walkthrough

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.mark.parametrize(
    "changed", ["none", "resumed", "head", "unfinished", "status_failed", "slack_thread"]
)
async def test_completed_review_archives_after_a_day_unless_review_resumes(
    monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    pr = await PullRequest(owner="o", repo="r", number=1).ensure()
    user = User()
    async with postgres.session() as db:
        db.add(user)
        await db.commit()
    await ReviewGuideSession.create(
        thread_id="thread-1",
        pull_request=pr,
        user_id=user.id,
        location=SlackThreadRef(
            channel_id="C1", thread_ts="1.0" if changed == "slack_thread" else "0"
        ),
        workspace_slug=None,
        mode="reviewer",
    )
    session = await ReviewGuideSession.get("thread-1")
    assert session is not None
    walk = Walk(head_sha="head-1")
    plan = Plan(head_sha="head-1")
    async with postgres.session() as db:
        db.add(
            Walkthrough(
                pull_request_id=pr.id,
                head_sha="head-1",
                merge_base_sha="base",
                plan_json=plan.model_dump(mode="json"),
                complete=changed != "unfinished",
            )
        )
        await db.commit()
    head = PullRequestHead.model_validate(
        {"head": {"sha": "head-2" if changed == "head" else "head-1"}, "base": {"sha": "base"}}
    )
    monkeypatch.setattr(sessions, "fetch_head", AsyncMock(return_value=head))
    archive = AsyncMock()
    monkeypatch.setattr(sessions, "archive_code_channel", archive)
    monkeypatch.setattr(
        code_channels,
        "set_session_status_result",
        AsyncMock(
            side_effect=SlackRequestError("is_archived") if changed == "status_failed" else None
        ),
    )

    before = datetime.now(UTC)
    await session.save_walk(walk, finished=True)
    job = await queue.get(f"{ARCHIVE_JOB}:thread-1")
    assert job is not None
    assert before + timedelta(days=1) <= job.due_at <= datetime.now(UTC) + timedelta(days=1)
    assert await WORKER.run_due() == 0
    archive.assert_not_awaited()

    if changed == "resumed":
        await session.save_walk(walk)
        assert await queue.get(f"{ARCHIVE_JOB}:thread-1") is None
    else:
        await queue.run_now(f"{ARCHIVE_JOB}:thread-1")
        assert await WORKER.run_due() == 1
        session = await ReviewGuideSession.get("thread-1")
        assert session is not None
        assert session.closed == (changed in {"none", "status_failed", "slack_thread"})
        assert await queue.get(f"{ARCHIVE_JOB}:thread-1") is None
    if changed in {"none", "status_failed"}:
        archive.assert_awaited_once_with("C1", summary_message_ts="")
    else:
        archive.assert_not_awaited()


async def test_restarting_a_review_in_a_thread_replaces_its_closed_one() -> None:
    first = await PullRequest(owner="o", repo="r", number=1).ensure()
    second = await PullRequest(owner="o", repo="r", number=2).ensure()
    user = User()
    async with postgres.session() as db:
        db.add(user)
        await db.commit()
    here = SlackThreadRef(channel_id="C1", thread_ts="1.0")
    await ReviewGuideSession.create(
        thread_id="thread-1",
        pull_request=first,
        user_id=user.id,
        location=here,
        workspace_slug=None,
        mode="reviewer",
    )
    session = await ReviewGuideSession.get("thread-1")
    assert session is not None
    await session.save_walk(Walk(head_sha="head-1"))
    await session.set_closed(True)

    await ReviewGuideSession.create(
        thread_id="thread-1",
        pull_request=second,
        user_id=user.id,
        location=None,
        workspace_slug=None,
        mode="author",
    )

    session = await ReviewGuideSession.get("thread-1")
    assert session is not None
    assert session.pull_request_id == second.id
    assert (session.closed, session.walk, session.surface, session.mode) == (
        False,
        None,
        "web",
        "author",
    )
