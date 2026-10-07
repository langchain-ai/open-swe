import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock
from xml.etree import ElementTree

import pytest

from agent.github.token_scope import GITHUB_TOKEN_REPOSITORIES_KEY
from agent.run_config import Repo
from agent.slack import client as slack_utils
from agent.slack import webhook as slack_webhooks
from agent.slack.channels import SlackChannel
from agent.slack.client import (
    format_slack_messages_for_prompt,
)
from agent.slack.payloads import SlackChannelContext, SlackChannelPayload
from agent.slack.request import SlackRequest
from agent.source_context import SourceContext
from agent.utils.run_usage import RunUsageSummary
from agent.webhooks import common as webhook_common
from agent.workspaces.store import WORKSPACES, WorkspaceCreate


async def _fake_trace_url(thread_id: str, **kwargs: object) -> str:
    return "https://smith/x"


class _FakeNotFoundError(Exception):
    status_code = 404


class _FakeThreadsClient:
    def __init__(self, thread: dict | None = None, raise_not_found: bool = False) -> None:
        self.thread = thread
        self.raise_not_found = raise_not_found
        self.requested_thread_id: str | None = None

    async def get(self, thread_id: str) -> dict:
        self.requested_thread_id = thread_id
        if self.raise_not_found:
            raise _FakeNotFoundError("not found")
        if self.thread is None:
            raise AssertionError("thread must be provided when raise_not_found is False")
        return self.thread

    async def update(self, *, thread_id: str, metadata: dict) -> None:
        cast(dict, self.thread)["metadata"].update(metadata)


class _FakeClient:
    def __init__(self, threads_client: _FakeThreadsClient) -> None:
        self.threads = threads_client


def test_channel_operations_fail_closed_without_external_sharing_status() -> None:
    context = SlackChannelPayload.of(None).to_context("C123")

    assert context.is_ext_shared is None
    assert not context.allows_operations
    assert SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False).allows_operations
    assert not SlackChannelContext(
        is_ext_shared=False, is_pending_ext_shared=True
    ).allows_operations
    assert SlackChannelContext(is_im=True).allows_operations


def test_source_context_does_not_reuse_permalink_for_different_slack_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_slack_permalink(_channel_id: str, _thread_ts: str) -> str | None:
        return None

    monkeypatch.setattr(webhook_common, "get_slack_permalink", fake_get_slack_permalink)

    enriched = asyncio.run(
        webhook_common._source_context_with_slack_permalink(
            SourceContext.parse(
                {"slack_thread": {"channel_id": "C999", "thread_ts": "1700000000.000999"}}
            ),
            {
                "source_context": {
                    "slack_thread": {
                        "channel_id": "C123",
                        "thread_ts": "1700000000.000100",
                        "permalink": "https://slack.example/existing",
                    }
                }
            },
        )
    )

    assert "permalink" not in enriched.dump()["slack_thread"]


def test_upsert_stamps_visibility_and_owner_only_on_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _FakeThreadsClient(raise_not_found=True)
    created: dict = {}

    async def create(*, thread_id: str, if_exists: str, metadata: dict) -> None:
        created.update(metadata)
        threads.thread = {"metadata": dict(metadata)}
        threads.raise_not_found = False

    threads.create = create  # type: ignore[attr-defined]
    monkeypatch.setattr(webhook_common, "get_client", lambda url: _FakeClient(threads))

    assert asyncio.run(
        webhook_common.upsert_agent_thread_metadata(
            "thread-id", source="slack", visibility="private", owner_login="Alice", title="Thread"
        )
    )
    assert created["visibility"] == "private"
    assert created["owner_login"] == "Alice"

    asyncio.run(
        webhook_common.upsert_agent_thread_metadata(
            "thread-id", source="slack", visibility="public", owner_login="bob", title="Thread"
        )
    )
    metadata = cast(dict, threads.thread)["metadata"]
    assert metadata["visibility"] == "private"
    assert metadata["owner_login"] == "Alice"


def test_upsert_records_a_token_scope_only_on_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _FakeThreadsClient(raise_not_found=True)

    async def create(*, thread_id: str, if_exists: str, metadata: dict) -> None:
        threads.thread = {"metadata": dict(metadata)}
        threads.raise_not_found = False

    threads.create = create  # type: ignore[attr-defined]
    monkeypatch.setattr(webhook_common, "get_client", lambda url: _FakeClient(threads))

    assert asyncio.run(
        webhook_common.upsert_agent_thread_metadata(
            "thread-id", source="github", title="Issue", token_repositories=["acme/oss"]
        )
    )
    asyncio.run(
        webhook_common.upsert_agent_thread_metadata(
            "thread-id", source="github", title="Issue", token_repositories=None
        )
    )

    metadata = cast(dict, threads.thread)["metadata"]
    assert metadata[GITHUB_TOKEN_REPOSITORIES_KEY] == ["acme/oss"]


