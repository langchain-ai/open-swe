"""Slack-started threads route through the shared workspace resolver.

Resolution order is tag, repository, Slack channel, the triggering user's
default, then the instance default -- see ``agent.workspaces.routing``. These
tests cover the tag spelling change and one end-to-end dispatch through
channel routing; ``tests/workspaces/test_routing.py`` covers the resolver
itself.
"""

from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree

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
from agent.workspaces.store import WORKSPACES, WorkspaceCreate, parse_workspace_tag
from tests.conftest import FakeStore
from tests.slack.test_slack_context import _setup_slack_mention_fakes

# Workspaces are rows; workspace settings are still Store records, so the tests that
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

    `get_slack_repo_config` almost always produces one — the workspace default or
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
    await upsert_workspace_overrides("oss", WorkspaceSettingsUpdate(default_repo="acme/oss"))

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
            default_agent_model="anthropic:claude-opus-5",
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
async def test_an_inherited_default_repository_owned_elsewhere_is_not_used(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    """The instance default repository does not cross into a workspace that does not own it."""
    captured: dict[str, Any] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        return False

    monkeypatch.setattr(webhook_common, "thread_exists", fake_thread_exists)

    await WORKSPACES.create(WorkspaceCreate(name="Core", repos=["acme/internal"]), "alice")
    await WORKSPACES.create(
        WorkspaceCreate(name="OSS", repos=["acme/oss"], slack_channel_ids=["C0SS"]), "alice"
    )
    # Set on the instance, so `oss` inherits it without owning it.
    await upsert_instance_settings(WorkspaceSettingsUpdate(default_repo="acme/internal"))

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
        webhook_common.SlackRepoResolution(Repo(owner="acme", name="internal"), explicit=False),
    )

    configurable = captured["run_create"]["kwargs"]["config"]["configurable"]
    assert configurable["workspace"] == "oss"
    assert configurable.get("repo") is None


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
        webhook_common.SlackRepoResolution(Repo(owner="acme", name="internal"), explicit=True),
    )

    configurable = captured["run_create"]["kwargs"]["config"]["configurable"]
    assert configurable["workspace"] == "internal"
    assert configurable["repo"] == {"owner": "acme", "name": "internal"}


@pytest.mark.parametrize(
    "model_switch",
    ["/model:", "/model:Fast", "/model:performance", "/model:fastest", "/model:fast /model:nope"],
)
async def test_invalid_slack_model_switch_posts_error_without_dispatch_or_persistence(
    monkeypatch: pytest.MonkeyPatch,
    model_switch: str,
) -> None:
    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def post_thread_reply(
        channel_id: str,
        thread_ts: str,
        text: str,
        **kwargs: object,
    ) -> bool:
        captured["error_reply"] = (channel_id, thread_ts, text, kwargs)
        return True

    async def upsert_metadata(*args: object, **kwargs: object) -> bool:
        captured["metadata_persisted"] = (args, kwargs)
        return True

    monkeypatch.setattr(webhook_common, "post_slack_thread_reply", post_thread_reply)
    monkeypatch.setattr(webhook_common, "upsert_agent_thread_metadata", upsert_metadata)

    request = SlackRequest.model_validate(
        {
            "channel_id": "C0SS",
            "thread_ts": "1700000000.000100",
            "event_ts": "1700000000.000200",
            "user_id": "U123",
            "text": f"<@UBOT> fix this {model_switch}",
            "bot_user_id": "UBOT",
        }
    )

    await slack_webhooks._process_slack_mention_impl(request, None)

    assert captured["error_reply"] == (
        "C0SS",
        "1700000000.000100",
        "Invalid model switch. Use `/model:fast` or `/model:perf`.",
        {"agent_thread_id": "mapped-thread"},
    )
    assert "run_create" not in captured
    assert "metadata_persisted" not in captured


async def test_slack_model_switch_uses_workspace_route_and_overrides_stored_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        return True

    async def stored_thread_choice(thread_id: str) -> tuple[str, str]:
        return "openai:gpt-6-astra", "high"

    async def thread_workspace(thread_id: str) -> str:
        return "oss"

    async def workspace_settings(workspace: str | None) -> SimpleNamespace:
        assert workspace == "oss"
        return SimpleNamespace(
            agent_routing_models={
                "fast": ("anthropic:claude-haiku-4-5", "low"),
                "performance": ("anthropic:claude-opus-5", "high"),
            }
        )

    async def upsert_metadata(thread_id: str, **kwargs: object) -> bool:
        captured["explicit_model_choice"] = kwargs.get("explicit_model_choice")
        return True

    monkeypatch.setattr(webhook_common, "thread_exists", fake_thread_exists)
    monkeypatch.setattr(webhook_common, "get_thread_model_choice", stored_thread_choice)
    monkeypatch.setattr(webhook_common, "get_thread_workspace", thread_workspace)
    monkeypatch.setattr(webhook_common, "get_workspace_settings", workspace_settings)
    monkeypatch.setattr(webhook_common, "upsert_agent_thread_metadata", upsert_metadata)

    request = SlackRequest.model_validate(
        {
            "channel_id": "C0SS",
            "thread_ts": "1700000000.000100",
            "event_ts": "1700000000.000200",
            "user_id": "U123",
            "text": "<@UBOT> fix this /model:perf then /model:fast",
            "bot_user_id": "UBOT",
        }
    )

    await slack_webhooks._process_slack_mention_impl(request, None)

    run_create = captured["run_create"]
    assert isinstance(run_create, dict)
    kwargs = run_create["kwargs"]
    assert isinstance(kwargs, dict)
    config = kwargs["config"]
    assert isinstance(config, dict)
    configurable = config["configurable"]
    assert isinstance(configurable, dict)
    assert configurable["workspace"] == "oss"
    assert configurable["agent_model_id"] == "anthropic:claude-haiku-4-5"
    assert configurable["agent_effort"] == "low"
    assert configurable["model_selection"] == "explicit"
    metadata = kwargs["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["slack_model_switch"] == "fast"
    assert metadata["slack_requested_model"] == "anthropic:claude-haiku-4-5"
    assert metadata["slack_requested_effort"] == "low"
    assert "open_swe_model_route" not in metadata
    run_input = kwargs["input"]
    assert isinstance(run_input, dict)
    messages = run_input["messages"]
    assert isinstance(messages, list)
    last_message = messages[-1]
    assert isinstance(last_message, dict)
    content = last_message["content"]
    assert isinstance(content, list)
    content_block = content[0]
    assert isinstance(content_block, dict)
    text = content_block["text"]
    assert isinstance(text, str)
    request_text = ElementTree.fromstring(text)
    assert request_text.findtext("content") == "fix this then"
    assert captured["explicit_model_choice"] == ("anthropic:claude-haiku-4-5", "low")
