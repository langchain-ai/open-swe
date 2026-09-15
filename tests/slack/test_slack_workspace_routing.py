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

# Workspaces are rows; team settings are still Store records, so the test that
# reads one keeps the store double as well.
pytestmark = pytest.mark.usefixtures("registry_db")


def test_workspace_tag_accepts_both_spellings() -> None:
    assert parse_workspace_tag("workspace:oss fix the bug") == ("oss", "fix the bug")
    assert parse_workspace_tag("env:oss fix the bug") == ("oss", "fix the bug")
    assert parse_workspace_tag("fix the bug") == (None, "fix the bug")


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
        webhook_common.SlackRepoResolution(Repo(owner="acme", name="internal"), explicit=False),
    )

    configurable = captured["run_create"]["kwargs"]["config"]["configurable"]
    assert configurable["workspace"] == "oss"
    # ...so the run gets the winning workspace's own default repository.
    assert configurable["repo"] == {"owner": "acme", "name": "oss"}


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
        webhook_common.SlackRepoResolution(Repo(owner="acme", name="internal"), explicit=True),
    )

    configurable = captured["run_create"]["kwargs"]["config"]["configurable"]
    assert configurable["workspace"] == "internal"
    assert configurable["repo"] == {"owner": "acme", "name": "internal"}
