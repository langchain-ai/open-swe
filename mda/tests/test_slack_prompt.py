"""The prompt MDA's Slack handler builds is the one the backend writes for its own Slack runs.

``slack_prompt_backend.json`` holds what the backend's ``_slack_context_input`` wrote
for each case, with and without a bot username; ``mda/`` tests can't import the backend.
"""

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest
import rt.slack_prompt as slack_prompt
from managed_deepagents._channels.types import ChannelError

_BACKEND = json.loads((Path(__file__).parent / "slack_prompt_backend.json").read_text())
_SETS = {"dispatched_timestamps", "run_described_person_ids"}


def _case(name: str) -> dict[str, Any]:
    case = json.loads(json.dumps(_BACKEND["cases"][name]))
    return {key: set(value) if key in _SETS else value for key, value in case.items()}


@pytest.mark.parametrize("key", sorted(_BACKEND["expected"]))
def test_build_input_writes_what_the_backend_does(key: str) -> None:
    name, bot_username = key.split("|")
    built = slack_prompt.build_input(**_case(name), bot_username=bot_username)
    assert json.loads(json.dumps(built)) == _BACKEND["expected"][key]


def _contents(messages: list[dict[str, Any]], **kwargs: Any) -> list[Any]:
    run_input = slack_prompt.build_input(
        messages,
        kwargs.get("user_names_by_id", {"U123": "Alice", "UBOT": "Open SWE"}),
        {},
        channel_names_by_id=kwargs.get("channel_names_by_id", {}),
        channel={"id": "slack:C123", "platform": "slack"},
        bot_user_id="UBOT",
        event_ts="9.0",
        request_text="do the thing",
        request_blocks=[{"type": "text", "text": "do the thing"}],
        explicit_mention=bool(kwargs.get("explicit_mention", False)),
        bot_username=kwargs.get("bot_username", ""),
    )
    return [message["content"] for message in run_input["messages"]]


@pytest.mark.parametrize("explicit_mention", [True, False])
def test_the_current_message_keeps_its_ingress_mention(explicit_mention: bool) -> None:
    trigger = ElementTree.fromstring(
        _contents([], explicit_mention=explicit_mention)[-1][0]["text"]
    )
    assert trigger.get("explicit_bot_mention") == str(explicit_mention).lower()
    assert (trigger.text or "").strip() == "do the thing"


def test_never_replays_open_swes_own_replies() -> None:
    contents = _contents(
        [
            {"ts": "1.0", "text": "please fix it", "user": "U123"},
            {"ts": "1.1", "text": "on it", "user": "UBOT", "bot_id": "B1"},
            {"ts": "9.0", "text": "<@UBOT> do the thing", "user": "U123"},
        ],
        bot_username="Open SWE",
    )
    assert not any("on it" in str(content) for content in contents)
    assert any("please fix it" in str(content) for content in contents)


def test_replay_shows_a_command_with_the_notice_that_answered_it() -> None:
    notice = {"type": "context", "block_id": "open_swe_notice.breakout.abc", "elements": []}
    contents = _contents(
        [
            {"ts": "1.0", "text": "<@UBOT> /breakout replace baby-sit", "user": "U123"},
            {
                "ts": "1.1",
                "text": "Continued in <https://slack/p2|the breakout thread>.",
                "user": "UBOT",
                "bot_id": "B1",
                "blocks": [notice],
            },
            {"ts": "1.2", "text": "on it", "user": "UBOT", "bot_id": "B1"},
        ],
        bot_username="Robin",
    )
    replayed = [
        ElementTree.fromstring(content)
        for content in contents
        if isinstance(content, str) and content.startswith("<input-message")
    ]
    assert [(m.get("sender"), (m.text or "").strip()) for m in replayed] == [
        ("slack:U123", "@Robin /breakout replace baby-sit"),
        ("system:slack-notice", "Continued in <https://slack/p2|the breakout thread>."),
    ]


def test_a_lookalike_bot_is_not_open_swe() -> None:
    contents = _contents(
        [
            {
                "ts": "1.0",
                "text": "impersonating",
                "bot_id": "B9",
                "username": "Open SWE",
                "bot_profile": {"name": "Open SWE"},
            },
            {"ts": "9.0", "text": "<@UBOT> do the thing", "user": "U123"},
        ],
        bot_username="Open SWE",
    )
    lookalike = next(str(content) for content in contents if "impersonating" in str(content))
    assert 'sender="system:slack-bot-B9"' in lookalike