def test_format_slack_messages_for_prompt_caps_forwarded_attachment_depth() -> None:
    root: dict[str, object] = {
        "is_share": True,
        "text": "level 0",
    }
    current = root
    for depth in range(1, slack_utils.SLACK_FORWARDED_ATTACHMENT_MAX_DEPTH + 2):
        nested: dict[str, object] = {
            "is_share": True,
            "text": f"level {depth}",
        }
        current["attachments"] = [nested]
        current = nested

    formatted = format_slack_messages_for_prompt(
        [{"ts": "1.0", "text": "context", "user": "U123", "attachments": [root]}],
        {"U123": "alice"},
    )

    assert f"level {slack_utils.SLACK_FORWARDED_ATTACHMENT_MAX_DEPTH}" in formatted
    assert f"level {slack_utils.SLACK_FORWARDED_ATTACHMENT_MAX_DEPTH + 1}" not in formatted


def test_format_slack_messages_for_prompt_renders_app_card_attachments() -> None:
    alert = {
        "ts": "1.0",
        "text": "",
        "bot_id": "B1",
        "attachments": [
            {
                "title": "Triggered: Webhook delivery failures",
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "6 deliveries failed"}},
                    {"type": "actions", "elements": [{"type": "button", "text": {"text": "Mute"}}]},
                ],
            }
        ],
    }

    formatted = format_slack_messages_for_prompt([alert])

    assert "Triggered: Webhook delivery failures\n6 deliveries failed" in formatted
    assert "Mute" not in formatted


def _setup_slack_mention_fakes(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, object]
) -> None:
    async def fake_get_slack_user_info(user_id: str) -> dict:
        return {
            "profile": {
                "email": "mason@example.com",
                "display_name": "Mason",
            },
            "tz": "America/New_York",
        }

    async def fake_fetch_slack_thread_messages(channel_id: str, thread_ts: str) -> list[dict]:
        captured["fetch_thread"] = {"channel_id": channel_id, "thread_ts": thread_ts}
        return [
            {"ts": "1700000000.000100", "text": "<@UBOT> first request", "user": "U123"},
            {"ts": "1700000000.000150", "text": "context", "user": "U456"},
            {
                "ts": "1700000000.000200",
                "text": "<@UBOT> continue on the branch",
                "user": "U123",
            },
        ]

    async def fake_get_slack_user_names(user_ids: list[str]) -> dict[str, str]:
        captured["user_ids"] = user_ids
        return {"U123": "Mason", "U456": "Teammate"}

    async def fake_resolve_slack_links_in_context(
        context_messages: list[dict], user_names_by_id: dict[str, str]
    ) -> tuple[str, list[str]]:
        captured["context_messages"] = context_messages
        captured["user_names_by_id"] = user_names_by_id
        return "", []

    async def fake_post_slack_trace_reply(channel_id: str, thread_ts: str, thread_id: str) -> None:
        captured["trace_reply"] = {
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "thread_id": thread_id,
        }

    class _FakeRunsClient:
        async def create(self, thread_id: str, graph: str, **kwargs) -> dict[str, str]:
            captured["run_create"] = {
                "thread_id": thread_id,
                "graph": graph,
                "kwargs": kwargs,
            }
            return {"run_id": "run-123"}

    class _FakeThreadsClientForProcess:
        async def get(self, thread_id: str) -> dict:
            return {"metadata": {"visibility": "public"}}

        async def get_state(self, thread_id: str) -> dict:
            return {"values": captured.get("thread_state_values", {"messages": []})}

        async def update(self, *, thread_id: str, metadata: dict) -> None:
            captured["metadata_update"] = {"thread_id": thread_id, "metadata": metadata}

    class _FakeLangGraphClientForProcess:
        runs = _FakeRunsClient()
        threads = _FakeThreadsClientForProcess()

    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(slack_webhooks, "get_langsmith_trace_url", _fake_trace_url)
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USERNAME", "open-swe")
    monkeypatch.setattr(webhook_common, "get_slack_user_info", fake_get_slack_user_info)
    monkeypatch.setattr(
        webhook_common, "fetch_slack_thread_messages", fake_fetch_slack_thread_messages
    )
    monkeypatch.setattr(webhook_common, "get_slack_user_names", fake_get_slack_user_names)
    monkeypatch.setattr(
        webhook_common, "resolve_slack_links_in_context", fake_resolve_slack_links_in_context
    )

    async def fake_login_for_slack_id(slack_user_id):
        return "mason-gh"

    async def fake_login_for_email(email):
        return None

    async def fake_refresh_cache() -> list:
        return []

    async def fake_get_valid_access_token(login):
        return "user-token"

    async def fake_post_prompt(*args, **kwargs) -> None:
        captured["prompt"] = {"args": args, "kwargs": kwargs}

    async def fake_resolve_slack_thread_id(client, channel_id, thread_ts):
        return "mapped-thread"

    monkeypatch.setattr(webhook_common, "post_slack_trace_reply", fake_post_slack_trace_reply)
    monkeypatch.setattr(webhook_common, "resolve_slack_thread_id", fake_resolve_slack_thread_id)
    client = _FakeLangGraphClientForProcess()
    monkeypatch.setattr(webhook_common, "get_client", lambda url: client)
    monkeypatch.setattr(slack_webhooks, "get_langgraph_client", lambda: client)
    monkeypatch.setattr(webhook_common.User, "login_for_slack", fake_login_for_slack_id)
    monkeypatch.setattr(webhook_common.User, "login_for_email", fake_login_for_email)
    monkeypatch.setattr(webhook_common, "get_valid_access_token", fake_get_valid_access_token)
    monkeypatch.setattr(webhook_common, "post_account_link_prompt", fake_post_prompt)


