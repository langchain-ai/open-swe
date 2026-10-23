from xml.etree import ElementTree

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from openswe.input_messages import (
    delivered_event_match_ids,
    dynamic_context_hash,
    human_input,
    person_introduction,
    visible_dynamic_context_hashes,
)


def _parse(content: str) -> ElementTree.Element:
    return ElementTree.fromstring(content)


def test_human_input_escapes_data_and_attributes() -> None:
    message = human_input(
        '<fix a="b"> & continue\x00\x1b\ud800\ufffe 🌱\t\nnext',
        {
            "sender_id": "github:octocat",
            "channel_id": "slack:C123",
            "surface": "web",
            "kind": "human",
            "data": {"label": "a\x01b", "details": {"result": "ok\x08"}},
        },
    )

    assert isinstance(message["content"], str)
    root = _parse(message["content"])
    assert root.attrib == {
        "sender": "github:octocat",
        "channel": "slack:C123",
        "surface": "web",
        "kind": "human",
        "label": "ab",
    }
    assert (root.text or "").strip() == '<fix a="b"> & continue 🌱\t\nnext'
    assert (root.findtext("details") or "").strip() == "result: ok"


def test_multimodal_input_preserves_non_text_blocks_and_order() -> None:
    image = {"type": "image", "base64": "abc", "mime_type": "image/png"}
    message = human_input(
        [image, {"type": "text", "text": "describe <this>"}],
        {"sender_id": "github:octocat", "surface": "web", "kind": "human"},
    )

    assert isinstance(message["content"], list)
    assert message["content"][0] is image
    assert (_parse(message["content"][1]["text"]).text or "").strip() == "describe <this>"


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


@pytest.mark.parametrize("as_model", [False, True])
def test_delivered_event_match_ids_survive_malformed_envelopes(as_model: bool) -> None:
    content = '<input-message event_match="legacy">bad\x1b[0m</input-message>'
    message = (
        HumanMessage(content=content, id="event-match:recorded")
        if as_model
        else {"content": content, "id": "event-match:recorded"}
    )
    legacy = {"content": '<input-message event_match="legacy">ok</input-message>'}

    assert delivered_event_match_ids([message, legacy, message]) == {"recorded", "legacy"}
