"""Slack-started threads route through the shared workspace resolver.

Resolution order is tag, repository, Slack channel, the triggering user's
default, then the instance default -- see ``openswe.workspaces.routing``. These
tests cover the tag spelling change and one end-to-end dispatch through
channel routing; ``tests/workspaces/test_routing.py`` covers the resolver
itself.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe.run_config import Repo
from openswe.slack import webhook as slack_webhooks
from openswe.slack.request import SlackRequest
from openswe.webhooks import common as webhook_common
from openswe.workspaces.store import WORKSPACES, WorkspaceCreate
from tests.conftest import FakeStore
from tests.slack.test_slack_context import _setup_slack_mention_fakes

# Workspaces are rows; workspace settings are still Store records, so the tests that
# read one keep the store double as well.
_needs_workspace_rows = pytest.mark.usefixtures("registry_db")


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
    monkeypatch.setattr(
        webhook_common.ThreadRecord,
        "read",
        AsyncMock(return_value=webhook_common.ThreadRecord(exists=False, metadata={})),
    )

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
            "text": "<@UBOT> /workspace:internal hello" if inherited_workspace else "<@UBOT> hello",
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