@pytest.mark.asyncio
async def test_web_question_keeps_context_without_slack_delivery(monkeypatch, fake_store):
    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)
    persisted = AsyncMock(return_value=True)
    mapping = AsyncMock()
    status = AsyncMock()
    monkeypatch.setattr(webhook_common, "upsert_agent_thread_metadata", persisted)
    monkeypatch.setattr(webhook_common, "store_slack_run_mapping", mapping)
    monkeypatch.setattr(slack_webhooks, "show_slack_thinking_status", status)
    request = SlackRequest(
        channel_id="C123",
        thread_ts="1700000000.000100",
        event_ts="1700000000.000200",
        thread_id="web-question",
        user_id="U123",
        text="quick question",
        bot_user_id="UBOT",
        context_thread_ts="1700000000.000100",
    )
    assert await slack_webhooks.process_slack_web_mention(request, None)
    run = captured["run_create"]
    assert isinstance(run, dict)
    config = run["kwargs"]["config"]["configurable"]
    assert config["source"] == "web"
    assert "slack_thread" not in config
    assert "slack_breakout" not in config
    assert persisted.await_args.kwargs["visibility"] == "private"
    assert persisted.await_args.kwargs["source_context"] is None
    messages = str(run["kwargs"]["input"])
    assert "first request" in messages and "context" in messages
    assert 'surface="web"' in messages
    mapping.assert_not_awaited()
    status.assert_not_awaited()


@pytest.fixture
async def slack_file_mention(monkeypatch, fake_store, registry_db):
    from agent.sandboxes import lifecycle, state

    captured: dict[str, Any] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    class Threads:
        metadata: dict[str, Any] | None = None

        async def get(self, thread_id: str) -> dict[str, Any]:
            if self.metadata is None:
                raise _FakeNotFoundError()
            return {"thread_id": thread_id, "metadata": dict(self.metadata)}

        async def create(self, *, thread_id: str, metadata: dict, **kwargs) -> None:
            if self.metadata is None:
                self.metadata = dict(metadata)

        async def update(self, *, thread_id: str, metadata: dict) -> None:
            if self.metadata is None:
                raise _FakeNotFoundError()
            self.metadata.update(metadata)

    class Sandbox:
        id = "sandbox-for-slack-files"

        def __init__(self, workspace_slug: str | None) -> None:
            self.workspace_slug = workspace_slug
            self.files: dict[str, bytes] = {}

        async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[dict]:
            self.files.update(files)
            return [{"error": None} for _ in files]

    provisioned: list[Sandbox] = []

    async def provision(*, workspace_slug: str | None = None, **kwargs: object) -> Sandbox:
        sandbox = Sandbox(workspace_slug)
        provisioned.append(sandbox)
        return sandbox

    client = slack_webhooks.get_langgraph_client()
    client.threads = Threads()
    client.store = fake_store
    backends = {}
    monkeypatch.setattr(lifecycle, "client", client)
    monkeypatch.setattr(state, "get_client", lambda: client)
    monkeypatch.setattr(lifecycle, "SANDBOX_BACKENDS", backends)
    monkeypatch.setattr(state, "SANDBOX_BACKENDS", backends)
    monkeypatch.setattr(lifecycle, "_create_sandbox_with_proxy", provision)
    monkeypatch.setattr(lifecycle, "get_recorded_proxy_base_config", lambda _: None)
    monkeypatch.setattr(webhook_common, "get_slack_permalink", AsyncMock(return_value=None))
    monkeypatch.setattr(slack_utils, "download_slack_file", AsyncMock(return_value=b"zip"))
    await WORKSPACES.create(WorkspaceCreate(name="Staging", repos=["acme/staging"]), "alice")
    request = SlackRequest(
        channel_id="C123",
        thread_ts="1700000000.000100",
        event_ts="1700000000.000100",
        thread_id="mapped-thread",
        user_id="U123",
        text="<@UBOT> analyze this zip",
        bot_user_id="UBOT",
    )
    monkeypatch.setattr(
        webhook_common,
        "fetch_slack_thread_messages",
        AsyncMock(
            return_value=[
                {
                    "ts": request.event_ts,
                    "text": request.text,
                    "user": request.user_id,
                    "files": [
                        {
                            "name": "bundle.zip",
                            "mimetype": "application/zip",
                            "url_private": "https://files.slack.com/bundle.zip",
                        }
                    ],
                }
            ]
        ),
    )
    return request, client.threads, provisioned, captured


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize(
    ("existing", "tag", "environment"),
    # A first mention with no tag, repo, or channel binding still resolves
    # through the shared workspace resolver, which falls all the way back to
    # the instance default rather than leaving the sandbox unbound.
    [(False, "", "default"), (False, "env:staging", "staging"), (True, "", "staging")],
)
async def test_slack_files_reach_bound_sandbox_with_thread_environment(
    slack_file_mention, private, existing, tag, environment
):
    request, threads, provisioned, captured = slack_file_mention
    if private:
        request = request.model_copy(update={"channel_context": SlackChannelContext(is_im=True)})
    if existing:
        threads.metadata = {
            "visibility": "private" if private else "public",
            "owner_login": "mason-gh",
            "environment": "staging",
            "created_at_ms": 1,
        }
    if tag:
        request = request.model_copy(update={"text": f"{request.text} {tag}"})

    await slack_webhooks._process_slack_mention_impl(request, None)

    assert threads.metadata["sandbox_id"] == "sandbox-for-slack-files"
    assert threads.metadata["visibility"] == ("private" if private else "public")
    assert threads.metadata["owner_login"] == "mason-gh"
    assert len(provisioned) == 1
    assert provisioned[0].workspace_slug == environment
    assert provisioned[0].files == {"/workspace/.open-swe/slack-files/bundle.zip": b"zip"}
    run = captured["run_create"]["kwargs"]
    assert run["config"]["configurable"].get("environment") == environment
    assert "/workspace/.open-swe/slack-files/bundle.zip" in str(run["input"]["messages"])