def test_names_mentioned_people_and_channels() -> None:
    contents = _contents(
        [
            {"ts": "1.0", "text": "cc <@U456>, see <#C456|> and <#C000>", "user": "U123"},
            {"ts": "9.0", "text": "<@UBOT> go", "user": "U123"},
        ],
        user_names_by_id={"U123": "Alice", "U456": "Bob <B>"},
        channel_names_by_id={"C456": "eng"},
    )
    assert "cc &lt;@U456|Bob &amp;lt;B&amp;gt;&gt;, see &lt;#C456|eng&gt; and &lt;#C000&gt;" in str(
        contents
    )


def test_the_thread_state_says_what_the_model_already_has() -> None:
    built = slack_prompt.build_input(**_case("mention_with_history"))
    values = {"messages": [{"type": "human", "content": m["content"]} for m in built["messages"]]}

    # As Agent Server's ``threads.get_state`` reads it.
    hashes, timestamps = slack_prompt.dispatched_context({"values": values, "next": []})

    assert timestamps == {"1.0", "1.2", "1.3", "9.0"}
    case: dict[str, Any] = {
        **_case("mention_with_history"),
        "dispatched_timestamps": timestamps,
        "visible_context_hashes": hashes,
    }
    again = slack_prompt.build_input(**case)
    # Only this turn's own context and the request are new: no context block or replayed
    # message is repeated.
    senders = [
        re.search(r'sender="([^"]+)"', str(message["content"])) for message in again["messages"]
    ]
    assert [match.group(1) for match in senders if match] == ["system:slack-context", "user:1"]
    assert slack_prompt.dispatched_context(None) == (set(), set())
    # Messages behind a summary are gone from the prompt, so their context is shown again.
    assert (
        slack_prompt.dispatched_context(
            {"values": {**values, "visible_from": len(built["messages"])}}
        )[0]
        == set()
    )


def test_selects_messages_since_the_last_mention() -> None:
    messages = [
        {"ts": "1.0", "text": "first", "user": "U1"},
        {"ts": "2.0", "text": "<@UBOT> earlier ask", "user": "U1"},
        {"ts": "3.0", "text": "between", "user": "U2"},
        {"ts": "4.0", "text": "<@UBOT> now", "user": "U1"},
    ]
    selected, origin = slack_prompt.select_context_messages(messages, "4.0", "UBOT")
    assert [message["ts"] for message in selected] == ["2.0", "3.0", "4.0"]
    assert origin == "last_mention"
    selected, origin = slack_prompt.select_context_messages(
        messages, "4.0", "", treat_all_messages_as_mentions=True
    )
    assert [message["ts"] for message in selected] == ["3.0", "4.0"]


@pytest.mark.parametrize(
    ("held", "expected"),
    [
        # The thread holds the earlier ask, not the /btw that went to a thread of its own.
        ({"1.0", "2.0"}, ["2.0", "3.0", "3.1", "4.0"]),
        # A new thread holds nothing and reads the Slack thread from its start.
        (set(), ["1.0", "2.0", "3.0", "3.1", "4.0"]),
    ],
)
def test_context_starts_at_the_last_mention_the_thread_holds(
    held: set[str], expected: list[str]
) -> None:
    messages = [
        {"ts": "1.0", "text": "first", "user": "U1"},
        {"ts": "2.0", "text": "<@UBOT> earlier ask", "user": "U1"},
        {"ts": "3.0", "text": "<@UBOT> /btw how does routing work?", "user": "U2"},
        {"ts": "3.1", "text": "It goes by channel.", "user": "UBOT", "bot_id": "B1"},
        {"ts": "4.0", "text": "<@UBOT> now", "user": "U1"},
    ]
    selected, _ = slack_prompt.select_context_messages(
        messages, "4.0", "UBOT", held_timestamps=held
    )
    assert [message["ts"] for message in selected] == expected


