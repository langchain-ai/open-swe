import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, update

from openswe.announcements import delivery
from openswe.announcements.models import Announcement, AnnouncementApproval, AnnouncementDelivery
from openswe.dashboard import oauth, routes
from openswe.database import postgres
from openswe.jobs import queue
from openswe.slack.http import SlackRequestError
from openswe.threads import index
from openswe.tools.send_announcement import send_announcement
from openswe.users.models import User, UserIdentity

pytestmark = pytest.mark.usefixtures("registry_db")


class FakeSlack:
    """DMs by Slack user id, with scripted refusals consumed one per attempt."""

    def __init__(self, refusals: dict[str, list[str]] | None = None) -> None:
        self.refusals = refusals or {}
        self.delivered: list[str] = []

    async def post_dm(self, slack_user_id: str, text: str, **_: object) -> tuple[str, str]:
        if pending := self.refusals.get(slack_user_id):
            raise SlackRequestError(pending.pop(0))
        self.delivered.append(slack_user_id)
        return f"D-{slack_user_id}", "1.0"


@pytest.fixture
def slack(monkeypatch: pytest.MonkeyPatch) -> FakeSlack:
    fake = FakeSlack()
    monkeypatch.setattr(delivery, "post_dm", fake.post_dm)
    monkeypatch.setattr(delivery, "asyncio", SimpleNamespace(sleep=AsyncMock()))
    return fake


async def _person(*identities: tuple[str, str]) -> User:
    user = User(
        identities=[
            UserIdentity(provider=provider, external_id=external_id, login=external_id)
            for provider, external_id in identities
        ]
    )
    async with postgres.session() as session:
        session.add(user)
        await session.flush()
    return user


@pytest.fixture
async def admin(monkeypatch: pytest.MonkeyPatch) -> User:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://test")
    return await _person(("github", "admin"), ("slack", "UADMIN"))


def _client(session: dict[str, str]) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[oauth.require_session] = lambda: session
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    )


@pytest.fixture
async def admin_client(admin: User) -> AsyncIterator[httpx.AsyncClient]:
    async with _client({"sub": "admin", "user_id": str(admin.id)}) as client:
        yield client


async def _started() -> str:
    [job] = await queue.find({"kind": delivery.ANNOUNCEMENT_TASK})
    announcement_id = job.payload["announcement_id"]
    assert isinstance(announcement_id, str)
    return announcement_id


async def test_everyone_on_slack_gets_one_dm_through_rate_limits_and_refusals(
    admin_client: httpx.AsyncClient, slack: FakeSlack
) -> None:
    await _person(("slack", "UALICE"))
    await _person(("slack", "UBOB"))
    await _person(("slack", "UCAROL"))
    await _person(("github", "dave"))
    slack.refusals = {"UBOB": ["account_inactive"], "UCAROL": ["rate_limited: 3"]}

    response = await admin_client.post(
        "/dashboard/api/admin/announcements", json={"text": "We shipped *walkthroughs*"}
    )
    assert response.status_code == 202
    assert response.json() == {"test": False, "recipients": 4}

    result = await delivery.deliver_announcement(await _started())
    assert sorted(slack.delivered) == ["UADMIN", "UALICE", "UCAROL"]
    assert result == {"status": "finished", "sent": 3, "failed": 1}

    await delivery.deliver_announcement(await _started())
    assert len(slack.delivered) == 3

    listed = (await admin_client.get("/dashboard/api/admin/announcements")).json()
    assert listed["recipients"] == 4
    [summary] = listed["items"]
    assert (summary["recipients"], summary["sent"], summary["failed"]) == (4, 3, 1)
    assert summary["finished_at"] is not None


async def test_a_resumed_send_never_redelivers_sent_or_interrupted_recipients(
    slack: FakeSlack,
) -> None:
    sent = await _person(("slack", "USENT"))
    caught = await _person(("slack", "UCAUGHT"))
    await _person(("slack", "UNEXT"))
    announcement = await Announcement.create("hello", None)
    await announcement.record(sent.id, "sent")
    await announcement.record(caught.id, "sending")

    result = await delivery.deliver_announcement(str(announcement.id))

    assert slack.delivered == ["UNEXT"]
    assert result == {"status": "finished", "sent": 1, "failed": 0}
    [summary] = await Announcement.recent()
    assert (summary.sent, summary.failed) == (2, 1)