@pytest.mark.parametrize("failure", ["account", "public-persistence", "private-persistence"])
async def test_slack_file_provisioning_requires_account_and_persisted_thread(
    monkeypatch, slack_file_mention, failure
):
    request, threads, provisioned, captured = slack_file_mention
    if failure == "account":
        monkeypatch.setattr(webhook_common, "get_valid_access_token", AsyncMock(return_value=None))
        monkeypatch.setattr(
            webhook_common, "has_access_token_record", AsyncMock(return_value=False)
        )
    else:
        monkeypatch.setattr(
            threads, "create", AsyncMock(side_effect=RuntimeError("store unavailable"))
        )

    if failure == "private-persistence":
        request = request.model_copy(update={"channel_context": SlackChannelContext(is_im=True)})
        with pytest.raises(RuntimeError, match="authorization metadata"):
            await slack_webhooks._process_slack_mention_impl(request, None)
    else:
        await slack_webhooks._process_slack_mention_impl(request, None)

    assert provisioned == []
    assert threads.metadata is None
    if failure == "public-persistence":
        assert "/workspace/.open-swe/slack-files/" not in str(
            captured["run_create"]["kwargs"]["input"]
        )
    else:
        assert "run_create" not in captured


@pytest.mark.parametrize("explicitly_tagged", [True, False])
@pytest.mark.parametrize("kitchen_channel", [True, False])
def test_slack_followup_publishes_as_requester_and_preserves_owner(
    monkeypatch: pytest.MonkeyPatch, explicitly_tagged: bool, kitchen_channel: bool, fake_store
) -> None:
    import importlib

    import langgraph_sdk

    from agent.dashboard import profiles

    opr = importlib.import_module("agent.tools.open_pull_request")
    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)
    client = slack_webhooks.get_langgraph_client()
    client.store = fake_store
    saved_metadata = {"visibility": "public", "owner_type": "user", "owner_login": "alice"}
    client.threads = _FakeThreadsClient(thread={"metadata": saved_metadata})
    monkeypatch.setattr(langgraph_sdk, "get_client", lambda: client)
    monkeypatch.setattr(webhook_common, "thread_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(webhook_common.User, "login_for_slack", AsyncMock(return_value="bob"))
    monkeypatch.setattr(
        profiles,
        "get_valid_access_token",
        AsyncMock(side_effect={"alice": "alice-token", "bob": "bob-token"}.get),
    )
    asyncio.run(
        slack_webhooks.process_slack_mention(
            SlackRequest(
                channel_id="C123",
                thread_ts="1700000000.000100",
                event_ts="1700000000.000300",
                user_id="U456",
                text="<@UBOT> create the PR" if explicitly_tagged else "create the PR",
                bot_user_id="UBOT",
                kitchen_channel=kitchen_channel,
                treat_all_messages_as_mentions=kitchen_channel,
            ),
            webhook_common.SlackRepoResolution(
                Repo(owner="langchain-ai", name="open-swe"), explicit=True
            ),
        )
    )
    run_create = captured["run_create"]
    assert isinstance(run_create, dict)
    kwargs = run_create["kwargs"]
    assert kwargs["multitask_strategy"] == ("interrupt" if explicitly_tagged else "enqueue")
    trigger = ElementTree.fromstring(kwargs["input"]["messages"][-1]["content"][0]["text"])
    assert trigger.get("explicit_bot_mention") == str(explicitly_tagged).lower()
    assert "<@UBOT>" not in (trigger.text or "")
    assert (
        f"@{webhook_common.SLACK_BOT_USERNAME} create the PR" in str(kwargs["input"])
    ) == explicitly_tagged
    run_config = kwargs["config"]
    run_config["configurable"]["thread_id"] = run_create["thread_id"]
    monkeypatch.setattr("agent.run_config.get_config", lambda: run_config)

    assert asyncio.run(opr._resolve_pr_author_token()) == ("bob-token", "user")
    assert saved_metadata["owner_login"] == "alice"


def test_process_slack_mention_unmapped_user_blocked_and_prompted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unmapped Slack user is blocked (no run) and prompted to link."""
    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        return False

    async def fake_login_for_slack_id(slack_user_id):
        return None

    async def fake_login_for_email(email):
        return None

    async def fake_post_prompt(
        channel_id, thread_ts, user_id, user_email, reason="unlinked", **kwargs
    ):
        captured["prompt"] = {"user_id": user_id, "user_email": user_email, "reason": reason}

    monkeypatch.setattr(webhook_common, "thread_exists", fake_thread_exists)
    monkeypatch.setattr(webhook_common.User, "login_for_slack", fake_login_for_slack_id)
    monkeypatch.setattr(webhook_common.User, "login_for_email", fake_login_for_email)
    monkeypatch.setattr(webhook_common, "post_account_link_prompt", fake_post_prompt)
    monkeypatch.setattr(webhook_common, "is_bot_token_only_mode", lambda: False)

    asyncio.run(
        slack_webhooks.process_slack_mention(
            SlackRequest.model_validate(
                {
                    "channel_id": "C123",
                    "thread_ts": "1700000000.000100",
                    "event_ts": "1700000000.000200",
                    "user_id": "U123",
                    "text": "<@UBOT> do the thing",
                    "bot_user_id": "UBOT",
                }
            ),
            webhook_common.SlackRepoResolution(
                Repo(owner="langchain-ai", name="open-swe"), explicit=True
            ),
        )
    )

    assert "run_create" not in captured
    assert captured["prompt"] == {
        "user_id": "U123",
        "user_email": "mason@example.com",
        "reason": "unlinked",
    }


def test_process_slack_mention_mapped_user_unusable_token_prompts_revoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user who signed in before but whose token is now unusable is told to re-auth."""
    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        return False

    async def fake_login_for_slack_id(slack_user_id):
        return "mason-gh" if slack_user_id == "U123" else None

    async def fake_get_valid_access_token(login):
        return None

    async def fake_has_token_record(login):
        return True

    async def fake_post_prompt(
        channel_id, thread_ts, user_id, user_email, reason="unlinked", **kwargs
    ):
        captured["prompt"] = {"reason": reason}

    monkeypatch.setattr(webhook_common, "thread_exists", fake_thread_exists)
    monkeypatch.setattr(webhook_common.User, "login_for_slack", fake_login_for_slack_id)
    monkeypatch.setattr(webhook_common, "get_valid_access_token", fake_get_valid_access_token)
    monkeypatch.setattr(webhook_common, "has_access_token_record", fake_has_token_record)
    monkeypatch.setattr(webhook_common, "post_account_link_prompt", fake_post_prompt)
    monkeypatch.setattr(webhook_common, "is_bot_token_only_mode", lambda: False)

    asyncio.run(
        slack_webhooks.process_slack_mention(
            SlackRequest.model_validate(
                {
                    "channel_id": "C123",
                    "thread_ts": "1700000000.000100",
                    "event_ts": "1700000000.000200",
                    "user_id": "U123",
                    "text": "<@UBOT> do the thing",
                    "bot_user_id": "UBOT",
                }
            ),
            webhook_common.SlackRepoResolution(
                Repo(owner="langchain-ai", name="open-swe"), explicit=True
            ),
        )
    )

    assert "run_create" not in captured
    assert captured["prompt"] == {"reason": "revoked"}


@pytest.fixture
def bot_run(monkeypatch, allowed_bot, fake_store):
    captured: dict[str, Any] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)
    monkeypatch.setenv("CONFIGURED_ADMINS", "mason-gh")
    monkeypatch.setattr(webhook_common, "is_bot_token_only_mode", lambda: False)
    monkeypatch.setattr(
        webhook_common,
        "get_valid_access_token",
        AsyncMock(side_effect=AssertionError("Bot requested personal OAuth")),
    )
    monkeypatch.setattr(webhook_common, "fetch_slack_thread_messages", AsyncMock(return_value=[]))

    class Threads:
        metadata: dict[str, Any] | None = None

        async def get(self, thread_id: str) -> dict[str, Any]:
            if self.metadata is None:
                raise _FakeNotFoundError()
            return {"thread_id": thread_id, "metadata": dict(self.metadata)}

        async def create(self, *, thread_id: str, metadata=None, **kwargs) -> None:
            if self.metadata is None:
                self.metadata = metadata or {}

        async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
            if self.metadata is None:
                raise _FakeNotFoundError()
            self.metadata.update(metadata)

    client = slack_webhooks.get_langgraph_client()
    client.threads = Threads()
    client.store = fake_store
    request = SlackRequest(
        channel_id="C123",
        thread_ts="1700000000.000100",
        thread_id="mapped-thread",
        event_ts="1700000000.000200",
        user_id="U123",
        text="<@UBOT> Open a PR",
        bot_user_id="UBOT",
        team_id="T123",
        triggering_bot_id="B123",
        triggering_bot_app_id="A123",
    )
    return request, client.threads, captured


