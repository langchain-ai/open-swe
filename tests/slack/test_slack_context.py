import asyncio
from typing import cast
from xml.etree import ElementTree

import pytest

from agent.source_context import SourceContext
from agent.utils import slack as slack_utils
from agent.utils.run_usage import RunUsageSummary
from agent.utils.slack import (
    convert_mentions_to_slack_format,
    format_slack_messages_for_prompt,
    get_slack_permalink,
    parse_github_pr_url,
    post_slack_trace_reply,
    replace_bot_mention_with_username,
    select_slack_context_messages,
    strip_bot_mention,
)
from agent.webhooks import common as webhook_common
from agent.webhooks import slack as slack_webhooks


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


def test_channel_context_preserves_external_sharing_status() -> None:
    context = slack_utils.normalize_slack_channel_context(
        "C123", {"name": "shared", "is_ext_shared": True}
    )

    assert context["is_ext_shared"] is True
    assert not slack_utils.slack_channel_allows_operations(context)


def test_channel_operations_fail_closed_without_external_sharing_status() -> None:
    context = slack_utils.normalize_slack_channel_context("C123", None)

    assert context["is_ext_shared"] is None
    assert not slack_utils.slack_channel_allows_operations(context)
    assert slack_utils.slack_channel_allows_operations(
        {"is_ext_shared": False, "is_pending_ext_shared": False}
    )
    assert not slack_utils.slack_channel_allows_operations(
        {"is_ext_shared": False, "is_pending_ext_shared": True}
    )
    assert slack_utils.slack_channel_allows_operations({"is_im": True})


