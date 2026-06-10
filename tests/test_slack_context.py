import asyncio

import pytest

from agent import webapp
from agent.utils import slack as slack_utils
from agent.utils.slack import (
    TRACE_REPLY_TIPS,
    convert_mentions_to_slack_format,
    format_slack_messages_for_prompt,
    parse_github_pr_url,
    post_slack_trace_reply,
    replace_bot_mention_with_username,
    select_slack_context_messages,
    strip_bot_mention,
)
from agent.webapp import generate_thread_id_from_slack_thread


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


class _FakeClient:
    def __init__(self, threads_client: _FakeThreadsClient) -> None:
        self.threads = threads_client


def test_generate_thread_id_from_slack_thread_is_deterministic() -> None:
    channel_id = "C12345"
    thread_ts = "1730900000.123456"
    first = generate_thread_id_from_slack_thread(channel_id, thread_ts)
    second = generate_thread_id_from_slack_thread(channel_id, thread_ts)
    assert first == second
    assert len(first) == 36


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


def test_format_slack_messages_for_prompt_uses_name_and_id() -> None:
    formatted = format_slack_messages_for_prompt(
        [{"ts": "1.0", "text": "hello", "user": "U123"}],
        {"U123": "alice"},
    )

    assert formatted == "@alice(U123): hello"


def test_format_slack_messages_for_prompt_replaces_bot_id_mention_in_text() -> None:
    formatted = format_slack_messages_for_prompt(
        [{"ts": "1.0", "text": "<@UBOT> status update?", "user": "U123"}],
        {"U123": "alice"},
        bot_user_id="UBOT",
        bot_username="open-swe",
    )

    assert formatted == "@alice(U123): @open-swe status update?"


def test_post_slack_trace_reply_includes_web_link_without_trace_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted: list[dict] = []

    async def fake_post_slack_thread_reply_with_ts(
        channel_id: str,
        thread_ts: str,
        text: str,
        *,
        unfurl_links: bool = True,
        unfurl_media: bool = True,
    ) -> tuple[str | None, str | None]:
        posted.append({"text": text, "unfurl_links": unfurl_links, "unfurl_media": unfurl_media})
        return "1.1", None

    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com/")
    monkeypatch.setattr(
        slack_utils, "post_slack_thread_reply_with_ts", fake_post_slack_thread_reply_with_ts
    )
    monkeypatch.setattr(slack_utils, "get_langsmith_trace_url", lambda thread_id: None)

    asyncio.run(post_slack_trace_reply("C123", "1.0", "thread-id"))

    assert len(posted) == 1
    text = posted[0]["text"]
    head, _, tip_line = text.partition("\n")
    assert head == "<https://app.example.com/agents/thread-id|Open in Web>"
    assert tip_line.startswith("_Tip: ") and tip_line.endswith("_")
    assert any(tip in tip_line for tip in TRACE_REPLY_TIPS)
    assert posted[0]["unfurl_links"] is False
    assert posted[0]["unfurl_media"] is False


def test_post_slack_trace_reply_includes_trace_link_and_tip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted: list[dict] = []

    async def fake_post_slack_thread_reply_with_ts(
        channel_id: str,
        thread_ts: str,
        text: str,
        *,
        unfurl_links: bool = True,
        unfurl_media: bool = True,
    ) -> tuple[str | None, str | None]:
        posted.append({"text": text, "unfurl_links": unfurl_links, "unfurl_media": unfurl_media})
        return "1.1", None

    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(
        slack_utils, "post_slack_thread_reply_with_ts", fake_post_slack_thread_reply_with_ts
    )
    monkeypatch.setattr(slack_utils, "get_langsmith_trace_url", lambda thread_id: "https://smith/x")

    asyncio.run(post_slack_trace_reply("C123", "1.0", "thread-id"))

    assert len(posted) == 1
    text = posted[0]["text"]
    head, _, tip_line = text.partition("\n")
    assert (
        head
        == "<https://smith/x|View trace> • <https://app.example.com/agents/thread-id|Open in Web>"
    )
    assert tip_line.startswith("_Tip: ") and tip_line.endswith("_")
    assert any(tip in tip_line for tip in TRACE_REPLY_TIPS)
    assert posted[0]["unfurl_links"] is False
    assert posted[0]["unfurl_media"] is False