@pytest.mark.parametrize("user_id", ["U123", ""])
async def test_allowed_bot_starts_and_continues_a_system_thread(bot_run, user_id):
    request, threads, captured = bot_run
    request = request.model_copy(update={"user_id": user_id})
    await slack_webhooks._process_slack_mention_impl(request, None)
    assert threads.metadata["owner_type"] == "system"
    assert threads.metadata["visibility"] == "public"
    assert not threads.metadata.get("owner_login")
    kwargs = captured.pop("run_create")["kwargs"]
    config = kwargs["config"]["configurable"]
    assert not config.get("github_login")
    assert not config.get("user_email")
    assert config["slack_thread"]["triggering_bot_id"] == "B123"
    assert config["slack_thread"]["triggering_user_id"] == user_id
    assert config["slack_thread"]["triggering_user_name"] == "Release bot"
    message = ElementTree.fromstring(kwargs["input"]["messages"][-1]["content"][0]["text"])
    assert message.attrib["sender"] == "system:slack-bot-B123"
    assert message.attrib["kind"] == "system"
    assert (message.text or "").strip() == f"@{webhook_common.SLACK_BOT_USERNAME} Open a PR"
    await slack_webhooks._process_slack_mention_impl(
        request.model_copy(update={"event_ts": "1700000000.000300"}), None
    )
    assert not captured["run_create"]["kwargs"]["config"]["configurable"].get("github_login")