async def test_only_admins_announce_and_a_test_reaches_only_the_admin(
    admin_client: httpx.AsyncClient, slack: FakeSlack
) -> None:
    intern = await _person(("github", "intern"), ("slack", "UINTERN"))
    async with _client({"sub": "intern", "user_id": str(intern.id)}) as client:
        refused = await client.post("/dashboard/api/admin/announcements", json={"text": "hi"})
    assert refused.status_code == 403

    response = await admin_client.post(
        "/dashboard/api/admin/announcements", json={"text": "hi", "test": True}
    )
    assert response.json() == {"test": True, "recipients": 1}
    assert slack.delivered == ["UADMIN"]
    assert await queue.find({"kind": delivery.ANNOUNCEMENT_TASK}) == []
    assert await Announcement.recent() == []


async def test_the_tool_requires_a_human_decision_and_freezes_the_audience(
    admin: User, admin_client: httpx.AsyncClient, slack: FakeSlack
) -> None:
    thread_id = str(uuid4())
    await index.create(
        thread_id,
        runtime="mda",
        metadata={
            "source": "dashboard",
            "visibility": "private",
            "owner_type": "user",
            "owner_login": "admin",
            "owner_user_id": str(admin.id),
            "admin_thread": True,
        },
    )
    config = {
        "configurable": {
            "thread_id": thread_id,
            "source": "dashboard",
            "admin_thread": True,
            "github_login": "admin",
        }
    }
    with patch("openswe.run_config.get_config", return_value=config):
        result = await send_announcement("  We shipped it  ")
        assert result["status"] == "pending_approval"
        assert result["text"] == "We shipped it"
        assert result["recipients"] == 1
    assert not slack.delivered
    assert await Announcement.recent() == []
    assert await queue.find({"kind": delivery.ANNOUNCEMENT_TASK}) == []
    [approval] = (await admin_client.get("/dashboard/api/admin/announcements")).json()["approvals"]
    assert (approval["text"], approval["recipients"]) == ("We shipped it", 1)
    async with postgres.session() as session:
        pending = await session.get(AnnouncementApproval, UUID(approval["id"]))
        announcement_id = str(pending.announcement_id)
    assert await delivery.deliver_announcement(announcement_id) == {"status": "awaiting_approval"}
    assert slack.delivered == []
    await _person(("slack", "ULATE"))
    path = f"/dashboard/api/admin/announcements/approvals/{approval['id']}/approve"
    body = {"payload_digest": approval["payload_digest"]}
    responses = await asyncio.gather(
        admin_client.post(path, json=body), admin_client.post(path, json=body)
    )
    assert [response.status_code for response in responses] == [200, 200]
    assert len(await queue.find({"kind": delivery.ANNOUNCEMENT_TASK})) == 1
    await delivery.deliver_announcement(await _started())
    assert slack.delivered == ["UADMIN"]
    [summary] = await Announcement.recent()
    assert (summary.text, summary.recipients, summary.sent) == ("We shipped it", 1, 1)
    assert summary.author == admin.display_name


async def test_slack_identity_changes_never_redirect_a_frozen_delivery(
    admin: User, admin_client: httpx.AsyncClient, slack: FakeSlack
) -> None:
    thread_id = str(uuid4())
    await index.create(
        thread_id,
        runtime="mda",
        metadata={
            "source": "dashboard",
            "visibility": "private",
            "owner_type": "user",
            "owner_user_id": str(admin.id),
            "admin_thread": True,
        },
    )
    approval = await AnnouncementApproval.prepare(thread_id, admin.id, "hello")
    async with postgres.session() as session:
        await session.execute(delete(UserIdentity).where(UserIdentity.provider == "slack"))
    async with postgres.session() as session:
        identity = UserIdentity(provider="slack", external_id="UNEW", login="new")
        identity.user_id = admin.id
        session.add(identity)
    path = f"/dashboard/api/admin/announcements/approvals/{approval.id}/approve"
    assert (
        await admin_client.post(path, json={"payload_digest": approval.payload_digest})
    ).status_code == 200
    assert await delivery.deliver_announcement(await _started()) == {
        "status": "finished",
        "sent": 0,
        "failed": 1,
    }
    assert slack.delivered == []
    [summary] = await Announcement.recent()
    assert (summary.sent, summary.failed) == (0, 1)