def test_post_slack_trace_reply_can_skip_web_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted: list[dict] = []

    async def fake_post_slack_thread_reply_with_ts(
        channel_id: str,
        thread_ts: str,
        text: str,
        *,
        unfurl_links: bool = True,
        unfurl_media: bool = True,
    ) -> tuple[str | None, str | None]:
        posted.append({"text": text, "unfurl_links": unfurl_links, "unfurl_media": unfurl_media})
        return "1.1", None

    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(
        slack_utils, "post_slack_thread_reply_with_ts", fake_post_slack_thread_reply_with_ts
    )
    monkeypatch.setattr(slack_utils, "get_langsmith_trace_url", lambda thread_id: "https://smith/x")

    asyncio.run(
        post_slack_trace_reply("C123", "1.0", "reviewer-thread-id", include_dashboard_link=False)
    )

    assert len(posted) == 1
    head, _, tip_line = posted[0]["text"].partition("\n")
    assert head == "<https://smith/x|View trace>"
    assert "Open in Web" not in posted[0]["text"]
    assert tip_line.startswith("_Tip: ") and tip_line.endswith("_")
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

    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeClient(threads_client))
    monkeypatch.setattr(
        webapp, "post_slack_thread_reply", fake_post_slack_thread_reply, raising=False
    )

    repo = asyncio.run(webapp.get_slack_repo_config("C123", "1.234"))

    assert repo == {"owner": "saved-owner", "name": "saved-repo"}
    assert threads_client.requested_thread_id == generate_thread_id_from_slack_thread(
        "C123", "1.234"
    )
    assert not posted


async def _no_team_default_repo() -> dict[str, str] | None:
    return None


def test_get_slack_repo_config_new_thread_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads_client = _FakeThreadsClient(raise_not_found=True)
    monkeypatch.setattr(webapp, "SLACK_REPO_OWNER", "default-owner")
    monkeypatch.setattr(webapp, "SLACK_REPO_NAME", "default-repo")
    monkeypatch.setattr(webapp, "get_team_default_repo", _no_team_default_repo)

    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeClient(threads_client))

    repo = asyncio.run(webapp.get_slack_repo_config("C123", "1.234"))

    assert repo == {"owner": "default-owner", "name": "default-repo"}


def test_get_slack_repo_config_existing_thread_without_repo_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads_client = _FakeThreadsClient(thread={"metadata": {}})
    monkeypatch.setattr(webapp, "SLACK_REPO_OWNER", "default-owner")
    monkeypatch.setattr(webapp, "SLACK_REPO_NAME", "default-repo")
    monkeypatch.setattr(webapp, "get_team_default_repo", _no_team_default_repo)

    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeClient(threads_client))

    repo = asyncio.run(webapp.get_slack_repo_config("C123", "1.234"))

    assert repo == {"owner": "default-owner", "name": "default-repo"}
    assert threads_client.requested_thread_id == generate_thread_id_from_slack_thread(
        "C123", "1.234"
    )


def test_get_slack_repo_config_ignores_repo_syntax_in_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads_client = _FakeThreadsClient(
        thread={"metadata": {"repo": {"owner": "saved-owner", "name": "saved-repo"}}}
    )

    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeClient(threads_client))

    repo = asyncio.run(webapp.get_slack_repo_config("C123", "1.234"))

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

    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeClient(threads_client))
    monkeypatch.setattr(webapp, "get_slack_user_info", fake_get_slack_user_info)
    monkeypatch.setattr(
        webapp, "resolve_login_from_email_async", fake_resolve_login_from_email_async
    )
    monkeypatch.setattr(webapp, "get_profile_default_repo", fake_get_profile_default_repo)

    repo = asyncio.run(webapp.get_slack_repo_config("C123", "1.234", slack_user_id="U123"))

    assert repo == {"owner": "profile-owner", "name": "profile-repo"}