def test_formats_cards_forwarded_messages_and_authors() -> None:
    text = slack_prompt.format_messages_for_prompt(
        [
            {"ts": "1.0", "text": "<@UBOT> hi", "user": "U1"},
            {"ts": "1.1", "blocks": [{"type": "section", "text": {"text": "card"}}], "bot_id": "B"},
            {"ts": "1.2", "text": "me", "user": "UBOT"},
            {"ts": "1.3", "user": "U2", "attachments": [{"is_share": True, "text": "fwd"}]},
        ],
        {"U1": "Al"},
        "UBOT",
        "Open SWE",
    )
    assert text == (
        "@Al(U1) [message_ts=1.0]: @Open SWE hi\n"
        "@Bot(bot) [message_ts=1.1]: card\n"
        "@Open SWE(self) [message_ts=1.2]: me\n"
        "@U2(U2) [message_ts=1.3]: [forwarded message]\n[Forwarded Slack message]\nfwd"
    )


def test_the_guidance_texts_match_the_backends() -> None:
    assert slack_prompt.thread_constant_context(concierge=True, kitchen=False) == "\n\n".join(
        [
            slack_prompt.CONCIERGE_CONTEXT,
            slack_prompt.NON_KITCHEN_CONTEXT,
        ]
    )
    assert slack_prompt.thread_turn_context(message_update=True, resolved_links="links") == (
        f"{slack_prompt.MESSAGE_UPDATE_PREAMBLE}\n\nlinks"
    )
    assert slack_prompt.dm_origin_section(
        {"channel_id": "C1", "subject": "the PR"}, permalink="", agent_thread_id="t-1"
    ).startswith(
        "This conversation is a reply to a direct message Robin sent this person on "
        "behalf of the Slack thread <#C1> about the PR"
    )
    assert "https://github.com/a/b/pull/1 (Fix)" in slack_prompt.review_request_section(
        {"url": "https://github.com/a/b/pull/1", "title": "Fix"}
    )


def test_the_channel_identity_has_what_stays_true_of_the_thread() -> None:
    channel = slack_prompt.channel_context(
        "C1", {"name": "eng", "topic": {"value": " Ship it "}, "purpose": ""}
    )
    assert slack_prompt.channel_identity(
        "C1", "1.0", channel, default_repo="acme/api", web_url="https://x/t"
    ) == {
        "id": "slack:C1",
        "platform": "slack",
        "name": "#eng",
        "thread_id": "1.0",
        "topic": "Ship it",
        "default_repo": "acme/api",
        "web_url": "https://x/t",
    }


def test_image_urls_are_linked_attached_then_resolved_each_once() -> None:
    urls = slack_prompt.image_urls(
        [
            {"text": "![a](https://img.example/a.png) https://img.example/a.png"},
            {
                "files": [
                    {"mimetype": "image/png", "url_private": "https://files.slack.com/f.png"},
                    {"mimetype": "text/plain", "url_private": "https://files.slack.com/f.txt"},
                ]
            },
        ],
        ["https://files.slack.com/f.png", "https://files.slack.com/g.png"],
    )
    assert urls == [
        "https://img.example/a.png",
        "https://files.slack.com/f.png",
        "https://files.slack.com/g.png",
    ]