async def test_bot_started_thread_stays_marked_after_a_person_replies(bot_run):
    request, threads, _ = bot_run
    await slack_webhooks._process_slack_mention_impl(request, None)
    assert threads.metadata["trigger_kind"] == "slack_bot"
    assert threads.metadata["triggering_bot"] == "T123:B123"
    assert await webhook_common.upsert_agent_thread_metadata(
        "mapped-thread", source="slack", user_email="alice@example.com", title=""
    )
    assert threads.metadata["trigger_kind"] == "slack_bot"
    assert threads.metadata["triggering_bot"] == "T123:B123"


@pytest.mark.parametrize("block", ["removed", "other-owner", "private", "other-bot", "store-error"])
async def test_bot_authorization_is_checked_before_execution(
    monkeypatch, bot_run, fake_store, block
):
    request, threads, captured = bot_run
    if block == "removed":
        await fake_store.delete_item(["allowed_slack_bots"], "T123:B123")
    elif block == "other-owner":
        threads.metadata = {"owner_type": "user", "owner_login": "alice", "visibility": "public"}
    elif block in {"private", "other-bot"}:
        await slack_webhooks._process_slack_mention_impl(request, None)
        captured.pop("run_create")
        if block == "private":
            threads.metadata["visibility"] = "private"
        else:
            threads.metadata["source_context"]["slack_thread"]["triggering_bot_id"] = "BOTHER"
    else:
        monkeypatch.setattr(fake_store, "get_item", AsyncMock(side_effect=RuntimeError("offline")))
    await slack_webhooks.process_slack_mention(request, None)
    assert "run_create" not in captured


@pytest.mark.parametrize("race", ["before-upsert", "during-create"])
async def test_bot_cannot_take_over_a_concurrently_created_thread(monkeypatch, bot_run, race):
    request, threads, captured = bot_run
    human = {"owner_type": "user", "owner_login": "alice", "visibility": "public"}
    if race == "before-upsert":
        upsert = webhook_common.upsert_agent_thread_metadata

        async def competing_upsert(*args, **kwargs):
            threads.metadata = dict(human)
            return await upsert(*args, **kwargs)

        monkeypatch.setattr(webhook_common, "upsert_agent_thread_metadata", competing_upsert)
    else:

        async def competing_create(**kwargs):
            threads.metadata = dict(human)

        monkeypatch.setattr(threads, "create", competing_create)
    with pytest.raises(RuntimeError, match="authorization metadata"):
        await slack_webhooks._process_slack_mention_impl(request, None)
    assert "run_create" not in captured
    assert threads.metadata == human


def _context_input(messages: list[dict], **kwargs: object) -> list[str]:
    run_input = slack_webhooks._slack_context_input(
        messages,
        cast(dict, kwargs.get("user_names_by_id", {"U123": "Alice", "UBOT": "Open SWE"})),
        cast(dict, kwargs.get("logins_by_user_id", {})),
        person_ids_by_user_id=cast(dict, kwargs.get("person_ids_by_user_id", {})),
        channel_names_by_id=cast(dict, kwargs.get("channel_names_by_id", {})),
        channel={"id": "slack:C123", "platform": "slack"},
        bot_user_id="UBOT",
        event_ts="9.0",
        request_text="do the thing",
        request_blocks=[{"type": "text", "text": "do the thing"}],
        dispatched_timestamps=cast(set, kwargs.get("dispatched_timestamps", set())),
        run_described_person_ids=cast(set, kwargs.get("run_described_person_ids", set())),
        explicit_mention=bool(kwargs.get("explicit_mention", False)),
    )
    return [cast(str, message["content"]) for message in run_input["messages"]]