def test_source_context_preserves_existing_slack_permalink_on_lookup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_slack_permalink(channel_id: str, thread_ts: str) -> str | None:
        assert channel_id == "C123"
        assert thread_ts == "1700000000.000100"
        return None

    monkeypatch.setattr(webhook_common, "get_slack_permalink", fake_get_slack_permalink)

    enriched = asyncio.run(
        webhook_common._source_context_with_slack_permalink(
            SourceContext.parse(
                {"slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.000100"}}
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

    assert enriched.slack_thread is not None
    assert enriched.slack_thread.permalink == "https://slack.example/existing"


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


def test_upsert_accumulates_participants_and_pins_source_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opening_context = {"slack_thread": {"triggering_user_id": "UFIRST"}}
    threads = _FakeThreadsClient({"metadata": {"source_context": opening_context}})
    monkeypatch.setattr(webhook_common, "get_client", lambda url: _FakeClient(threads))

    async def upsert(github_login: str, user_email: str, slack_user_id: str) -> None:
        await webhook_common.upsert_agent_thread_metadata(
            "thread-id",
            source="slack",
            github_login=github_login,
            user_email=user_email,
            title=github_login,
            source_context=SourceContext.parse(
                {"slack_thread": {"triggering_user_id": slack_user_id}}
            ),
        )

    asyncio.run(upsert("first-gh", "first@example.com", "UFIRST"))
    asyncio.run(upsert("commenter-gh", "commenter@example.com", "UCOMMENTER"))
    metadata = cast(dict, threads.thread)["metadata"]
    assert "github_login" not in metadata
    assert "triggering_user_email" not in metadata
    assert metadata["participant_logins"] == {"commenter-gh": True, "first-gh": True}
    assert metadata["source_context"] == opening_context
    assert metadata["title"] == metadata["title_seed"] == "first-gh"


def test_select_slack_context_messages_uses_thread_start_when_no_prior_mention() -> None:
    bot_user_id = "UBOT"
    messages = [
        {"ts": "1.0", "text": "hello", "user": "U1"},
        {"ts": "2.0", "text": "context", "user": "U2"},
        {"ts": "3.0", "text": "<@UBOT> please help", "user": "U1"},
    ]

    selected, mode = select_slack_context_messages(messages, "3.0", bot_user_id)

    assert mode == "thread_start"
    assert [item["ts"] for item in selected] == ["1.0", "2.0", "3.0"]


def test_select_slack_context_messages_uses_previous_mention_boundary() -> None:
    bot_user_id = "UBOT"
    messages = [
        {"ts": "1.0", "text": "hello", "user": "U1"},
        {"ts": "2.0", "text": "<@UBOT> first request", "user": "U1"},
        {"ts": "3.0", "text": "extra context", "user": "U2"},
        {"ts": "4.0", "text": "<@UBOT> second request", "user": "U3"},
    ]

    selected, mode = select_slack_context_messages(messages, "4.0", bot_user_id)

    assert mode == "last_mention"
    assert [item["ts"] for item in selected] == ["2.0", "3.0", "4.0"]


def test_select_slack_context_messages_ignores_messages_after_current_event() -> None:
    bot_user_id = "UBOT"
    messages = [
        {"ts": "1.0", "text": "<@UBOT> first request", "user": "U1"},
        {"ts": "2.0", "text": "follow-up", "user": "U2"},
        {"ts": "3.0", "text": "<@UBOT> second request", "user": "U3"},
        {"ts": "4.0", "text": "after event", "user": "U4"},
    ]

    selected, mode = select_slack_context_messages(messages, "3.0", bot_user_id)

    assert mode == "last_mention"
    assert [item["ts"] for item in selected] == ["1.0", "2.0", "3.0"]


def test_select_slack_context_messages_treats_direct_user_messages_as_mentions() -> None:
    bot_user_id = "UBOT"
    messages = [
        {"ts": "1.0", "text": "first request", "user": "U1"},
        {"ts": "2.0", "text": "agent response", "user": "UBOT", "bot_id": "B1"},
        {"ts": "3.0", "text": "follow up", "user": "U1"},
        {"ts": "3.5", "text": "agent response", "user": "UBOT", "bot_id": "B1"},
        {"ts": "4.0", "text": "latest", "user": "U1"},
    ]

    selected, mode = select_slack_context_messages(
        messages,
        "4.0",
        bot_user_id,
        treat_all_messages_as_mentions=True,
    )

    assert mode == "last_mention"
    assert [item["ts"] for item in selected] == ["3.0", "3.5", "4.0"]


def test_strip_bot_mention_removes_bot_tag() -> None:
    assert strip_bot_mention("<@UBOT> please check", "UBOT") == "please check"


def test_strip_bot_mention_removes_bot_username_tag() -> None:
    assert (
        strip_bot_mention("@open-swe please check", "UBOT", bot_username="open-swe")
        == "please check"
    )


def test_replace_bot_mention_with_username() -> None:
    assert (
        replace_bot_mention_with_username("<@UBOT> can you help?", "UBOT", "open-swe")
        == "@open-swe can you help?"
    )


def test_convert_mentions_to_slack_format_basic() -> None:
    assert (
        convert_mentions_to_slack_format("Hey @Brace Sproul(U06KD8BFY95), check this")
        == "Hey <@U06KD8BFY95>, check this"
    )


def test_convert_mentions_to_slack_format_multiple() -> None:
    text = "@Alice(U111) and @Bob(U222) please review"
    assert convert_mentions_to_slack_format(text) == "<@U111> and <@U222> please review"


def test_convert_mentions_to_slack_format_no_match() -> None:
    text = "No mentions here, just @plain text"
    assert convert_mentions_to_slack_format(text) == text


def test_convert_mentions_to_slack_format_preserves_existing_slack_mentions() -> None:
    text = "Already tagged <@U06KD8BFY95> correctly"
    assert convert_mentions_to_slack_format(text) == text


def test_parse_github_pr_url_raw_url() -> None:
    pr_ref = parse_github_pr_url("https://github.com/langchain-ai/open-swe/pull/1244")

    assert pr_ref is not None
    assert pr_ref.owner == "langchain-ai"
    assert pr_ref.repo == "open-swe"
    assert pr_ref.number == 1244
    assert pr_ref.url == "https://github.com/langchain-ai/open-swe/pull/1244"


def test_parse_github_pr_url_slack_formatted_link() -> None:
    pr_ref = parse_github_pr_url("<https://github.com/langchain-ai/open-swe/pull/1244|PR>")

    assert pr_ref is not None
    assert pr_ref.owner == "langchain-ai"
    assert pr_ref.repo == "open-swe"
    assert pr_ref.number == 1244


def test_format_slack_messages_for_prompt_includes_ids_for_each_message() -> None:
    formatted = format_slack_messages_for_prompt(
        [
            {"ts": "1.0", "text": "hello", "user": "U123"},
            {"ts": "1.1", "text": "follow up", "user": "U456"},
        ],
        {"U123": "alice", "U456": "bob"},
    )

    assert formatted == (
        "@alice(U123) [message_ts=1.0]: hello\n@bob(U456) [message_ts=1.1]: follow up"
    )


def test_format_slack_messages_for_prompt_replaces_bot_id_mention_in_text() -> None:
    formatted = format_slack_messages_for_prompt(
        [{"ts": "1.0", "text": "<@UBOT> status update?", "user": "U123"}],
        {"U123": "alice"},
        bot_user_id="UBOT",
        bot_username="open-swe",
    )

    assert formatted == "@alice(U123) [message_ts=1.0]: @open-swe status update?"


def test_format_slack_messages_for_prompt_includes_forwarded_attachment() -> None:
    formatted = format_slack_messages_for_prompt(
        [
            {
                "ts": "1.0",
                "text": "please handle this",
                "user": "U123",
                "attachments": [
                    {
                        "is_share": True,
                        "author_name": "Bob",
                        "text": "The forwarded request",
                        "from_url": "https://example.slack.com/archives/C123/p123",
                    }
                ],
            }
        ],
        {"U123": "alice"},
    )

    assert formatted == (
        "@alice(U123) [message_ts=1.0]: please handle this\n"
        "[Forwarded Slack message from Bob]\n"
        "The forwarded request\n"
        "Source: https://example.slack.com/archives/C123/p123"
    )


def test_format_slack_messages_for_prompt_uses_forwarded_fallback() -> None:
    formatted = format_slack_messages_for_prompt(
        [
            {
                "ts": "1.0",
                "text": "",
                "user": "U123",
                "attachments": [
                    {
                        "is_reply_unfurl": True,
                        "author_name": "Bob",
                        "fallback": "Fallback forwarded text",
                    }
                ],
            }
        ],
        {"U123": "alice"},
    )

    assert formatted == (
        "@alice(U123) [message_ts=1.0]: [forwarded message]\n"
        "[Forwarded Slack message from Bob]\n"
        "Fallback forwarded text"
    )


def test_format_slack_messages_for_prompt_includes_nested_forwarded_attachments() -> None:
    formatted = format_slack_messages_for_prompt(
        [
            {
                "ts": "1.0",
                "text": "nested context",
                "user": "U123",
                "attachments": [
                    {
                        "is_share": True,
                        "author_name": "Bob",
                        "text": "First level",
                        "attachments": [
                            {
                                "is_share": True,
                                "author_name": "Carol",
                                "text": "Second level",
                                "attachments": [
                                    {
                                        "is_reply_unfurl": True,
                                        "author_name": "Dave",
                                        "text": "Third level",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
        {"U123": "alice"},
    )

    assert formatted == (
        "@alice(U123) [message_ts=1.0]: nested context\n"
        "[Forwarded Slack message from Bob]\n"
        "First level\n"
        "  [Forwarded Slack message from Carol]\n"
        "  Second level\n"
        "    [Forwarded Slack message from Dave]\n"
        "    Third level"
    )


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


def test_format_slack_messages_for_prompt_ignores_regular_unfurl_attachment() -> None:
    formatted = format_slack_messages_for_prompt(
        [
            {
                "ts": "1.0",
                "text": "look at this link",
                "user": "U123",
                "attachments": [{"title": "Example", "text": "Unfurl preview"}],
            }
        ],
        {"U123": "alice"},
    )

    assert formatted == "@alice(U123) [message_ts=1.0]: look at this link"


def test_post_slack_thread_reply_adds_web_context_block(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_post_message_with_ts(
        channel_id: str,
        text: str,
        *,
        thread_ts: str | None = None,
        unfurl_links: bool = True,
        unfurl_media: bool = True,
        blocks: list[dict] | None = None,
    ) -> tuple[str | None, str | None]:
        captured.update(
            {
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "text": text,
                "unfurl_links": unfurl_links,
                "unfurl_media": unfurl_media,
                "blocks": blocks,
            }
        )
        return "1.1", None

    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(slack_utils, "_post_slack_message_with_ts", fake_post_message_with_ts)

    asyncio.run(
        slack_utils.post_slack_thread_reply_with_ts(
            "C123", "1.0", "Done", agent_thread_id="mapped-thread"
        )
    )

    expected_footer = "<https://app.example.com/agents/mapped-thread|Open in Web>"
    assert captured["text"] == f"Done {expected_footer}"
    assert captured["blocks"] == [
        {"type": "section", "text": {"type": "mrkdwn", "text": "Done"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": expected_footer}]},
    ]

    captured.clear()
    asyncio.run(
        slack_utils.post_slack_thread_reply_with_ts(
            "C-code",
            webhook_common.CODE_CHANNEL_SESSION_TS,
            "Done",
            agent_thread_id="mapped-thread",
        )
    )
    assert captured["text"] == "Done"
    assert captured["blocks"] is None


def test_post_slack_thread_reply_keeps_long_messages_text_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_post_message_with_ts(
        channel_id: str,
        text: str,
        *,
        thread_ts: str | None = None,
        unfurl_links: bool = True,
        unfurl_media: bool = True,
        blocks: list[dict] | None = None,
    ) -> tuple[str | None, str | None]:
        captured.update({"text": text, "blocks": blocks})
        return "1.1", None

    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(slack_utils, "_post_slack_message_with_ts", fake_post_message_with_ts)

    long_text = "x" * (slack_utils.SLACK_SECTION_TEXT_MAX_CHARS + 1)
    asyncio.run(
        slack_utils.post_slack_thread_reply_with_ts(
            "C123", "1.0", long_text, agent_thread_id="mapped-thread"
        )
    )

    expected_thread_id = "mapped-thread"
    expected_footer = f"<https://app.example.com/agents/{expected_thread_id}|Open in Web>"
    assert captured["text"] == f"{long_text} {expected_footer}"
    assert captured["blocks"] is None


def test_post_slack_thread_reply_appends_web_context_block_to_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "Pick one"}},
        {"type": "actions", "elements": []},
    ]

    async def fake_post_message_with_ts(
        channel_id: str,
        text: str,
        *,
        thread_ts: str | None = None,
        unfurl_links: bool = True,
        unfurl_media: bool = True,
        blocks: list[dict] | None = None,
    ) -> tuple[str | None, str | None]:
        captured.update({"text": text, "blocks": blocks})
        return "1.1", None

    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(slack_utils, "_post_slack_message_with_ts", fake_post_message_with_ts)

    asyncio.run(
        slack_utils.post_slack_thread_reply_with_ts(
            "C123",
            "1.0",
            "Pick one",
            blocks=blocks,
            agent_thread_id="mapped-thread",
        )
    )

    expected_thread_id = "mapped-thread"
    expected_footer = f"<https://app.example.com/agents/{expected_thread_id}|Open in Web>"
    assert captured["text"] == f"Pick one {expected_footer}"
    posted_blocks = captured["blocks"]
    assert isinstance(posted_blocks, list)
    assert posted_blocks[:-1] == blocks
    assert posted_blocks[-1] == {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": expected_footer}],
    }
    assert blocks[0]["text"]["text"] == "Pick one"


def test_post_slack_thread_reply_keeps_usage_with_existing_web_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    usage = RunUsageSummary(models=("model-a",), main_agent_tokens=110)

    async def fake_post_message_with_ts(
        channel_id: str,
        text: str,
        **kwargs: object,
    ) -> tuple[str | None, str | None]:
        captured.update(kwargs)
        return "1.1", None

    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(slack_utils, "_post_slack_message_with_ts", fake_post_message_with_ts)
    dashboard_url = slack_utils._slack_thread_dashboard_url(
        "C123", "1.0", agent_thread_id="mapped-thread"
    )
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": dashboard_url}}]

    asyncio.run(
        slack_utils.post_slack_thread_reply_with_ts(
            "C123",
            "1.0",
            "Done",
            blocks=blocks,
            usage=usage,
            agent_thread_id="mapped-thread",
        )
    )

    posted_blocks = cast(list[dict[str, object]], captured["blocks"])
    assert str(posted_blocks).count(str(dashboard_url)) == 1
    assert posted_blocks[-1] == {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "model-a • 110 main-agent tokens"}],
    }


def test_format_slack_web_link_footer_includes_run_usage() -> None:
    usage = RunUsageSummary(models=("model-a", "model-b"), main_agent_tokens=12_345)

    footer = slack_utils.format_slack_web_link_footer("https://app.example/agents/t1", usage)

    assert footer == (
        "<https://app.example/agents/t1|Open in Web> • model-a + model-b • 12.3K main-agent tokens"
    )


def test_format_slack_web_link_footer_prefers_session_cost() -> None:
    usage = RunUsageSummary(models=("model-a",), main_agent_tokens=12_345, session_cost_usd=0.42)

    footer = slack_utils.format_slack_web_link_footer("https://app.example/agents/t1", usage)

    assert footer == "<https://app.example/agents/t1|Open in Web> • model-a • $0.42"


def test_with_slack_session_cost_preserves_blocks_and_is_idempotent() -> None:
    text = "Done <https://app.example/agents/t1|Open in Web> • model-a • 110 main-agent tokens"
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "Done"}},
        {"type": "actions", "elements": [{"type": "button", "action_id": "approve"}]},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        "<https://app.example/agents/t1|Open in Web> • model-a • "
                        "110 main-agent tokens"
                    ),
                }
            ],
        },
    ]

    updated_text, updated_blocks = slack_utils.with_slack_session_cost(text, blocks, 0.42)
    repeated = slack_utils.with_slack_session_cost(updated_text, updated_blocks, 0.42)

    assert repeated == (updated_text, updated_blocks)
    assert updated_text.endswith("model-a • $0.42")
    assert "main-agent tokens" not in updated_text
    assert updated_blocks is not None
    assert updated_blocks[1] == blocks[1]
    assert updated_blocks[2]["elements"][0]["text"].endswith("model-a • $0.42")
    assert "main-agent tokens" not in updated_blocks[2]["elements"][0]["text"]


def test_post_slack_trace_reply_has_no_tip(monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[dict] = []

    async def fake_post_slack_thread_reply_with_ts(
        channel_id: str,
        thread_ts: str,
        text: str,
        *,
        unfurl_links: bool = True,
        unfurl_media: bool = True,
        agent_thread_id: str | None = None,
    ) -> tuple[str | None, str | None]:
        posted.append({"text": text, "unfurl_links": unfurl_links, "unfurl_media": unfurl_media})
        return "1.1", None

    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(
        slack_utils, "post_slack_thread_reply_with_ts", fake_post_slack_thread_reply_with_ts
    )
    monkeypatch.setattr(slack_utils, "get_langsmith_trace_url", _fake_trace_url)

    asyncio.run(post_slack_trace_reply("C123", "1.0", "thread-id"))

    assert posted[0]["text"] == (
        "<https://smith/x|View trace> • <https://app.example.com/agents/thread-id|Open in Web>"
    )
    assert "Tip:" not in posted[0]["text"]
    assert posted[0]["unfurl_links"] is False
    assert posted[0]["unfurl_media"] is False


def test_select_slack_context_messages_detects_username_mention() -> None:
    selected, mode = select_slack_context_messages(
        [
            {"ts": "1.0", "text": "@open-swe first request", "user": "U1"},
            {"ts": "2.0", "text": "follow up", "user": "U2"},
            {"ts": "3.0", "text": "@open-swe second request", "user": "U3"},
        ],
        "3.0",
        bot_user_id="UBOT",
        bot_username="open-swe",
    )

    assert mode == "last_mention"
    assert [item["ts"] for item in selected] == ["1.0", "2.0", "3.0"]


def test_get_slack_repo_config_uses_existing_thread_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads_client = _FakeThreadsClient(
        thread={"metadata": {"repo": {"owner": "saved-owner", "name": "saved-repo"}}}
    )

    posted = False

    async def fake_post_slack_thread_reply(channel_id: str, thread_ts: str, text: str) -> bool:
        nonlocal posted
        posted = True
        return True

    monkeypatch.setattr(webhook_common, "get_client", lambda url: _FakeClient(threads_client))
    monkeypatch.setattr(
        webhook_common, "post_slack_thread_reply", fake_post_slack_thread_reply, raising=False
    )

    repo = asyncio.run(
        webhook_common.get_slack_repo_config("C123", "1.234", thread_id="mapped-thread")
    )

    assert repo == {"owner": "saved-owner", "name": "saved-repo"}
    assert threads_client.requested_thread_id == "mapped-thread"
    assert not posted


async def _no_team_default_repo() -> dict[str, str] | None:
    return None


def test_get_slack_repo_config_new_thread_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads_client = _FakeThreadsClient(raise_not_found=True)
    monkeypatch.setattr(webhook_common, "SLACK_REPO_OWNER", "default-owner")
    monkeypatch.setattr(webhook_common, "SLACK_REPO_NAME", "default-repo")
    monkeypatch.setattr(webhook_common, "get_team_default_repo", _no_team_default_repo)

    monkeypatch.setattr(webhook_common, "get_client", lambda url: _FakeClient(threads_client))

    repo = asyncio.run(
        webhook_common.get_slack_repo_config("C123", "1.234", thread_id="mapped-thread")
    )

    assert repo == {"owner": "default-owner", "name": "default-repo"}


def test_get_slack_repo_config_existing_thread_without_repo_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads_client = _FakeThreadsClient(thread={"metadata": {}})
    monkeypatch.setattr(webhook_common, "SLACK_REPO_OWNER", "default-owner")
    monkeypatch.setattr(webhook_common, "SLACK_REPO_NAME", "default-repo")
    monkeypatch.setattr(webhook_common, "get_team_default_repo", _no_team_default_repo)

    monkeypatch.setattr(webhook_common, "get_client", lambda url: _FakeClient(threads_client))

    repo = asyncio.run(
        webhook_common.get_slack_repo_config("C123", "1.234", thread_id="mapped-thread")
    )

    assert repo == {"owner": "default-owner", "name": "default-repo"}
    assert threads_client.requested_thread_id == "mapped-thread"


def test_get_slack_repo_config_ignores_repo_syntax_in_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads_client = _FakeThreadsClient(
        thread={"metadata": {"repo": {"owner": "saved-owner", "name": "saved-repo"}}}
    )

    monkeypatch.setattr(webhook_common, "get_client", lambda url: _FakeClient(threads_client))

    repo = asyncio.run(
        webhook_common.get_slack_repo_config("C123", "1.234", thread_id="mapped-thread")
    )

    assert repo == {"owner": "saved-owner", "name": "saved-repo"}


def test_get_slack_repo_config_applies_profile_default_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads_client = _FakeThreadsClient(thread={"metadata": {}})

    async def fake_get_slack_user_info(user_id: str) -> dict:
        return {"profile": {"email": "mason@example.com"}}

    async def fake_resolve_login_from_email_async(email: str | None) -> str | None:
        return "mason"

    async def fake_get_profile_default_repo(login: str | None) -> dict[str, str] | None:
        assert login == "mason"
        return {"owner": "profile-owner", "name": "profile-repo"}

    monkeypatch.setattr(webhook_common, "get_client", lambda url: _FakeClient(threads_client))
    monkeypatch.setattr(webhook_common, "get_slack_user_info", fake_get_slack_user_info)
    monkeypatch.setattr(
        webhook_common, "resolve_login_from_email_async", fake_resolve_login_from_email_async
    )
    monkeypatch.setattr(webhook_common, "get_profile_default_repo", fake_get_profile_default_repo)

    repo = asyncio.run(
        webhook_common.get_slack_repo_config(
            "C123", "1.234", slack_user_id="U123", thread_id="mapped-thread"
        )
    )

    assert repo == {"owner": "profile-owner", "name": "profile-repo"}


def test_get_slack_repo_config_applies_team_default_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads_client = _FakeThreadsClient(thread={"metadata": {}})

    async def fake_get_team_default_repo() -> dict[str, str] | None:
        return {"owner": "team-owner", "name": "team-repo"}

    monkeypatch.setattr(webhook_common, "get_client", lambda url: _FakeClient(threads_client))
    monkeypatch.setattr(webhook_common, "get_team_default_repo", fake_get_team_default_repo)
    monkeypatch.setattr(webhook_common, "SLACK_REPO_NAME", "")
    monkeypatch.setattr(webhook_common, "DEFAULT_REPO_NAME", "")

    repo = asyncio.run(
        webhook_common.get_slack_repo_config("C123", "1.234", thread_id="mapped-thread")
    )

    assert repo == {"owner": "team-owner", "name": "team-repo"}


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
    monkeypatch.setattr(webhook_common, "login_for_slack_id", fake_login_for_slack_id)
    monkeypatch.setattr(webhook_common, "login_for_email", fake_login_for_email)
    monkeypatch.setattr(webhook_common, "refresh_user_mapping_cache", fake_refresh_cache)
    monkeypatch.setattr(webhook_common, "get_valid_access_token", fake_get_valid_access_token)
    monkeypatch.setattr(webhook_common, "_post_account_link_prompt", fake_post_prompt)


def test_process_slack_mention_preserves_forwarded_attachment_from_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_fetch_slack_thread_messages(channel_id: str, thread_ts: str) -> list[dict]:
        return [
            {"ts": thread_ts, "text": "thread root", "user": "U123"},
            {
                "ts": "1700000000.000200",
                "text": "<@UBOT> handle this",
                "user": "U123",
            },
        ]

    async def fake_thread_exists(thread_id: str) -> bool:
        return True

    monkeypatch.setattr(
        webhook_common, "fetch_slack_thread_messages", fake_fetch_slack_thread_messages
    )
    monkeypatch.setattr(webhook_common, "_thread_exists", fake_thread_exists)

    asyncio.run(
        slack_webhooks.process_slack_mention(
            {
                "channel_id": "C123",
                "thread_ts": "1700000000.000100",
                "event_ts": "1700000000.000200",
                "user_id": "U123",
                "text": "<@UBOT> handle this",
                "attachments": [
                    {
                        "is_share": True,
                        "author_name": "Teammate",
                        "text": "Forwarded requirements",
                    }
                ],
                "bot_user_id": "UBOT",
            },
            {"owner": "langchain-ai", "name": "open-swe"},
        )
    )

    run_create = captured["run_create"]
    assert isinstance(run_create, dict)
    kwargs = run_create["kwargs"]
    prompt_block = kwargs["input"]["messages"][-1]["content"][0]
    prompt = ElementTree.fromstring(prompt_block["text"]).findtext("content") or ""
    assert "[Forwarded Slack message from Teammate]" in prompt
    assert "Forwarded requirements" in prompt


def test_process_slack_mention_creates_thread_first_run_without_trace_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        captured["thread_exists_check"] = thread_id
        return False

    monkeypatch.setattr(webhook_common, "_thread_exists", fake_thread_exists)

    thread_ts = "1700000000.000100"
    event_ts = "1700000000.000200"
    expected_thread_id = "mapped-thread"

    asyncio.run(
        slack_webhooks.process_slack_mention(
            {
                "channel_id": "C123",
                "thread_ts": thread_ts,
                "event_ts": event_ts,
                "user_id": "U123",
                "text": "<@UBOT> continue on the branch",
                "bot_user_id": "UBOT",
            },
            {"owner": "langchain-ai", "name": "open-swe"},
        )
    )

    assert captured["thread_exists_check"] == expected_thread_id
    assert captured["fetch_thread"] == {"channel_id": "C123", "thread_ts": thread_ts}
    metadata_update = captured["metadata_update"]
    assert isinstance(metadata_update, dict)
    assert metadata_update["thread_id"] == expected_thread_id
    assert "injected_dynamic_context_hashes" not in metadata_update["metadata"]
    assert "trace_reply" not in captured

    run_create = captured["run_create"]
    assert isinstance(run_create, dict)
    assert run_create["thread_id"] == expected_thread_id
    assert run_create["graph"] == "agent"
    kwargs = run_create["kwargs"]
    assert kwargs["if_not_exists"] == "create"
    assert kwargs["multitask_strategy"] == "interrupt"
    assert kwargs["durability"] == "sync"
    slack_thread_context = kwargs["config"]["configurable"]["slack_thread"]
    assert slack_thread_context["thread_ts"] == thread_ts
    assert slack_thread_context["triggering_user_timezone"] == "America/New_York"
    messages = kwargs["input"]["messages"]
    entities = [
        ElementTree.fromstring(message["content"])
        for message in messages
        if isinstance(message["content"], str) and message["content"].startswith("<dynamic-context")
    ]
    person = next(entity for entity in entities if entity.attrib["id"] == "slack:U123")
    channel = next(entity for entity in entities if entity.attrib["id"] == "slack:C123")
    request_block = messages[-1]["content"][0]
    request = ElementTree.fromstring(request_block["text"]).findtext("content") or ""
    prompt_message = next(
        message
        for message in messages
        if isinstance(message["content"], str)
        and 'sender="system:slack-context"' in message["content"]
    )
    prompt = ElementTree.fromstring(prompt_message["content"]).findtext("content") or ""
    assert person.findtext("display_name") == "Mason"
    assert channel.attrib["id"] == "slack:C123"
    assert "## Default Repository Hint\nlangchain-ai/open-swe" in prompt
    assert "## Triggering User Time Zone\nAmerica/New_York" in prompt
    assert (
        "Use this only if the Slack conversation does not identify a different repository."
        in prompt
    )
    assert prompt.count("## Slack Thread") == 1
    assert f"Thread TS: {thread_ts}" in prompt
    assert "## Open SWE Links" in prompt
    assert f"- Web: https://app.example.com/agents/{expected_thread_id}" in prompt
    assert "- Trace: https://smith/x" in prompt
    assert "do not duplicate it manually" in prompt
    assert "slack_thread_reply" not in prompt
    assert "slack_add_reaction" not in prompt
    assert "slack_read_thread_messages" not in prompt
    assert request == "continue on the branch"


def test_process_slack_mention_treats_direct_message_as_implicit_mention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        return True

    async def fake_fetch_slack_thread_messages(channel_id: str, thread_ts: str) -> list[dict]:
        captured["fetch_thread"] = {"channel_id": channel_id, "thread_ts": thread_ts}
        return [
            {"ts": "1700000000.000100", "text": "first request", "user": "U123"},
            {
                "ts": "1700000000.000150",
                "text": "agent response",
                "user": "UBOT",
                "bot_id": "B1",
            },
            {"ts": "1700000000.000200", "text": "continue on the branch", "user": "U123"},
        ]

    monkeypatch.setattr(webhook_common, "_thread_exists", fake_thread_exists)
    monkeypatch.setattr(
        webhook_common, "fetch_slack_thread_messages", fake_fetch_slack_thread_messages
    )

    asyncio.run(
        slack_webhooks.process_slack_mention(
            {
                "channel_id": "D123",
                "thread_ts": "1700000000.000100",
                "event_ts": "1700000000.000200",
                "user_id": "U123",
                "text": "continue on the branch",
                "bot_user_id": "UBOT",
                "treat_all_messages_as_mentions": True,
            },
            {"owner": "langchain-ai", "name": "open-swe"},
        )
    )

    run_create = captured["run_create"]
    assert isinstance(run_create, dict)
    messages = run_create["kwargs"]["input"]["messages"]
    prompt_message = next(
        message
        for message in messages
        if isinstance(message["content"], str)
        and 'sender="system:slack-context"' in message["content"]
    )
    prompt = ElementTree.fromstring(prompt_message["content"]).findtext("content") or ""
    request_block = messages[-1]["content"][0]
    request = ElementTree.fromstring(request_block["text"]).findtext("content") or ""
    assert "Context starts at: the previous direct message" in prompt
    assert request == "continue on the branch"
    context_messages = captured["context_messages"]
    assert isinstance(context_messages, list)
    assert [message["ts"] for message in context_messages] == [
        "1700000000.000100",
        "1700000000.000150",
        "1700000000.000200",
    ]


def test_process_slack_mention_skips_trace_reply_on_followup_mention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subsequent mentions in a Slack thread should not post 'Working on it!'."""
    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        captured["thread_exists_check"] = thread_id
        return True

    monkeypatch.setattr(webhook_common, "_thread_exists", fake_thread_exists)

    thread_ts = "1700000000.000100"
    event_ts = "1700000000.000300"
    expected_thread_id = "mapped-thread"

    asyncio.run(
        slack_webhooks.process_slack_mention(
            {
                "channel_id": "C123",
                "thread_ts": thread_ts,
                "event_ts": event_ts,
                "user_id": "U123",
                "text": "<@UBOT> follow up question",
                "bot_user_id": "UBOT",
            },
            {"owner": "langchain-ai", "name": "open-swe"},
        )
    )

    assert captured["thread_exists_check"] == expected_thread_id
    assert "trace_reply" not in captured
    run_create = captured["run_create"]
    assert isinstance(run_create, dict)
    assert run_create["thread_id"] == expected_thread_id


def test_process_slack_mention_unmapped_user_blocked_and_prompted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unmapped Slack user is blocked (no run) and prompted to link."""
    from agent.dashboard import user_mappings

    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)
    user_mappings.clear_cache()

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

    monkeypatch.setattr(webhook_common, "_thread_exists", fake_thread_exists)
    monkeypatch.setattr(webhook_common, "login_for_slack_id", fake_login_for_slack_id)
    monkeypatch.setattr(webhook_common, "login_for_email", fake_login_for_email)
    monkeypatch.setattr(webhook_common, "_post_account_link_prompt", fake_post_prompt)
    monkeypatch.setattr(webhook_common, "is_bot_token_only_mode", lambda: False)

    asyncio.run(
        slack_webhooks.process_slack_mention(
            {
                "channel_id": "C123",
                "thread_ts": "1700000000.000100",
                "event_ts": "1700000000.000200",
                "user_id": "U123",
                "text": "<@UBOT> do the thing",
                "bot_user_id": "UBOT",
            },
            {"owner": "langchain-ai", "name": "open-swe"},
        )
    )

    assert "run_create" not in captured
    assert captured["prompt"] == {
        "user_id": "U123",
        "user_email": "mason@example.com",
        "reason": "unlinked",
    }


def test_process_slack_mention_mapped_user_no_token_record_prompts_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mapped user who never signed in (no token record) is prompted to set up."""
    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        return False

    async def fake_login_for_slack_id(slack_user_id):
        return "mason-gh" if slack_user_id == "U123" else None

    async def fake_get_valid_access_token(login):
        return None

    async def fake_has_token_record(login):
        return False

    async def fake_post_prompt(
        channel_id, thread_ts, user_id, user_email, reason="unlinked", **kwargs
    ):
        captured["prompt"] = {"reason": reason}

    monkeypatch.setattr(webhook_common, "_thread_exists", fake_thread_exists)
    monkeypatch.setattr(webhook_common, "login_for_slack_id", fake_login_for_slack_id)
    monkeypatch.setattr(webhook_common, "get_valid_access_token", fake_get_valid_access_token)
    monkeypatch.setattr(webhook_common, "has_access_token_record", fake_has_token_record)
    monkeypatch.setattr(webhook_common, "_post_account_link_prompt", fake_post_prompt)
    monkeypatch.setattr(webhook_common, "is_bot_token_only_mode", lambda: False)

    asyncio.run(
        slack_webhooks.process_slack_mention(
            {
                "channel_id": "C123",
                "thread_ts": "1700000000.000100",
                "event_ts": "1700000000.000200",
                "user_id": "U123",
                "text": "<@UBOT> do the thing",
                "bot_user_id": "UBOT",
            },
            {"owner": "langchain-ai", "name": "open-swe"},
        )
    )

    assert "run_create" not in captured
    assert captured["prompt"] == {"reason": "unlinked"}


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

    monkeypatch.setattr(webhook_common, "_thread_exists", fake_thread_exists)
    monkeypatch.setattr(webhook_common, "login_for_slack_id", fake_login_for_slack_id)
    monkeypatch.setattr(webhook_common, "get_valid_access_token", fake_get_valid_access_token)
    monkeypatch.setattr(webhook_common, "has_access_token_record", fake_has_token_record)
    monkeypatch.setattr(webhook_common, "_post_account_link_prompt", fake_post_prompt)
    monkeypatch.setattr(webhook_common, "is_bot_token_only_mode", lambda: False)

    asyncio.run(
        slack_webhooks.process_slack_mention(
            {
                "channel_id": "C123",
                "thread_ts": "1700000000.000100",
                "event_ts": "1700000000.000200",
                "user_id": "U123",
                "text": "<@UBOT> do the thing",
                "bot_user_id": "UBOT",
            },
            {"owner": "langchain-ai", "name": "open-swe"},
        )
    )

    assert "run_create" not in captured
    assert captured["prompt"] == {"reason": "revoked"}


def test_process_slack_mention_mapped_user_with_token_runs_as_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mapped, authenticated Slack user runs as themselves with no prompt."""
    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        return False

    async def fake_login_for_slack_id(slack_user_id):
        return "mason-gh" if slack_user_id == "U123" else None

    owner_meta: dict[str, object] = {}

    async def fake_upsert_owner(thread_id: str, **kwargs: object) -> None:
        owner_meta.update(kwargs)

    monkeypatch.setattr(webhook_common, "_thread_exists", fake_thread_exists)
    monkeypatch.setattr(webhook_common, "login_for_slack_id", fake_login_for_slack_id)
    monkeypatch.setattr(webhook_common, "upsert_agent_thread_metadata", fake_upsert_owner)

    asyncio.run(
        slack_webhooks.process_slack_mention(
            {
                "channel_id": "C123",
                "thread_ts": "1700000000.000100",
                "event_ts": "1700000000.000200",
                "user_id": "U123",
                "text": "<@UBOT> do the thing",
                "bot_user_id": "UBOT",
            },
            {"owner": "langchain-ai", "name": "open-swe"},
        )
    )

    run_create = captured["run_create"]
    run_create_data = cast(dict[str, object], run_create)
    kwargs = cast(dict[str, object], run_create_data["kwargs"])
    config = cast(dict[str, object], kwargs["config"])
    configurable = cast(dict[str, object], config["configurable"])
    assert configurable["github_login"] == "mason-gh"
    # The thread is tagged with the login resolved from the Slack user id, so it
    # surfaces in the web Agents UI even when the Slack profile email does not
    # resolve to a mapping (login_for_email returns None in this harness).
    assert owner_meta["github_login"] == "mason-gh"
    assert "use_installation_token_fallback" not in configurable
    assert "prompt" not in captured


def test_process_slack_mention_bot_only_mode_runs_without_user_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In bot-token-only mode an unmapped user still gets a run (no blocking)."""
    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        return False

    async def fake_login_for_slack_id(slack_user_id):
        return None

    async def fake_login_for_email(email):
        return None

    monkeypatch.setattr(webhook_common, "_thread_exists", fake_thread_exists)
    monkeypatch.setattr(webhook_common, "login_for_slack_id", fake_login_for_slack_id)
    monkeypatch.setattr(webhook_common, "login_for_email", fake_login_for_email)
    monkeypatch.setattr(webhook_common, "is_bot_token_only_mode", lambda: True)

    asyncio.run(
        slack_webhooks.process_slack_mention(
            {
                "channel_id": "C123",
                "thread_ts": "1700000000.000100",
                "event_ts": "1700000000.000200",
                "user_id": "U123",
                "text": "<@UBOT> do the thing",
                "bot_user_id": "UBOT",
            },
            {"owner": "langchain-ai", "name": "open-swe"},
        )
    )

    assert "run_create" in captured
    assert "prompt" not in captured


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, url: str, **kwargs: object) -> _FakeResponse:
        return _FakeResponse(self._payload)


def test_get_slack_permalink_returns_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")
    link = "https://workspace.slack.com/archives/C123/p1700000000000100"
    monkeypatch.setattr(
        slack_utils.httpx,
        "AsyncClient",
        lambda *a, **k: _FakeAsyncClient({"ok": True, "permalink": link}),
    )

    result = asyncio.run(get_slack_permalink("C123", "1700000000.000100"))

    assert result == link


def test_get_slack_permalink_returns_none_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(
        slack_utils.httpx,
        "AsyncClient",
        lambda *a, **k: _FakeAsyncClient({"ok": False, "error": "message_not_found"}),
    )

    result = asyncio.run(get_slack_permalink("C123", "1700000000.000100"))

    assert result is None


def test_get_slack_permalink_without_token_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "")

    result = asyncio.run(get_slack_permalink("C123", "1700000000.000100"))

    assert result is None


def test_thread_environment_round_trips_through_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tagged opening message persists the environment; follow-ups read it back.

    Without this, a follow-up (which carries no `env:` tag) would resolve the
    default environment while reusing the sandbox built from the tagged one.
    """
    threads = _FakeThreadsClient({"metadata": {}})
    monkeypatch.setattr(webhook_common, "get_client", lambda url: _FakeClient(threads))

    asyncio.run(
        webhook_common.upsert_agent_thread_metadata(
            "thread-id",
            source="slack",
            environment="staging",
        )
    )
    assert threads.thread is not None
    assert threads.thread["metadata"]["environment"] == "staging"
    assert asyncio.run(webhook_common._get_thread_environment("thread-id")) == "staging"


def test_thread_environment_is_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    threads = _FakeThreadsClient({"metadata": {}})
    monkeypatch.setattr(webhook_common, "get_client", lambda url: _FakeClient(threads))
    assert asyncio.run(webhook_common._get_thread_environment("thread-id")) is None


def test_thread_environment_is_none_for_a_missing_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _FakeThreadsClient(raise_not_found=True)
    monkeypatch.setattr(webhook_common, "get_client", lambda url: _FakeClient(threads))
    assert asyncio.run(webhook_common._get_thread_environment("thread-id")) is None


def _context_input(messages: list[dict], **kwargs: object) -> list[str]:
    run_input = slack_webhooks._slack_context_input(
        messages,
        cast(dict, kwargs.get("user_names_by_id", {"U123": "Alice", "UBOT": "Open SWE"})),
        cast(dict, kwargs.get("logins_by_user_id", {})),
        channel_id="C123",
        bot_user_id="UBOT",
        event_ts="9.0",
        request_text="do the thing",
        request_blocks=[{"type": "text", "text": "do the thing"}],
        operational_context="## Open SWE Links",
    )
    return [cast(str, message["content"]) for message in run_input["messages"]]


def test_slack_context_attributes_own_replies_to_open_swe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open SWE posts with a bot token, so its replies carry `user` *and* `bot_id`."""
    monkeypatch.setattr(webhook_common, "SLACK_BOT_USERNAME", "Open SWE")
    contents = _context_input(
        [
            {"ts": "1.0", "text": "please fix it", "user": "U123"},
            {"ts": "1.1", "text": "on it", "user": "UBOT", "bot_id": "B1"},
            {"ts": "9.0", "text": "<@UBOT> do the thing", "user": "U123"},
        ]
    )

    own_reply = next(text for text in contents if "on it" in text)
    assert 'sender="system:open-swe"' in own_reply
    assert 'kind="system"' in own_reply
    assert any(
        '<dynamic-context kind="system"' in text and "<sender_type>self</sender_type>" in text
        for text in contents
    )
    assert not any('sender="slack:UBOT"' in text for text in contents)


def test_slack_context_marks_other_bots_as_bots() -> None:
    contents = _context_input(
        [
            {
                "ts": "1.0",
                "text": "build failed",
                "user": "UCI",
                "bot_id": "B9",
                "bot_profile": {"name": "CI Bot"},
            },
            {"ts": "9.0", "text": "<@UBOT> do the thing", "user": "U123"},
        ]
    )

    bot_message = next(text for text in contents if "build failed" in text)
    assert 'sender="system:slack-bot-B9"' in bot_message
    assert 'kind="system"' in bot_message
    intro = next(text for text in contents if 'id="system:slack-bot-B9"' in text)
    assert "<display_name>CI Bot</display_name>" in intro
    assert "<sender_type>bot</sender_type>" in intro


def test_slack_context_marks_people_without_an_open_swe_account() -> None:
    contents = _context_input(
        [
            {"ts": "1.0", "text": "hi", "user": "U123"},
            {"ts": "1.1", "text": "hello", "user": "U456"},
            {"ts": "9.0", "text": "<@UBOT> do the thing", "user": "U123"},
        ],
        user_names_by_id={"U123": "Alice", "U456": "Guest"},
        logins_by_user_id={"U123": "alice-gh"},
    )

    linked = next(text for text in contents if 'id="slack:U123"' in text)
    assert "<github_login>alice-gh</github_login>" in linked
    assert "<open_swe_account>linked</open_swe_account>" in linked
    unlinked = next(text for text in contents if 'id="slack:U456"' in text)
    assert "<open_swe_account>unlinked</open_swe_account>" in unlinked
    assert "github_login" not in unlinked


def test_format_slack_messages_for_prompt_labels_bots_and_self() -> None:
    formatted = format_slack_messages_for_prompt(
        [
            {"ts": "1.0", "text": "on it", "user": "UBOT", "bot_id": "B1"},
            {
                "ts": "1.1",
                "text": "build failed",
                "user": "UCI",
                "bot_id": "B9",
                "bot_profile": {"name": "CI Bot"},
            },
        ],
        {"UBOT": "Open SWE", "UCI": "CI Bot"},
        bot_user_id="UBOT",
        bot_username="Open SWE",
    )

    assert formatted == (
        "@Open SWE(self) [message_ts=1.0]: on it\n@CI Bot(bot) [message_ts=1.1]: build failed"
    )


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
    assert "<sender_type>bot</sender_type>" in intro


def test_format_slack_messages_for_prompt_does_not_label_a_lookalike_as_self() -> None:
    formatted = format_slack_messages_for_prompt(
        [{"ts": "1.0", "text": "impersonating", "bot_id": "B9", "username": "Open SWE"}],
        {},
        bot_user_id="UBOT",
        bot_username="Open SWE",
    )

    assert formatted == "@Open SWE(bot) [message_ts=1.0]: impersonating"


def _trigger_identities(
    messages: list[dict],
    *,
    event_ts: str,
    trigger_user_id: str,
    user_names_by_id: dict | None = None,
    logins_by_user_id: dict | None = None,
) -> list[str]:
    run_input = slack_webhooks._slack_context_input(
        messages,
        user_names_by_id if user_names_by_id is not None else {"U123": "Alice", "UBOT": "Open SWE"},
        logins_by_user_id if logins_by_user_id is not None else {},
        channel_id="C123",
        bot_user_id="UBOT",
        event_ts=event_ts,
        trigger_user_id=trigger_user_id,
        request_text="do the thing",
        request_blocks=[{"type": "text", "text": "do the thing"}],
        operational_context="## Open SWE Links",
    )
    return [cast(str, message["content"]) for message in run_input["messages"]]


def test_slack_trigger_resolves_from_the_triggering_user_not_the_event_ts() -> None:
    """A `message_changed` event's `event_ts` matches no message in the window.

    Keying the trigger off `event_ts` yields `slack:unknown`, so the agent is
    told the request came from nobody and the sender-context dedupe misses.
    """
    contents = _trigger_identities(
        [{"ts": "1.0", "text": "please fix it", "user": "U123"}],
        event_ts="9.5",
        trigger_user_id="U123",
        user_names_by_id={"U123": "Alice"},
        logins_by_user_id={"U123": "alice-gh"},
    )

    assert not any("slack:unknown" in text for text in contents)
    trigger = next(text for text in contents if 'id="slack:U123"' in text)
    assert "<display_name>Alice</display_name>" in trigger
    assert "<github_login>alice-gh</github_login>" in trigger


def test_slack_trigger_is_never_attributed_to_open_swe() -> None:
    """The approve-button path passes the ts of Open SWE's own button message.

    Matching that ts against thread history attributes the run to the bot as a
    person, so the prompt claims a human asked for the retry.
    """
    contents = _trigger_identities(
        [
            {"ts": "1.0", "text": "please fix it", "user": "U123"},
            {"ts": "9.0", "text": "Approve workflow push", "user": "UBOT", "bot_id": "B1"},
        ],
        event_ts="9.0",
        trigger_user_id="U123",
        user_names_by_id={"U123": "Alice", "UBOT": "Open SWE"},
    )

    assert not any('id="slack:UBOT"' in text for text in contents)
    assert not any('sender="slack:UBOT"' in text for text in contents)
    assert any('id="slack:U123"' in text for text in contents)


def test_process_slack_mention_queues_a_message_edit_instead_of_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An edit is a correction to work already in flight, not a new request."""
    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        return True

    async def fake_queue_message_for_thread(thread_id: str, content: object) -> bool:
        captured["queued"] = {"thread_id": thread_id, "content": content}
        return True

    monkeypatch.setattr(webhook_common, "_thread_exists", fake_thread_exists)
    monkeypatch.setattr(slack_webhooks, "queue_message_for_thread", fake_queue_message_for_thread)

    asyncio.run(
        slack_webhooks.process_slack_mention(
            {
                "channel_id": "C123",
                "thread_ts": "1700000000.000100",
                "event_ts": "1700000000.000300",
                "user_id": "U123",
                "text": "<@UBOT> actually use PR 5889",
                "bot_user_id": "UBOT",
                "message_update": True,
            },
            {"owner": "langchain-ai", "name": "open-swe"},
        )
    )

    assert "run_create" not in captured
    queued = captured["queued"]
    assert isinstance(queued, dict)
    assert queued["thread_id"] == "mapped-thread"
    blocks = cast(list, queued["content"])
    text = "\n".join(
        block["text"] for block in blocks if isinstance(block, dict) and block.get("text")
    )
    assert "actually use PR 5889" in text
    assert "edited" in text


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

    monkeypatch.setattr(webhook_common, "_thread_exists", fake_thread_exists)
    monkeypatch.setattr(slack_webhooks, "queue_message_for_thread", fake_queue_message_for_thread)

    asyncio.run(
        slack_webhooks.process_slack_mention(
            {
                "channel_id": "C123",
                "thread_ts": "1700000000.000100",
                "event_ts": "1700000000.000300",
                "user_id": "U123",
                "text": "<@UBOT> actually use PR 5889",
                "bot_user_id": "UBOT",
                "message_update": True,
            },
            {"owner": "langchain-ai", "name": "open-swe"},
        )
    )

    run_create = captured["run_create"]
    assert isinstance(run_create, dict)
    assert run_create["kwargs"]["multitask_strategy"] == "enqueue"
