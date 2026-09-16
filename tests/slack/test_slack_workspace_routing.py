"""Slack-started threads route through the shared workspace resolver.

Resolution order is tag, repository, Slack channel, the triggering user's
default, then the instance default -- see ``agent.workspaces.routing``. These
tests cover the tag spelling change and one end-to-end dispatch through
channel routing; ``tests/workspaces/test_routing.py`` covers the resolver
itself.
"""

from typing import Any

import pytest

from agent.dashboard.team_settings import TeamSettingsUpdate, upsert_team_settings
from agent.run_config import Repo
from agent.slack import webhook as slack_webhooks
from agent.slack.request import SlackRequest
from agent.webhooks import common as webhook_common
from agent.workspaces.store import WORKSPACES, WorkspaceCreate, parse_workspace_tag
from tests.conftest import FakeStore
from tests.slack.test_slack_context import _setup_slack_mention_fakes

# Workspaces are rows; team settings are still Store records, so the tests that
# read one keep the store double as well.
_needs_workspace_rows = pytest.mark.usefixtures("registry_db")


def test_workspace_tag_accepts_both_spellings() -> None:
    assert parse_workspace_tag("workspace:oss fix the bug") == ("oss", "fix the bug")
    assert parse_workspace_tag("env:oss fix the bug") == ("oss", "fix the bug")
    assert parse_workspace_tag("fix the bug") == (None, "fix the bug")


@_needs_workspace_rows
async def test_first_mention_in_bound_channel_dispatches_with_its_workspace(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    """No tag and no repo hint: the Slack channel's bound workspace still wins."""
    captured: dict[str, Any] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        return False

    monkeypatch.setattr(webhook_common, "thread_exists", fake_thread_exists)

    await WORKSPACES.create(
        WorkspaceCreate(name="OSS", repos=["acme/oss"], slack_channel_ids=["C0SS"]), "alice"
    )

    request = SlackRequest.model_validate(
        {
            "channel_id": "C0SS",
            "thread_ts": "1700000000.000100",
            "event_ts": "1700000000.000200",
            "user_id": "U123",
            "text": "<@UBOT> hello",
            "bot_user_id": "UBOT",
        }
    )

    await slack_webhooks._process_slack_mention_impl(request, None)

    run_create = captured["run_create"]
    assert isinstance(run_create, dict)
    configurable = run_create["kwargs"]["config"]["configurable"]
    assert configurable["workspace"] == "oss"
    # `environment` is kept alongside `workspace` for one release so a run started
    # before this change lands and one started after read the same key back.
    assert configurable["environment"] == "oss"


@_needs_workspace_rows
async def test_a_bound_channel_outranks_a_defaulted_repository(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    """A repository nobody named does not decide the workspace.

    `get_slack_repo_config` almost always produces one — the team default or
    `SLACK_REPO_*` — so if a defaulted repository counted, a bound channel's
    workspace would never win.
    """
    captured: dict[str, Any] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        return False

    monkeypatch.setattr(webhook_common, "thread_exists", fake_thread_exists)

    await WORKSPACES.create(WorkspaceCreate(name="Default", repos=["acme/internal"]), "alice")
    await WORKSPACES.create(
        WorkspaceCreate(name="OSS", repos=["acme/oss"], slack_channel_ids=["C0SS"]), "alice"
    )
    await upsert_team_settings(TeamSettingsUpdate(default_repo="acme/oss"), workspace="oss")

    request = SlackRequest.model_validate(
        {
            "channel_id": "C0SS",
            "thread_ts": "1700000000.000100",
            "event_ts": "1700000000.000200",
            "user_id": "U123",
            "text": "<@UBOT> hello",
            "bot_user_id": "UBOT",
        }
    )

    # The deployment's default repository belongs to `default`, and nothing in
    # the thread, the message, or the channel description named it.
    await slack_webhooks._process_slack_mention_impl(
        request,
        webhook_common.SlackRepoResolution((Repo(owner="acme", name="internal"),), explicit=False),
    )

    configurable = captured["run_create"]["kwargs"]["config"]["configurable"]
    assert configurable["workspace"] == "oss"
    # ...so the run gets the winning workspace's own default repository.
    assert configurable["repos"] == [{"owner": "acme", "name": "oss"}]


@_needs_workspace_rows
async def test_the_vision_fallback_reads_the_resolved_workspaces_model(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    """An image in a bound channel is checked against that workspace's model.

    `default` here runs a model that takes images and `oss` one that does not,
    so reading the wrong workspace's default would skip the fallback entirely.
    """
    captured: dict[str, Any] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        return False

    async def fake_thread_messages(channel_id: str, thread_ts: str) -> list[dict[str, Any]]:
        return [
            {
                "ts": "1700000000.000200",
                "text": "<@UBOT> look at https://example.com/shot.png",
                "user": "U123",
            }
        ]

    async def no_image_block(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(webhook_common, "thread_exists", fake_thread_exists)
    monkeypatch.setattr(webhook_common, "fetch_slack_thread_messages", fake_thread_messages)
    monkeypatch.setattr(webhook_common, "fetch_image_block", no_image_block)

    await WORKSPACES.create(
        WorkspaceCreate(name="OSS", repos=["acme/oss"], slack_channel_ids=["C0SS"]), "alice"
    )
    await upsert_team_settings(
        TeamSettingsUpdate(
            default_agent_model="anthropic:claude-opus-5",
            default_agent_reasoning_effort="high",
        ),
        workspace="default",
    )
    await upsert_team_settings(
        TeamSettingsUpdate(
            default_agent_model="fireworks:accounts/fireworks/models/kimi-k3",
            default_agent_reasoning_effort="high",
        ),
        workspace="oss",
    )

    request = SlackRequest.model_validate(
        {
            "channel_id": "C0SS",
            "thread_ts": "1700000000.000100",
            "event_ts": "1700000000.000200",
            "user_id": "U123",
            "text": "<@UBOT> look at https://example.com/shot.png",
            "bot_user_id": "UBOT",
        }
    )

    await slack_webhooks._process_slack_mention_impl(request, None)

    configurable = captured["run_create"]["kwargs"]["config"]["configurable"]
    assert configurable["workspace"] == "oss"
    assert (configurable["agent_model_id"], configurable["agent_effort"]) == (
        webhook_common.default_vision_model_pair()
    )


@_needs_workspace_rows
async def test_a_named_repository_still_outranks_a_bound_channel(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    captured: dict[str, Any] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        return False

    monkeypatch.setattr(webhook_common, "thread_exists", fake_thread_exists)

    await WORKSPACES.create(WorkspaceCreate(name="Internal", repos=["acme/internal"]), "alice")
    await WORKSPACES.create(
        WorkspaceCreate(name="OSS", repos=["acme/oss"], slack_channel_ids=["C0SS"]), "alice"
    )

    request = SlackRequest.model_validate(
        {
            "channel_id": "C0SS",
            "thread_ts": "1700000000.000100",
            "event_ts": "1700000000.000200",
            "user_id": "U123",
            "text": "<@UBOT> hello",
            "bot_user_id": "UBOT",
        }
    )

    await slack_webhooks._process_slack_mention_impl(
        request,
        webhook_common.SlackRepoResolution((Repo(owner="acme", name="internal"),), explicit=True),
    )

    configurable = captured["run_create"]["kwargs"]["config"]["configurable"]
    assert configurable["workspace"] == "internal"
    assert configurable["repos"] == [{"owner": "acme", "name": "internal"}]
