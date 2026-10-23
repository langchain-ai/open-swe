from unittest.mock import AsyncMock, patch

import pytest

from openswe.jobs import queue
from openswe.threads import index
from openswe.workspaces import firehose
from openswe.workspaces.store import WORKSPACES, WorkspaceCreate, WorkspaceUpdate

pytestmark = pytest.mark.usefixtures("registry_db")
THREAD = "00000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def dashboard_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://open-swe.test")


@pytest.mark.parametrize("private", [False, True])
async def test_firehose_links_and_redaction_are_delivered_once(private: bool) -> None:
    await WORKSPACES.create(WorkspaceCreate(name="Core", firehose_channel_id="CFIREHOSE"), "admin")
    await index.create(
        THREAD,
        runtime="mda",
        metadata={
            "workspace": "core",
            "title": "Secret <@U123>",
            "visibility": "private" if private else "public",
            "source_context": {"slack_thread": {"channel_id": "CORIGIN", "thread_ts": "123.456"}},
        },
    )
    jobs = await queue.claim_due(10)
    [job] = [job for job in jobs if job.kind == firehose.JOB_KIND]
    post = AsyncMock(return_value="456.789")
    with patch.object(firehose, "post_slack_top_level_message_with_ts", post):
        await firehose.deliver(job)
        await firehose.deliver(job)
    post.assert_awaited_once()
    channel, message = post.call_args.args
    assert channel == "CFIREHOSE"
    assert f"/agents/{THREAD}|Web>" in message
    if private:
        assert message.startswith("Private thread\n")
        assert "Secret" not in message and "CORIGIN" not in message
    else:
        assert "Secret &lt;@U123&gt;" in message
        assert "https://slack.com/archives/CORIGIN/p123456|Slack>" in message
    await job.finish()
    await index.update(THREAD, {"title": "A later title"})
    assert not await queue.claim_due(10)


async def test_stub_workspace_assignment_and_failed_delivery_can_retry() -> None:
    await WORKSPACES.create(WorkspaceCreate(name="Core", firehose_channel_id="CFIREHOSE"), "admin")
    await index.create(THREAD, runtime="mda", metadata={"title": "Stub"})
    assert not await queue.claim_due(10)
    await index.update(THREAD, {"workspace": "core"})
    [job] = await queue.claim_due(10)
    with patch.object(firehose, "post_slack_top_level_message_with_ts", AsyncMock(return_value="")):
        with pytest.raises(RuntimeError, match="not delivered"):
            await firehose.deliver(job)
    post = AsyncMock(return_value="456.789")
    with patch.object(firehose, "post_slack_top_level_message_with_ts", post):
        await firehose.deliver(job)
    post.assert_awaited_once()


async def test_firehose_can_be_cleared_without_changing_channel_bindings() -> None:
    await WORKSPACES.create(WorkspaceCreate(name="Core", firehose_channel_id="CFIREHOSE"), "admin")
    saved = await WORKSPACES.apply_update("core", WorkspaceUpdate(prompt="Keep firehose"))
    assert saved.firehose_channel_id == "CFIREHOSE"
    saved = await WORKSPACES.apply_update("core", WorkspaceUpdate(firehose_channel_id=None))
    assert saved.firehose_channel_id is None and saved.slack_channel_ids == []
    await index.create(THREAD, runtime="mda", metadata={"workspace": "core"})
    assert not await queue.claim_due(10)