class _Slack:
    """Slack's Web API as the bot token reads it: one answer per method, recorded."""

    def __init__(self, answers: Mapping[str, Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def __call__(
        self, token: str, method: str, body: Mapping[str, object], **kwargs: object
    ) -> Mapping[str, object]:
        assert token == "xoxb-token"
        assert kwargs == {"http_method": "GET"}
        self.calls.append((method, dict(body)))
        answer = self.answers[method]
        if isinstance(answer, Exception):
            raise answer
        return answer(body) if callable(answer) else answer


@pytest.fixture
def slack(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(answers: Mapping[str, Any]) -> _Slack:
        api = _Slack(answers)

        async def token() -> str:
            return "xoxb-token"

        monkeypatch.setattr(slack_prompt, "slack_api", api)
        monkeypatch.setattr(slack_prompt, "slack_bot_token", token)
        return api

    return install


async def test_reads_every_page_of_a_thread_oldest_first(slack: Any) -> None:
    pages = {
        "": {"messages": [{"ts": "2.0"}], "response_metadata": {"next_cursor": "c2"}},
        "c2": {"messages": [{"ts": "1.0"}, "junk"], "response_metadata": {"next_cursor": ""}},
    }
    api = slack({"conversations.replies": lambda body: pages[str(body.get("cursor", ""))]})

    assert await slack_prompt.slack_history("C1", "1.0") == [{"ts": "1.0"}, {"ts": "2.0"}]
    assert api.calls[0] == ("conversations.replies", {"channel": "C1", "limit": 200, "ts": "1.0"})


async def test_a_failed_read_keeps_what_it_read(slack: Any) -> None:
    slack({"conversations.replies": ChannelError("channel_provider_rejected", "no")})
    assert await slack_prompt.slack_history("C1", "1.0") == []


async def test_names_people_and_only_public_channels(slack: Any) -> None:
    users = {
        "U1": {"user": {"profile": {"display_name": " Al "}}},
        "U2": {"user": {"real_name": "Bea"}},
    }
    channels = {
        "C1": {
            "channel": {
                "name": "eng",
                "is_channel": True,
                "is_private": False,
                "is_ext_shared": False,
                "is_pending_ext_shared": False,
            }
        },
        "C2": {"channel": {"name": "secret", "is_channel": True, "is_private": True}},
    }
    slack(
        {
            "users.info": lambda body: (
                users.get(str(body["user"]))
                or (_ for _ in ()).throw(ChannelError("channel_provider_rejected", "missing"))
            ),
            "conversations.info": lambda body: channels[str(body["channel"])],
        }
    )

    assert await slack_prompt.user_names(["U2", "U1", "U3", ""]) == {
        "U1": "Al",
        "U2": "Bea",
        "U3": "U3",
    }
    assert await slack_prompt.public_channel_names(["C1", "C2"]) == {"C1": "eng"}


async def test_resolves_linked_slack_messages(slack: Any) -> None:
    link = "https://acme.slack.com/archives/C7/p1776281321762829"
    slack(
        {
            "conversations.history": {
                "messages": [
                    {
                        "ts": "1776281321.762829",
                        "text": "the bug",
                        "user": "U8",
                        "files": [
                            {
                                "mimetype": "image/png",
                                "url_private": "https://files.slack.com/x.png",
                            }
                        ],
                    }
                ]
            },
            "users.info": {"user": {"real_name": "Cy"}},
        }
    )

    section, images = await slack_prompt.resolved_links(
        [{"text": f"see {link} and {link}"}], {}, channel_id="C7"
    )

    assert section == (
        f"\n\n## Cross-posted Slack Messages\n**{link}**\n  Author: Cy\n  Message: the bug"
    )
    assert images == ["https://files.slack.com/x.png"]
    assert await slack_prompt.resolved_links([{"text": "no links"}], {}, channel_id="C7") == (
        "",
        [],
    )


async def test_a_link_into_another_channel_is_followed_only_when_that_channel_is_public(
    slack: Any,
) -> None:
    """The bot reads every channel it is in; a private channel's message stays there."""
    public = "https://acme.slack.com/archives/C5/p1776281321762829"
    private = "https://acme.slack.com/archives/G9/p1776281321762830"
    dm = "https://acme.slack.com/archives/D3/p1776281321762831"
    shared = {"is_channel": True, "is_im": False, "is_mpim": False}
    channels = {
        "C5": {
            **shared,
            "is_private": False,
            "is_ext_shared": False,
            "is_pending_ext_shared": False,
        },
        "G9": {
            **shared,
            "is_private": True,
            "is_ext_shared": False,
            "is_pending_ext_shared": False,
        },
        "D3": {"is_channel": False, "is_im": True},
    }
    api = slack(
        {
            "conversations.info": lambda body: {"channel": channels[str(body["channel"])]},
            "conversations.history": lambda body: {
                "messages": [{"ts": body["latest"], "text": f"in {body['channel']}", "user": ""}]
            },
        }
    )

    section, _ = await slack_prompt.resolved_links(
        [{"text": f"{public} {private} {dm}"}], {}, channel_id="C1"
    )

    assert f"**{public}**\n  Author: \n  Message: in C5" in section
    assert f"**{private}**\n  (Could not fetch — bot may not have access)" in section
    assert f"**{dm}**\n  (Could not fetch — bot may not have access)" in section
    assert [body["channel"] for name, body in api.calls if name == "conversations.history"] == [
        "C5"
    ]


async def test_a_pasted_dm_link_is_read_from_the_preview_slack_adds(
    slack: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slack unfurls a pasted link after the message is posted; the DM itself is never read."""
    link = "https://acme.slack.com/archives/D3/p1776281321762831"
    preview = {"is_msg_unfurl": True, "from_url": link, "author_name": "Robin", "text": "card"}
    reads = iter(
        [
            {"messages": [{"ts": "4.0"}, {"ts": "5.0"}]},
            {"messages": [{"ts": "4.0"}, {"ts": "5.0", "attachments": [preview]}]},
        ]
    )
    slack(
        {
            "conversations.replies": lambda body: next(reads),
            "conversations.info": {"channel": {"is_channel": False, "is_im": True}},
        }
    )

    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr(slack_prompt.asyncio, "sleep", no_wait)
    message = {"ts": "5.0", "text": f"we removed these <{link}|{link}>"}

    await slack_prompt.await_link_unfurls("C1", "4.0", message)

    assert message["attachments"] == [preview]

    assert await slack_prompt.resolved_links([message], {}, channel_id="C1") == ("", [])


async def test_previews_slack_did_add_are_kept_when_another_never_arrives(
    slack: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown, missing = (
        "https://acme.slack.com/archives/D3/p1776281321762831",
        "https://acme.slack.com/archives/D4/p1776281321762832",
    )
    preview = {"is_msg_unfurl": True, "from_url": shown, "text": "card"}
    slack(
        {
            "conversations.replies": {"messages": [{"ts": "5.0", "attachments": [preview]}]},
            "conversations.info": {"channel": {"is_channel": False, "is_im": True}},
        }
    )

    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr(slack_prompt.asyncio, "sleep", no_wait)
    message = {"ts": "5.0", "text": f"{shown} {missing}"}

    await slack_prompt.await_link_unfurls("C1", "4.0", message)

    assert message["attachments"] == [preview]


async def test_a_link_robin_can_read_does_not_wait_for_a_preview(slack: Any) -> None:
    api = slack({})
    message = {"ts": "5.0", "text": "see https://acme.slack.com/archives/C1/p1776281321762831"}

    await slack_prompt.await_link_unfurls("C1", "4.0", message)

    assert api.calls == []


async def test_a_previewed_readable_link_adds_only_its_images(slack: Any) -> None:
    link = "https://acme.slack.com/archives/C1/p1776281321762831"
    image = {"mimetype": "image/png", "url_private": "https://files.slack.com/x.png"}
    slack({"conversations.history": {"messages": [{"ts": "1.0", "text": "hi", "files": [image]}]}})
    message = {
        "text": link,
        "attachments": [{"is_msg_unfurl": True, "from_url": link, "text": "hi"}],
    }

    assert await slack_prompt.resolved_links([message], {}, channel_id="C1") == (
        "",
        ["https://files.slack.com/x.png"],
    )


async def test_a_permalink_names_the_message(slack: Any) -> None:
    slack({"chat.getPermalink": {"permalink": "https://acme.slack.com/archives/C1/p1"}})
    assert await slack_prompt.permalink("C1", "1.0") == "https://acme.slack.com/archives/C1/p1"


async def test_slack_files_are_fetched_with_the_bot_token_and_others_without(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched: list[tuple[str, str]] = []

    async def slack_file(url: str, token: str) -> Any:
        fetched.append((url, token))

        class _Response:
            headers = {"Content-Type": "image/png"}

        return _Response(), b"png"

    async def other(url: str) -> Any:
        fetched.append((url, ""))

    async def token() -> str:
        return "xoxb-token"

    monkeypatch.setattr(slack_prompt, "_slack_file", slack_file)
    monkeypatch.setattr(slack_prompt, "fetch_image_block", other)
    monkeypatch.setattr(slack_prompt, "slack_bot_token", token)

    blocks = await slack_prompt.image_blocks(
        ["https://files.slack.com/a.png", "https://img.example/b.png"]
    )

    assert fetched == [
        ("https://files.slack.com/a.png", "xoxb-token"),
        ("https://img.example/b.png", ""),
    ]
    assert [block["type"] for block in blocks] == ["image"]