@pytest.mark.parametrize("explicit_mention", [True, False])
def test_current_slack_message_preserves_ingress_mention(explicit_mention: bool) -> None:
    contents = _context_input([], explicit_mention=explicit_mention)
    trigger_blocks = contents[-1]
    assert isinstance(trigger_blocks, list)
    trigger = ElementTree.fromstring(trigger_blocks[0]["text"])
    assert trigger.get("explicit_bot_mention") == str(explicit_mention).lower()
    assert (trigger.text or "").strip() == "do the thing"


def test_replayed_slack_mentions_ignore_forwarded_tags() -> None:
    contents = _context_input(
        [
            {"ts": "1.0", "user": "U123", "text": "<@UBOT> could this stack?"},
            {
                "ts": "2.0",
                "user": "U123",
                "text": "interesting",
                "attachments": [{"is_share": True, "text": "<@UBOT> fix this"}],
            },
        ]
    )
    replayed = [
        ElementTree.fromstring(content)
        for content in contents
        if isinstance(content, str) and content.startswith("<input-message")
    ]
    assert [message.get("explicit_bot_mention") for message in replayed] == ["true", "false"]
    assert "could this stack?" in (replayed[0].text or "")


def test_slack_context_labels_mentioned_people_with_their_names() -> None:
    contents = _context_input(
        [
            {"ts": "1.0", "text": "cc <@U456> and <@U000> for viz", "user": "U123"},
            {"ts": "9.0", "text": "<@UBOT> go", "user": "U123"},
        ],
        user_names_by_id={"U123": "Alice", "U456": "Bob <B>"},
    )

    assert "cc &lt;@U456|Bob &amp;lt;B&amp;gt;&gt; and &lt;@U000&gt; for viz" in str(contents)


def test_slack_context_names_unnamed_channel_mentions() -> None:
    contents = _context_input(
        [
            {"ts": "1.0", "text": "see <#C456|>, <#C789|old-name>, <#C000>", "user": "U123"},
            {"ts": "9.0", "text": "<@UBOT> go", "user": "U123"},
        ],
        channel_names_by_id={"C456": "eng", "C789": "new-name"},
    )

    assert "see &lt;#C456|eng&gt;, &lt;#C789|old-name&gt;, &lt;#C000&gt;" in str(contents)


def test_breakout_preceding_text_is_prior_message_not_part_of_request() -> None:
    run_input = slack_webhooks._slack_context_input(
        [
            {
                "ts": "9.0",
                "text": "Context for this task.\nMore details <@UBOT> /breakout fix it",
                "user": "U123",
                "attachments": [
                    {
                        "is_share": True,
                        "author_name": "Bob",
                        "text": "Forwarded details",
                    }
                ],
            }
        ],
        {"U123": "Alice"},
        {},
        channel={"id": "slack:C123", "platform": "slack"},
        bot_user_id="UBOT",
        event_ts="9.0",
        trigger_user_id="U123",
        request_text="fix it",
        request_blocks=[{"type": "text", "text": "fix it"}],
        prior_message_text="Context for this task.\nMore details",
        is_breakout=True,
    )
    inputs = [
        message["content"]
        for message in run_input["messages"]
        if message["role"] == "user" and 'kind="human"' in str(message["content"])
    ]

    assert len(inputs) == 2
    assert "Context for this task.\nMore details" in inputs[0]
    assert "/breakout" not in inputs[0]
    assert "fix it" in inputs[1][0]["text"]
    assert "[Forwarded Slack message from Bob]" in inputs[1][0]["text"]
    assert "Forwarded details" in inputs[1][0]["text"]
    assert "More details" not in inputs[1][0]["text"]
    assert "/breakout" not in inputs[1][0]["text"]


def test_slack_context_never_replays_open_swes_own_replies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model wrote them and already holds them; replaying shows it its own words twice.

    Open SWE posts with a bot token, so its replies carry `user` *and* `bot_id`;
    keying off `user` alone attributes them to a person instead.
    """
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USERNAME", "Open SWE")
    contents = _context_input(
        [
            {"ts": "1.0", "text": "please fix it", "user": "U123"},
            {"ts": "1.1", "text": "on it", "user": "UBOT", "bot_id": "B1"},
            {"ts": "9.0", "text": "<@UBOT> do the thing", "user": "U123"},
        ]
    )

    assert not any("on it" in text for text in contents)
    assert not any("system:open-swe" in text for text in contents)
    assert not any('sender="slack:UBOT"' in text for text in contents)
    assert any("please fix it" in text for text in contents)


def test_slack_context_does_not_treat_a_lookalike_bot_as_open_swe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A third-party app may post under our own username; only the user id proves identity."""
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USERNAME", "Open SWE")
    contents = _context_input(
        [
            {
                "ts": "1.0",
                "text": "impersonating",
                "bot_id": "B9",
                "username": "Open SWE",
                "bot_profile": {"name": "Open SWE"},
            },
            {"ts": "9.0", "text": "<@UBOT> do the thing", "user": "U123"},
        ]
    )

    # Rendered as an ordinary bot, so the transcript still shows it.
    lookalike = next(text for text in contents if "impersonating" in text)
    assert 'sender="system:slack-bot-B9"' in lookalike
    assert 'sender="system:open-swe"' not in lookalike
    intro = next(text for text in contents if 'id="system:slack-bot-B9"' in text)
    assert "sender_type: bot" in intro


