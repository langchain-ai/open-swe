"""Slack-started threads route through the shared workspace resolver.

Resolution order is tag, repository, Slack channel, the triggering user's
default, then the instance default -- see ``agent.workspaces.routing``. These
tests cover the tag spelling change and one end-to-end dispatch through
channel routing; ``tests/workspaces/test_routing.py`` covers the resolver
itself.
"""

from typing import Any

import pytest

from agent.dashboard.workspace_settings import (
    WorkspaceSettingsUpdate,
    upsert_instance_settings,
    upsert_workspace_overrides,
)
from agent.run_config import Repo
from agent.slack import webhook as slack_webhooks
from agent.slack.request import SlackRequest
from agent.webhooks import common as webhook_common
from agent.workspaces.store import WORKSPACES, WorkspaceCreate
from tests.conftest import FakeStore
from tests.slack.test_slack_context import _setup_slack_mention_fakes

# Workspaces are rows; workspace settings are still Store records, so the tests that
# read one keep the store double as well.
_needs_workspace_rows = pytest.mark.usefixtures("registry_db")


@_needs_workspace_rows
async def test_the_vision_fallback_reads_the_resolved_workspaces_model(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    """An image in a bound channel is checked against that workspace's model.

    The instance default takes images and `oss` overrides it with one that does
    not, so reading the wrong tier would skip the fallback entirely.
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
    await upsert_instance_settings(
        WorkspaceSettingsUpdate(
            default_agent_model="anthropic:claude-opus-5-5",
            default_agent_reasoning_effort="high",
        )
    )
    await upsert_workspace_overrides(
        "oss",
        WorkspaceSettingsUpdate(
            default_agent_model="fireworks:accounts/fireworks/models/kimi-k3",
            default_agent_reasoning_effort="high",
        ),
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
@pytest.mark.parametrize("inherited_workspace", [None, "oss"])
async def test_a_bound_channel_outranks_a_named_repository(
    monkeypatch: pytest.MonkeyPatch,
    fake_store: FakeStore,
    inherited_workspace: str | None,
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
            "text": "<@UBOT> workspace:internal hello" if inherited_workspace else "<@UBOT> hello",
            "bot_user_id": "UBOT",
        }
    )

    await slack_webhooks._process_slack_mention_impl(
        request,
        webhook_common.SlackRepoResolution(Repo(owner="acme", name="internal"), explicit=True),
        inherited_workspace=inherited_workspace,
    )

    # The message came from `oss`'s channel, and `oss` can work in a repository
    # `internal` prefers, so it stays in `oss` with the named repository.
    configurable = captured["run_create"]["kwargs"]["config"]["configurable"]
    assert configurable["workspace"] == "oss"
    assert configurable["repo"] == {"owner": "acme", "name": "internal"}