def test_get_slack_repo_config_applies_team_default_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads_client = _FakeThreadsClient(thread={"metadata": {}})

    async def fake_get_team_default_repo() -> dict[str, str] | None:
        return {"owner": "team-owner", "name": "team-repo"}

    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeClient(threads_client))
    monkeypatch.setattr(webapp, "get_team_default_repo", fake_get_team_default_repo)
    monkeypatch.setattr(webapp, "SLACK_REPO_NAME", "")
    monkeypatch.setattr(webapp, "DEFAULT_REPO_NAME", "")

    repo = asyncio.run(webapp.get_slack_repo_config("C123", "1.234"))

    assert repo == {"owner": "team-owner", "name": "team-repo"}


def _setup_slack_mention_fakes(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, object]
) -> None:
    async def fake_get_slack_user_info(user_id: str) -> dict:
        return {
            "profile": {
                "email": "mason@example.com",
                "display_name": "Mason",
            }
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

    async def fake_is_thread_active(thread_id: str) -> bool:
        captured["active_thread_id"] = thread_id
        return False

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

    monkeypatch.setattr(webapp, "SLACK_BOT_USERNAME", "open-swe")
    monkeypatch.setattr(webapp, "get_slack_user_info", fake_get_slack_user_info)
    monkeypatch.setattr(webapp, "fetch_slack_thread_messages", fake_fetch_slack_thread_messages)
    monkeypatch.setattr(webapp, "get_slack_user_names", fake_get_slack_user_names)
    monkeypatch.setattr(
        webapp, "resolve_slack_links_in_context", fake_resolve_slack_links_in_context
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

    monkeypatch.setattr(webapp, "is_thread_active", fake_is_thread_active)
    monkeypatch.setattr(webapp, "post_slack_trace_reply", fake_post_slack_trace_reply)
    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeLangGraphClientForProcess())
    monkeypatch.setattr(webapp, "login_for_slack_id", fake_login_for_slack_id)
    monkeypatch.setattr(webapp, "login_for_email", fake_login_for_email)
    monkeypatch.setattr(webapp, "refresh_user_mapping_cache", fake_refresh_cache)
    monkeypatch.setattr(webapp, "get_valid_access_token", fake_get_valid_access_token)
    monkeypatch.setattr(webapp, "_post_account_link_prompt", fake_post_prompt)


def test_process_slack_mention_creates_thread_first_run_with_trace_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        captured["thread_exists_check"] = thread_id
        return False

    monkeypatch.setattr(webapp, "_thread_exists", fake_thread_exists)

    thread_ts = "1700000000.000100"
    event_ts = "1700000000.000200"
    expected_thread_id = generate_thread_id_from_slack_thread("C123", thread_ts)

    asyncio.run(
        webapp.process_slack_mention(
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
    assert captured["active_thread_id"] == expected_thread_id
    assert captured["metadata_update"] == {
        "thread_id": expected_thread_id,
        "metadata": {"repo": {"owner": "langchain-ai", "name": "open-swe"}},
    }
    assert captured["trace_reply"] == {
        "channel_id": "C123",
        "thread_ts": thread_ts,
        "thread_id": expected_thread_id,
    }

    run_create = captured["run_create"]
    assert isinstance(run_create, dict)
    assert run_create["thread_id"] == expected_thread_id
    assert run_create["graph"] == "agent"
    kwargs = run_create["kwargs"]
    assert kwargs["if_not_exists"] == "create"
    assert "multitask_strategy" not in kwargs
    assert kwargs["config"]["configurable"]["slack_thread"]["thread_ts"] == thread_ts
    prompt_block = kwargs["input"]["messages"][0]["content"][0]
    assert "## Default Repository Hint\nlangchain-ai/open-swe" in prompt_block["text"]
    assert (
        "Use this only if the Slack conversation does not identify a different repository."
        in (prompt_block["text"])
    )
    assert prompt_block["text"].count("## Slack Thread") == 1
    assert f"Thread TS: {thread_ts}" in prompt_block["text"]
    assert "## Latest Mention Request\ncontinue on the branch" in prompt_block["text"]


def test_process_slack_mention_skips_trace_reply_on_followup_mention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subsequent mentions in a Slack thread should not post 'Working on it!'."""
    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        captured["thread_exists_check"] = thread_id
        return True

    monkeypatch.setattr(webapp, "_thread_exists", fake_thread_exists)

    thread_ts = "1700000000.000100"
    event_ts = "1700000000.000300"
    expected_thread_id = generate_thread_id_from_slack_thread("C123", thread_ts)

    asyncio.run(
        webapp.process_slack_mention(
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


def test_process_slack_mention_queues_active_thread_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_get_slack_user_info(user_id: str) -> dict:
        return {
            "profile": {
                "email": "mason@example.com",
                "display_name": "Mason",
            }
        }

    async def fake_fetch_slack_thread_messages(channel_id: str, thread_ts: str) -> list[dict]:
        return [
            {"ts": "1700000000.000100", "text": "<@UBOT> first request", "user": "U123"},
            {
                "ts": "1700000000.000200",
                "text": "<@UBOT> include this screenshot https://example.com/image.png",
                "user": "U123",
            },
        ]

    async def fake_get_slack_user_names(user_ids: list[str]) -> dict[str, str]:
        captured["user_ids"] = user_ids
        return {"U123": "Mason"}

    async def fake_resolve_slack_links_in_context(
        context_messages: list[dict], user_names_by_id: dict[str, str]
    ) -> tuple[str, list[str]]:
        captured["context_messages"] = context_messages
        return "", []

    async def fake_fetch_image_block(image_url: str, http_client: object) -> None:
        captured["image_url"] = image_url
        return None

    async def fake_is_thread_active(thread_id: str) -> bool:
        captured["active_thread_id"] = thread_id
        return True

    async def fake_queue_message_for_thread(thread_id: str, message_content: object) -> bool:
        captured["queued"] = {"thread_id": thread_id, "message_content": message_content}
        return True

    async def fake_post_slack_trace_reply(*args, **kwargs) -> None:
        raise AssertionError("trace reply should not be posted for queued mid-run Slack messages")

    async def fake_thread_exists(thread_id: str) -> bool:
        return True

    class _FakeRunsClient:
        async def create(self, *args, **kwargs) -> None:
            raise AssertionError("run should not be created for active Slack threads")

    class _FakeThreadsClientForProcess:
        async def update(self, *, thread_id: str, metadata: dict) -> None:
            captured["metadata_update"] = {"thread_id": thread_id, "metadata": metadata}

    class _FakeLangGraphClientForProcess:
        runs = _FakeRunsClient()
        threads = _FakeThreadsClientForProcess()

    monkeypatch.setattr(webapp, "SLACK_BOT_USERNAME", "open-swe")
    monkeypatch.setattr(webapp, "get_slack_user_info", fake_get_slack_user_info)
    monkeypatch.setattr(webapp, "fetch_slack_thread_messages", fake_fetch_slack_thread_messages)
    monkeypatch.setattr(webapp, "get_slack_user_names", fake_get_slack_user_names)
    monkeypatch.setattr(
        webapp, "resolve_slack_links_in_context", fake_resolve_slack_links_in_context
    )
    monkeypatch.setattr(webapp, "fetch_image_block", fake_fetch_image_block)
    monkeypatch.setattr(webapp, "is_thread_active", fake_is_thread_active)
    monkeypatch.setattr(webapp, "queue_message_for_thread", fake_queue_message_for_thread)
    monkeypatch.setattr(webapp, "post_slack_trace_reply", fake_post_slack_trace_reply)
    monkeypatch.setattr(webapp, "_thread_exists", fake_thread_exists)
    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeLangGraphClientForProcess())

    async def fake_login_for_slack_id(slack_user_id):
        return "mason-gh"

    async def fake_login_for_email(email):
        return None

    async def fake_refresh_cache() -> list:
        return []

    async def fake_get_valid_access_token(login):
        return "user-token"

    monkeypatch.setattr(webapp, "login_for_slack_id", fake_login_for_slack_id)
    monkeypatch.setattr(webapp, "login_for_email", fake_login_for_email)
    monkeypatch.setattr(webapp, "refresh_user_mapping_cache", fake_refresh_cache)
    monkeypatch.setattr(webapp, "get_valid_access_token", fake_get_valid_access_token)

    thread_ts = "1700000000.000100"
    event_ts = "1700000000.000200"
    expected_thread_id = generate_thread_id_from_slack_thread("C123", thread_ts)

    asyncio.run(
        webapp.process_slack_mention(
            {
                "channel_id": "C123",
                "thread_ts": thread_ts,
                "event_ts": event_ts,
                "user_id": "U123",
                "text": "<@UBOT> include this screenshot https://example.com/image.png",
                "bot_user_id": "UBOT",
            },
            {"owner": "langchain-ai", "name": "open-swe"},
        )
    )

    assert captured["active_thread_id"] == expected_thread_id
    assert captured["queued"]["thread_id"] == expected_thread_id
    queued_payload = captured["queued"]["message_content"]
    assert queued_payload["image_urls"] == ["https://example.com/image.png"]
    assert "## Latest Mention Request\ninclude this screenshot" in queued_payload["text"]


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

    async def fake_post_prompt(channel_id, thread_ts, user_id, user_email, reason="unlinked"):
        captured["prompt"] = {"user_id": user_id, "user_email": user_email, "reason": reason}

    monkeypatch.setattr(webapp, "_thread_exists", fake_thread_exists)
    monkeypatch.setattr(webapp, "login_for_slack_id", fake_login_for_slack_id)
    monkeypatch.setattr(webapp, "login_for_email", fake_login_for_email)
    monkeypatch.setattr(webapp, "_post_account_link_prompt", fake_post_prompt)

    asyncio.run(
        webapp.process_slack_mention(
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

    async def fake_post_prompt(channel_id, thread_ts, user_id, user_email, reason="unlinked"):
        captured["prompt"] = {"reason": reason}

    monkeypatch.setattr(webapp, "_thread_exists", fake_thread_exists)
    monkeypatch.setattr(webapp, "login_for_slack_id", fake_login_for_slack_id)
    monkeypatch.setattr(webapp, "get_valid_access_token", fake_get_valid_access_token)
    monkeypatch.setattr(webapp, "has_access_token_record", fake_has_token_record)
    monkeypatch.setattr(webapp, "_post_account_link_prompt", fake_post_prompt)

    asyncio.run(
        webapp.process_slack_mention(
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

    async def fake_post_prompt(channel_id, thread_ts, user_id, user_email, reason="unlinked"):
        captured["prompt"] = {"reason": reason}

    monkeypatch.setattr(webapp, "_thread_exists", fake_thread_exists)
    monkeypatch.setattr(webapp, "login_for_slack_id", fake_login_for_slack_id)
    monkeypatch.setattr(webapp, "get_valid_access_token", fake_get_valid_access_token)
    monkeypatch.setattr(webapp, "has_access_token_record", fake_has_token_record)
    monkeypatch.setattr(webapp, "_post_account_link_prompt", fake_post_prompt)

    asyncio.run(
        webapp.process_slack_mention(
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

    monkeypatch.setattr(webapp, "_thread_exists", fake_thread_exists)
    monkeypatch.setattr(webapp, "login_for_slack_id", fake_login_for_slack_id)
    monkeypatch.setattr(webapp, "upsert_agent_thread_owner_metadata", fake_upsert_owner)

    asyncio.run(
        webapp.process_slack_mention(
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
    configurable = run_create["kwargs"]["config"]["configurable"]
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

    monkeypatch.setattr(webapp, "_thread_exists", fake_thread_exists)
    monkeypatch.setattr(webapp, "login_for_slack_id", fake_login_for_slack_id)
    monkeypatch.setattr(webapp, "login_for_email", fake_login_for_email)
    monkeypatch.setattr(webapp, "is_bot_token_only_mode", lambda: True)

    asyncio.run(
        webapp.process_slack_mention(
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
