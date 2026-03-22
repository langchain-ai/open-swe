import asyncio

import pytest

from agent import webapp
from agent.utils.slack import (
    format_slack_messages_for_prompt,
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


def test_get_slack_repo_config_message_repo_overrides_existing_thread_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}
    threads_client = _FakeThreadsClient(
        thread={"metadata": {"repo": {"owner": "saved-owner", "name": "saved-repo"}}}
    )

    async def fake_post_slack_thread_reply(channel_id: str, thread_ts: str, text: str) -> bool:
        captured["channel_id"] = channel_id
        captured["thread_ts"] = thread_ts
        captured["text"] = text
        return True

    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeClient(threads_client))
    monkeypatch.setattr(webapp, "post_slack_thread_reply", fake_post_slack_thread_reply)

    repo = asyncio.run(
        webapp.get_slack_repo_config("please use repo:new-owner/new-repo", "C123", "1.234")
    )

    assert repo == {"owner": "new-owner", "name": "new-repo"}
    assert threads_client.requested_thread_id is None
    assert captured["text"] == "Using repository: `new-owner/new-repo`"


def test_get_slack_repo_config_parses_message_for_new_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads_client = _FakeThreadsClient(raise_not_found=True)

    async def fake_post_slack_thread_reply(channel_id: str, thread_ts: str, text: str) -> bool:
        return True

    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeClient(threads_client))
    monkeypatch.setattr(webapp, "post_slack_thread_reply", fake_post_slack_thread_reply)

    repo = asyncio.run(
        webapp.get_slack_repo_config("please use repo:new-owner/new-repo", "C123", "1.234")
    )

    assert repo == {"owner": "new-owner", "name": "new-repo"}


def test_get_slack_repo_config_existing_thread_without_repo_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads_client = _FakeThreadsClient(thread={"metadata": {}})
    monkeypatch.setattr(webapp, "SLACK_REPO_OWNER", "default-owner")
    monkeypatch.setattr(webapp, "SLACK_REPO_NAME", "default-repo")

    async def fake_post_slack_thread_reply(channel_id: str, thread_ts: str, text: str) -> bool:
        return True

    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeClient(threads_client))
    monkeypatch.setattr(webapp, "post_slack_thread_reply", fake_post_slack_thread_reply)

    repo = asyncio.run(webapp.get_slack_repo_config("please help", "C123", "1.234"))

    assert repo == {"owner": "default-owner", "name": "default-repo"}
    assert threads_client.requested_thread_id == generate_thread_id_from_slack_thread(
        "C123", "1.234"
    )


def test_get_slack_repo_config_space_syntax_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """repo owner/name (space instead of colon) should be detected correctly."""
    threads_client = _FakeThreadsClient(raise_not_found=True)

    async def fake_post_slack_thread_reply(channel_id: str, thread_ts: str, text: str) -> bool:
        return True

    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeClient(threads_client))
    monkeypatch.setattr(webapp, "post_slack_thread_reply", fake_post_slack_thread_reply)

    repo = asyncio.run(
        webapp.get_slack_repo_config(
            "please fix the bug in repo langchain-ai/langchainjs", "C123", "1.234"
        )
    )

    assert repo == {"owner": "langchain-ai", "name": "langchainjs"}


def test_get_slack_repo_config_github_url_extracted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitHub URL in message should be used to detect the repo."""
    threads_client = _FakeThreadsClient(raise_not_found=True)

    async def fake_post_slack_thread_reply(channel_id: str, thread_ts: str, text: str) -> bool:
        return True

    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeClient(threads_client))
    monkeypatch.setattr(webapp, "post_slack_thread_reply", fake_post_slack_thread_reply)

    repo = asyncio.run(
        webapp.get_slack_repo_config(
            "I found a bug in https://github.com/langchain-ai/langgraph-api please fix it",
            "C123",
            "1.234",
        )
    )

    assert repo == {"owner": "langchain-ai", "name": "langgraph-api"}


def test_get_slack_repo_config_explicit_repo_beats_github_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit repo: syntax takes priority over a GitHub URL also present in the message."""
    threads_client = _FakeThreadsClient(raise_not_found=True)

    async def fake_post_slack_thread_reply(channel_id: str, thread_ts: str, text: str) -> bool:
        return True

    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeClient(threads_client))
    monkeypatch.setattr(webapp, "post_slack_thread_reply", fake_post_slack_thread_reply)

    repo = asyncio.run(
        webapp.get_slack_repo_config(
            "see https://github.com/langchain-ai/langgraph-api but use repo:my-org/my-repo",
            "C123",
            "1.234",
        )
    )

    assert repo == {"owner": "my-org", "name": "my-repo"}


def test_get_slack_repo_config_explicit_space_syntax_beats_thread_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit repo owner/name (space syntax) takes priority over saved thread metadata."""
    threads_client = _FakeThreadsClient(
        thread={"metadata": {"repo": {"owner": "saved-owner", "name": "saved-repo"}}}
    )

    async def fake_post_slack_thread_reply(channel_id: str, thread_ts: str, text: str) -> bool:
        return True

    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeClient(threads_client))
    monkeypatch.setattr(webapp, "post_slack_thread_reply", fake_post_slack_thread_reply)

    repo = asyncio.run(
        webapp.get_slack_repo_config(
            "actually use repo langchain-ai/langchainjs today", "C123", "1.234"
        )
    )

    assert repo == {"owner": "langchain-ai", "name": "langchainjs"}


def test_get_slack_repo_config_github_url_beats_thread_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GitHub URL in the message takes priority over saved thread metadata."""
    threads_client = _FakeThreadsClient(
        thread={"metadata": {"repo": {"owner": "saved-owner", "name": "saved-repo"}}}
    )

    async def fake_post_slack_thread_reply(channel_id: str, thread_ts: str, text: str) -> bool:
        return True

    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeClient(threads_client))
    monkeypatch.setattr(webapp, "post_slack_thread_reply", fake_post_slack_thread_reply)

    repo = asyncio.run(
        webapp.get_slack_repo_config(
            "I found a bug in https://github.com/langchain-ai/langgraph-api",
            "C123",
            "1.234",
        )
    )

    assert repo == {"owner": "langchain-ai", "name": "langgraph-api"}


def test_get_slack_repo_config_repo_name_only_defaults_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """repo:name without org should default owner to langchain-ai."""
    threads_client = _FakeThreadsClient(raise_not_found=True)

    async def fake_post_slack_thread_reply(channel_id: str, thread_ts: str, text: str) -> bool:
        return True

    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeClient(threads_client))
    monkeypatch.setattr(webapp, "post_slack_thread_reply", fake_post_slack_thread_reply)

    repo = asyncio.run(
        webapp.get_slack_repo_config("fix bug in repo:langchainplus", "C123", "1.234")
    )

    assert repo == {"owner": "langchain-ai", "name": "langchainplus"}


def test_get_slack_repo_config_repo_name_only_space_syntax(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """repo name (space syntax, no org) should default owner to langchain-ai."""
    threads_client = _FakeThreadsClient(raise_not_found=True)

    async def fake_post_slack_thread_reply(channel_id: str, thread_ts: str, text: str) -> bool:
        return True

    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeClient(threads_client))
    monkeypatch.setattr(webapp, "post_slack_thread_reply", fake_post_slack_thread_reply)

    repo = asyncio.run(webapp.get_slack_repo_config("fix bug in repo open-swe", "C123", "1.234"))

    assert repo == {"owner": "langchain-ai", "name": "open-swe"}
