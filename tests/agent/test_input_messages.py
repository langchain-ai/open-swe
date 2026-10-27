from xml.etree import ElementTree

from langchain_core.messages import AIMessage, HumanMessage

from openswe.input_messages import (
    dynamic_context_hash,
    human_input,
    person_introduction,
    visible_dynamic_context_hashes,
)
from openswe.utils.dashboard_handoff import PassageReply, dashboard_input_context


def _parse(content: str) -> ElementTree.Element:
    return ElementTree.fromstring(content)


def test_human_input_escapes_data_and_attributes() -> None:
    message = human_input(
        '<fix a="b"> & continue',
        {
            "sender_id": "github:octocat",
            "channel_id": "slack:C123",
            "surface": "web",
            "kind": "human",
        },
    )

    assert isinstance(message["content"], str)
    root = _parse(message["content"])
    assert root.attrib == {
        "sender": "github:octocat",
        "channel": "slack:C123",
        "surface": "web",
        "kind": "human",
    }
    assert (root.text or "").strip() == '<fix a="b"> & continue'


def test_multimodal_input_preserves_non_text_blocks_and_order() -> None:
    image = {"type": "image", "base64": "abc", "mime_type": "image/png"}
    message = human_input(
        [image, {"type": "text", "text": "describe <this>"}],
        {"sender_id": "github:octocat", "surface": "web", "kind": "human"},
    )

    assert isinstance(message["content"], list)
    assert message["content"][0] is image
    assert (_parse(message["content"][1]["text"]).text or "").strip() == "describe <this>"


def test_replies_ride_as_escaped_elements_even_without_text() -> None:
    image = {"type": "image", "base64": "abc", "mime_type": "image/png"}
    replies = [
        PassageReply(
            source="file",
            file="greet.py",
            lines="6",
            before="def greet():\n    ",
            selection='return "</reply>"',
            after="\n",
            comment="Use a <constant>",
        ),
        PassageReply(source="conversation", selection="Opening a PR.", comment="Draft?"),
    ]
    message = human_input([image], dashboard_input_context("github:octocat", None, replies))

    assert isinstance(message["content"], list)
    root = _parse(message["content"][1]["text"])
    assert (root.text or "").strip() == ""
    first, second = root.findall("reply")
    assert 'selection: return "</reply>"' in (first.text or "")
    assert "comment: Use a <constant>" in (first.text or "")
    assert "before:\n  def greet():\n      \n" in (first.text or "")
    assert (second.text or "").split("\n")[1:] == [
        "source: conversation",
        "selection: Opening a PR.",
        "comment: Draft?",
        "",
    ]


def _person_intro_message(entity_id: str) -> HumanMessage:
    content = person_introduction({"id": entity_id, "display_name": "Ramon"})["content"]
    assert isinstance(content, str)
    return HumanMessage(content=content)


def test_visible_dynamic_context_hashes_ignores_summarized_prefix() -> None:
    messages = [
        _person_intro_message("slack:U1"),
        AIMessage(content="working"),
        HumanMessage(content="follow up"),
    ]

    assert visible_dynamic_context_hashes({"messages": messages})

    summarized = {"messages": messages, "_summarization_event": {"cutoff_index": 1}}
    assert visible_dynamic_context_hashes(summarized) == set()


def test_changed_person_context_can_return_to_an_earlier_profile() -> None:
    person_id = "user:123"
    linked = person_introduction({"id": person_id, "open_swe_account": "linked"})
    unlinked = person_introduction({"id": person_id, "open_swe_account": "unlinked"})
    other = _person_intro_message("slack:U2")
    messages = [linked, other, unlinked]

    assert visible_dynamic_context_hashes({"messages": messages}) == {
        dynamic_context_hash(other.content),
        dynamic_context_hash(unlinked["content"]),
    }
    messages.append(linked)
    assert visible_dynamic_context_hashes({"messages": messages}) == {
        dynamic_context_hash(other.content),
        dynamic_context_hash(linked["content"]),
    }
