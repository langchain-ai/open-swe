import asyncio

import pytest

from agent import webapp
from agent.utils.slack import (
    extract_slack_message_links,
    fetch_slack_messages_from_links,
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


# ---------------------------------------------------------------------------
# extract_slack_message_links tests
# ---------------------------------------------------------------------------


def test_extract_slack_message_links_single_message() -> None:
    text = "Check this: https://myorg.slack.com/archives/C08UK957CMC/p1773090218882099"
    links = extract_slack_message_links(text)
    assert len(links) == 1
    assert links[0]["channel_id"] == "C08UK957CMC"
    assert links[0]["message_ts"] == "1773090218.882099"
    assert links[0]["thread_ts"] == ""


def test_extract_slack_message_links_threaded_message() -> None:
    text = (
        "See https://langchain.slack.com/archives/C08UK957CMC/p1773090218882099"
        "?thread_ts=1773090000.100000&cid=C08UK957CMC"
    )
    links = extract_slack_message_links(text)
    assert len(links) == 1
    assert links[0]["channel_id"] == "C08UK957CMC"
    assert links[0]["message_ts"] == "1773090218.882099"
    assert links[0]["thread_ts"] == "1773090000.100000"


def test_extract_slack_message_links_multiple() -> None:
    text = (
        "First: https://org.slack.com/archives/C111/p1000000000000001 "
        "Second: https://org.slack.com/archives/C222/p2000000000000002"
    )
    links = extract_slack_message_links(text)
    assert len(links) == 2
    assert links[0]["channel_id"] == "C111"
    assert links[1]["channel_id"] == "C222"


def test_extract_slack_message_links_deduplicates() -> None:
    url = "https://org.slack.com/archives/C111/p1000000000000001"
    text = f"{url} and again {url}"
    links = extract_slack_message_links(text)
    assert len(links) == 1


def test_extract_slack_message_links_no_links() -> None:
    assert extract_slack_message_links("no links here") == []
    assert extract_slack_message_links("") == []


def test_extract_slack_message_links_in_angle_brackets() -> None:
    text = "Check <https://myorg.slack.com/archives/C08UK957CMC/p1773090218882099>"
    links = extract_slack_message_links(text)
    assert len(links) == 1
    assert links[0]["channel_id"] == "C08UK957CMC"


# ---------------------------------------------------------------------------
# fetch_slack_messages_from_links tests
# ---------------------------------------------------------------------------


def test_fetch_slack_messages_from_links_no_links() -> None:
    result = asyncio.run(fetch_slack_messages_from_links("no links here"))
    assert result == ""


def test_fetch_slack_messages_from_links_fetches_and_formats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.utils.slack as slack_mod

    async def fake_fetch_single(channel_id: str, message_ts: str, thread_ts: str = "") -> dict:
        return {"user": "U123", "text": "Hello from linked message", "ts": message_ts}

    async def fake_get_user_names(user_ids: list[str]) -> dict[str, str]:
        return {"U123": "alice"}

    monkeypatch.setattr(slack_mod, "_fetch_single_slack_message", fake_fetch_single)
    monkeypatch.setattr(slack_mod, "get_slack_user_names", fake_get_user_names)

    text = "See https://org.slack.com/archives/C111/p1000000000000001"
    result = asyncio.run(fetch_slack_messages_from_links(text))
    assert "@alice: Hello from linked message" in result
    assert "Slack message from" in result


def test_fetch_slack_messages_from_links_handles_fetch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.utils.slack as slack_mod

    async def fake_fetch_single(channel_id: str, message_ts: str, thread_ts: str = "") -> None:
        return None

    monkeypatch.setattr(slack_mod, "_fetch_single_slack_message", fake_fetch_single)

    text = "See https://org.slack.com/archives/C111/p1000000000000001"
    result = asyncio.run(fetch_slack_messages_from_links(text))
    assert result == ""