async def test_only_the_requesting_admin_can_decide_and_superseded_or_rejected_cannot_send(
    admin: User, admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_id = str(uuid4())
    await index.create(
        thread_id,
        runtime="mda",
        metadata={
            "source": "dashboard",
            "visibility": "private",
            "owner_type": "user",
            "owner_login": "admin",
            "owner_user_id": str(admin.id),
            "admin_thread": True,
        },
    )
    original = await AnnouncementApproval.prepare(thread_id, admin.id, "original")
    replacement = await AnnouncementApproval.prepare(thread_id, admin.id, "replacement")
    base = "/dashboard/api/admin/announcements/approvals"
    assert (
        await admin_client.post(
            f"{base}/{original.id}/approve", json={"payload_digest": original.payload_digest}
        )
    ).status_code == 409
    other = await _person(("github", "other"))
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin,other")
    async with _client({"sub": "other", "user_id": str(other.id)}) as client:
        assert (
            await client.post(
                f"{base}/{replacement.id}/approve",
                json={"payload_digest": replacement.payload_digest},
            )
        ).status_code == 404
    body = {"payload_digest": replacement.payload_digest}
    assert (await admin_client.post(f"{base}/{replacement.id}/reject", json=body)).json() == {
        "status": "rejected"
    }
    assert (await admin_client.post(f"{base}/{replacement.id}/approve", json=body)).json() == {
        "status": "rejected"
    }
    assert await queue.find({"kind": delivery.ANNOUNCEMENT_TASK}) == []
    assert await Announcement.recent() == []


async def test_lost_thread_eligibility_or_admin_status_blocks_approval(
    admin: User, admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_id = str(uuid4())
    await index.create(
        thread_id,
        runtime="mda",
        metadata={
            "source": "dashboard",
            "visibility": "private",
            "owner_type": "user",
            "owner_login": "admin",
            "owner_user_id": str(admin.id),
            "admin_thread": True,
        },
    )
    approval = await AnnouncementApproval.prepare(thread_id, admin.id, "hello")
    path = f"/dashboard/api/admin/announcements/approvals/{approval.id}/approve"
    body = {"payload_digest": approval.payload_digest}
    await index.update(thread_id, metadata={"visibility": "public"})
    assert (await admin_client.post(path, json=body)).status_code == 404
    await index.update(thread_id, metadata={"visibility": "private"})
    monkeypatch.setenv("CONFIGURED_ADMINS", "other")
    assert (await admin_client.post(path, json=body)).status_code == 403
    assert await queue.find({"kind": delivery.ANNOUNCEMENT_TASK}) == []


async def test_expired_changed_audience_and_failed_queue_cannot_send(
    admin: User, admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_id = str(uuid4())
    await index.create(
        thread_id,
        runtime="mda",
        metadata={
            "source": "dashboard",
            "visibility": "private",
            "owner_type": "user",
            "owner_user_id": str(admin.id),
            "admin_thread": True,
        },
    )
    base = "/dashboard/api/admin/announcements/approvals"
    expired = await AnnouncementApproval.prepare(thread_id, admin.id, "old")
    async with postgres.session() as session:
        await session.execute(
            update(AnnouncementApproval)
            .where(AnnouncementApproval.id == expired.id)
            .values(created_at=datetime.now(UTC) - timedelta(hours=25))
        )
    assert (
        await admin_client.post(
            f"{base}/{expired.id}/approve", json={"payload_digest": expired.payload_digest}
        )
    ).status_code == 409
    stale = await AnnouncementApproval.prepare(thread_id, admin.id, "stale")
    async with postgres.session() as session:
        approval = await session.get(AnnouncementApproval, stale.id)
        await session.execute(
            delete(AnnouncementDelivery).where(
                AnnouncementDelivery.announcement_id == approval.announcement_id
            )
        )
    assert (
        await admin_client.post(
            f"{base}/{stale.id}/approve", json={"payload_digest": stale.payload_digest}
        )
    ).status_code == 409
    changed_text = await AnnouncementApproval.prepare(thread_id, admin.id, "before")
    async with postgres.session() as session:
        approval = await session.get(AnnouncementApproval, changed_text.id)
        await session.execute(
            update(Announcement)
            .where(Announcement.id == approval.announcement_id)
            .values(text="after")
        )
    assert (
        await admin_client.post(
            f"{base}/{changed_text.id}/approve",
            json={"payload_digest": changed_text.payload_digest},
        )
    ).status_code == 409
    changed_user = await AnnouncementApproval.prepare(thread_id, admin.id, "same audience size")
    other = await _person(("slack", "UNEW"))
    async with postgres.session() as session:
        approval = await session.get(AnnouncementApproval, changed_user.id)
        await session.execute(
            update(AnnouncementDelivery)
            .where(AnnouncementDelivery.announcement_id == approval.announcement_id)
            .values(user_id=other.id, slack_user_id="UNEW")
        )
    assert (
        await admin_client.post(
            f"{base}/{changed_user.id}/approve",
            json={"payload_digest": changed_user.payload_digest},
        )
    ).status_code == 409
    changed_identity = await AnnouncementApproval.prepare(thread_id, admin.id, "same count")
    async with postgres.session() as session:
        approval = await session.get(AnnouncementApproval, changed_identity.id)
        await session.execute(
            update(AnnouncementDelivery)
            .where(
                AnnouncementDelivery.announcement_id == approval.announcement_id,
                AnnouncementDelivery.user_id == admin.id,
            )
            .values(slack_user_id="UDIFFERENT")
        )
    assert (
        await admin_client.post(
            f"{base}/{changed_identity.id}/approve",
            json={"payload_digest": changed_identity.payload_digest},
        )
    ).status_code == 409
    current = await AnnouncementApproval.prepare(thread_id, admin.id, "current")
    assert (
        await admin_client.post(
            f"{base}/{current.id}/approve", json={"payload_digest": "outdated-view"}
        )
    ).status_code == 409
    schedule = queue.schedule

    async def fail_after_schedule(*args: object, **kwargs: object) -> object:
        await schedule(*args, **kwargs)
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(queue, "schedule", fail_after_schedule)
    with pytest.raises(RuntimeError, match="queue unavailable"):
        await admin_client.post(
            f"{base}/{current.id}/approve", json={"payload_digest": current.payload_digest}
        )
    assert await queue.find({"kind": delivery.ANNOUNCEMENT_TASK}) == []
    async with postgres.session() as session:
        assert (await session.get(AnnouncementApproval, current.id)).status == "pending"
    monkeypatch.setattr(queue, "schedule", schedule)
    assert (
        await admin_client.post(
            f"{base}/{current.id}/approve", json={"payload_digest": current.payload_digest}
        )
    ).json() == {"status": "approved"}
    assert len(await queue.find({"kind": delivery.ANNOUNCEMENT_TASK})) == 1


async def test_tool_rejects_nonadmins_ordinary_threads_and_mcp(
    admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_id = str(uuid4())
    metadata = {
        "source": "dashboard",
        "visibility": "private",
        "owner_type": "user",
        "owner_user_id": str(admin.id),
        "admin_thread": True,
    }
    await index.create(thread_id, runtime="mda", metadata=metadata)
    config = {
        "configurable": {
            "thread_id": thread_id,
            "source": "dashboard",
            "admin_thread": True,
            "github_login": "admin",
        }
    }
    with patch("openswe.run_config.get_config", return_value=config):
        monkeypatch.setenv("CONFIGURED_ADMINS", "other")
        assert (await send_announcement("no"))["ok"] is False
        monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
        await index.update(thread_id, metadata={"visibility": "public"})
        assert (await send_announcement("no"))["ok"] is False
        await index.update(thread_id, metadata={"visibility": "private"})
        config["configurable"]["admin_thread"] = False
        assert (await send_announcement("no"))["ok"] is False
        config["configurable"]["admin_thread"] = True
        config["configurable"]["source"] = "mcp"
        assert (await send_announcement("no"))["ok"] is False
    assert await AnnouncementApproval.pending(admin.id) == []
    assert await queue.find({"kind": delivery.ANNOUNCEMENT_TASK}) == []


async def test_slack_dm_admin_can_prepare_and_decide_in_dashboard(
    admin: User, admin_client: httpx.AsyncClient
) -> None:
    thread_id = str(uuid4())
    slack_thread = {"channel_id": "D123", "thread_ts": "1.0", "channel_context": {"is_im": True}}
    metadata = {
        "source": "slack",
        "visibility": "private",
        "owner_type": "user",
        "owner_user_id": str(admin.id),
        "source_context": {"slack_thread": slack_thread},
    }
    await index.create(thread_id, runtime="mda", metadata=metadata)
    config = {
        "configurable": {
            "thread_id": thread_id,
            "source": "slack",
            "slack_thread": slack_thread,
            "admin_thread": True,
            "github_login": "admin",
        }
    }
    with patch("openswe.run_config.get_config", return_value=config):
        result = await send_announcement("hello")
    assert result["status"] == "pending_approval"
    assert result["approval_url"] == "http://test/admin/users#announcement-approvals"
    [approval] = (await admin_client.get("/dashboard/api/admin/announcements")).json()["approvals"]
    response = await admin_client.post(
        f"/dashboard/api/admin/announcements/approvals/{approval['id']}/approve",
        json={"payload_digest": approval["payload_digest"]},
    )
    assert response.json() == {"status": "approved"}