def test_queued_slack_edit_names_people_and_public_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)
    channels = {
        "C9": SlackChannel(
            id="C9",
            name="eng",
            payload={
                "id": "C9",
                "name": "eng",
                "is_channel": True,
                "is_private": False,
                "is_ext_shared": False,
                "is_pending_ext_shared": False,
            },
        ),
        "C8": SlackChannel(
            id="C8",
            name="secret",
            payload={"id": "C8", "name": "secret", "is_channel": True, "is_private": True},
        ),
    }

    async def fake_thread_exists(thread_id: str) -> bool:
        return True

    async def fake_queue_message_for_thread(thread_id: str, content: object) -> bool:
        captured["queued"] = content
        return True

    async def fake_load(channel_id: str) -> SlackChannel | None:
        return channels.get(channel_id)

    monkeypatch.setattr(webhook_common, "thread_exists", fake_thread_exists)
    monkeypatch.setattr(slack_webhooks, "queue_message_for_thread", fake_queue_message_for_thread)
    monkeypatch.setattr(slack_webhooks.SlackChannel, "load", fake_load)

    asyncio.run(
        slack_webhooks.process_slack_mention(
            SlackRequest.model_validate(
                {
                    "channel_id": "C123",
                    "thread_ts": "1700000000.000100",
                    "event_ts": "1700000000.000300",
                    "user_id": "U123",
                    "text": "<@UBOT> ask <@U456> in <#C9|> not <#C8|>",
                    "bot_user_id": "UBOT",
                    "message_update": True,
                    "attachments": [{"is_share": True, "text": "cc <@U777> in <#C9>"}],
                }
            ),
            webhook_common.SlackRepoResolution(
                Repo(owner="langchain-ai", name="open-swe"), explicit=True
            ),
        )
    )

    text = "\n".join(
        block["text"]
        for block in cast(list, captured["queued"])
        if isinstance(block, dict) and block.get("text")
    )
    assert "ask <@U456|Teammate> in <#C9|eng> not <#C8|>" in text
    assert "U777" in cast(list, captured["user_ids"])


def test_process_slack_mention_runs_an_edit_when_queueing_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A store outage must not swallow the correction outright."""
    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        return True

    async def fake_queue_message_for_thread(thread_id: str, content: object) -> bool:
        return False

    monkeypatch.setattr(webhook_common, "thread_exists", fake_thread_exists)
    monkeypatch.setattr(slack_webhooks, "queue_message_for_thread", fake_queue_message_for_thread)

    asyncio.run(
        slack_webhooks.process_slack_mention(
            SlackRequest.model_validate(
                {
                    "channel_id": "C123",
                    "thread_ts": "1700000000.000100",
                    "event_ts": "1700000000.000300",
                    "user_id": "U123",
                    "text": "<@UBOT> actually use PR 5889",
                    "bot_user_id": "UBOT",
                    "message_update": True,
                }
            ),
            webhook_common.SlackRepoResolution(
                Repo(owner="langchain-ai", name="open-swe"), explicit=True
            ),
        )
    )

    run_create = captured["run_create"]
    assert isinstance(run_create, dict)
    assert run_create["kwargs"]["multitask_strategy"] == "enqueue"


@pytest.mark.parametrize(
    ("run_cost", "expected_cost"),
    [(0.42, "$0.42"), (0.001, "$0.42 (<$0.01)")],
)
def test_pending_cost_marks_latest_reply_until_cost_arrives(
    run_cost: float, expected_cost: str
) -> None:
    url = "https://app.example/agents/t1"
    usage = RunUsageSummary(models=("model-a",), total_tokens=123)
    blocks = slack_utils._with_slack_web_link_context_block(
        "Done", [{"type": "section", "text": {"type": "mrkdwn", "text": "Done"}}], url, usage
    )
    text = slack_utils.append_slack_web_link_footer("Done", url, usage)
    assert "calculating cost" not in text

    pending_text, pending_blocks = slack_utils.with_slack_pending_session_cost(text, blocks)
    assert pending_text.endswith("model-a • calculating cost...")
    assert pending_blocks is not None
    assert pending_blocks[-1]["elements"][0]["text"].endswith("model-a • calculating cost...")

    # Idempotent while awaiting cost, and the refresh swaps the label for the cost.
    assert slack_utils.with_slack_pending_session_cost(pending_text, pending_blocks) == (
        pending_text,
        pending_blocks,
    )
    final_text, final_blocks = slack_utils.with_slack_session_cost(
        pending_text, pending_blocks, 0.42, run_cost=run_cost
    )
    assert final_text.endswith(f"model-a • {expected_cost}")
    assert final_blocks is not None
    assert final_blocks[-1]["elements"][0]["text"].endswith(f"model-a • {expected_cost}")
    assert slack_utils.with_slack_session_cost(
        final_text, final_blocks, 0.42, run_cost=run_cost
    ) == (
        final_text,
        final_blocks,
    )

    # Messages without a web footer (e.g. interim acknowledgements) stay untouched.
    assert slack_utils.with_slack_pending_session_cost("Working on it", None) == (
        "Working on it",
        None,
    )
