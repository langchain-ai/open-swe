from xml.etree import ElementTree

from langchain_core.messages import AIMessage, HumanMessage

from openswe.input_messages import (
    VisibleContext,
    human_input,
    latest_context_only,
    person_introduction,
)


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


def _person_intro_message(entity_id: str, name: str = "Ramon") -> HumanMessage:
    content = person_introduction({"id": entity_id, "display_name": name})["content"]
    assert isinstance(content, str)
    return HumanMessage(content=content)


def test_visible_context_ignores_summarized_prefix() -> None:
    intro = person_introduction({"id": "slack:U1", "display_name": "Ramon"})
    messages = [
        _person_intro_message("slack:U1"),
        AIMessage(content="working"),
        HumanMessage(content="follow up"),
    ]

    assert not VisibleContext.of_state({"messages": messages}).admit(intro)

    summarized = {"messages": messages, "_summarization_event": {"cutoff_index": 1}}
    assert VisibleContext.of_state(summarized).admit(intro)


def test_reverted_context_is_sent_again_and_only_the_latest_reaches_the_model() -> None:
    first = _person_intro_message("slack:U1", "Ramon")
    renamed = _person_intro_message("slack:U1", "Ray")
    other = _person_intro_message("slack:U2", "Ada")
    messages = [first, other, HumanMessage(content="hi"), renamed]

    assert VisibleContext.of_messages(messages).admit(
        person_introduction({"id": "slack:U1", "display_name": "Ramon"})
    )
    assert latest_context_only(messages) == [other, messages[2], renamed]
