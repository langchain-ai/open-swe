from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from openswe.announcements import delivery
from openswe.announcements.models import Announcement
from openswe.dashboard import oauth, routes
from openswe.database import postgres
from openswe.jobs import queue
from openswe.slack.http import SlackRequestError
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


async def test_a_resumed_send_never_redelivers_someone_caught_mid_send(slack: FakeSlack) -> None:
    caught = await _person(("slack", "UCAUGHT"))
    await _person(("slack", "UNEXT"))
    announcement = await Announcement.create("hello", None)
    await announcement.record(caught.id, "sending")

    result = await delivery.deliver_announcement(str(announcement.id))

    assert slack.delivered == ["UNEXT"]
    assert result == {"status": "finished", "sent": 1, "failed": 0}
    [summary] = await Announcement.recent()
    assert summary.failed == 1


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


async def test_the_tool_previews_until_the_admin_confirms(
    admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    threads = SimpleNamespace(
        get=AsyncMock(return_value={"metadata": {"visibility": "private", "owner_type": "user"}})
    )
    monkeypatch.setattr(
        "openswe.utils.thread_reads.langgraph_sdk.get_client",
        lambda: SimpleNamespace(threads=threads),
    )
    config = {
        "configurable": {
            "thread_id": "t-1",
            "source": "dashboard",
            "admin_thread": True,
            "github_login": "admin",
        }
    }
    with patch("openswe.run_config.get_config", return_value=config):
        preview = await send_announcement("We shipped it")
        assert preview == {"status": "preview", "recipients": 1, "text": "We shipped it"}
        assert await Announcement.recent() == []

        sent = await send_announcement("We shipped it", confirm=True)
    assert sent == {"status": "sending", "recipients": 1}
    [summary] = await Announcement.recent()
    assert summary.author == admin.display_name
    assert len(await queue.find({"kind": delivery.ANNOUNCEMENT_TASK})) == 1
