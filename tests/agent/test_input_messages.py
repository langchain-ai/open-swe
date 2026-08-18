from xml.etree import ElementTree

import pytest

from agent.input_messages import (
    build_input_messages,
    build_run_input,
    human_input,
    wrap_system_prompt,
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
    assert root.findtext("content") == '<fix a="b"> & continue'


def test_multimodal_input_preserves_non_text_blocks_and_order() -> None:
    image = {"type": "image", "base64": "abc", "mime_type": "image/png"}
    message = human_input(
        [image, {"type": "text", "text": "describe <this>"}],
        {"sender_id": "github:octocat", "surface": "web", "kind": "human"},
    )

    assert isinstance(message["content"], list)
    assert message["content"][0] is image
    assert _parse(message["content"][1]["text"]).findtext("content") == "describe <this>"


def test_first_seen_introductions_are_practical_and_mutate_registry() -> None:
    injected = set()
    kwargs = {
        "people": [{"id": "github:octocat", "platform": "github", "github_login": "octocat"}],
        "channels": [{"id": "slack:C123", "platform": "slack", "topic": "a < b"}],
        "injected_dynamic_context_hashes": injected,
    }
    first = build_input_messages(
        "first",
        {
            "sender_id": "github:octocat",
            "channel_id": "slack:C123",
            "surface": "web",
            "kind": "human",
        },
        **kwargs,
    )
    second = build_input_messages(
        "second",
        {
            "sender_id": "github:octocat",
            "channel_id": "slack:C123",
            "surface": "web",
            "kind": "human",
        },
        **kwargs,
    )

    assert len(first) == 3
    assert len(second) == 1
    assert len(injected) == 2
    assert all(len(value) == 64 for value in injected)
    channel_content = first[1]["content"]
    assert isinstance(channel_content, str)
    channel = _parse(channel_content)
    topic = channel.find("topic")
    assert topic is not None
    assert topic.attrib["trust"] == "untrusted"
    assert channel.findtext("topic") == "a < b"


def test_system_prompt_wrapping_is_idempotent_and_additions_are_distinct() -> None:
    wrapped = wrap_system_prompt("Follow <rules> & finish")
    assert wrap_system_prompt(wrapped) == wrapped
    augmented = wrap_system_prompt(wrapped, additions=["Wrap up now"])

    root = _parse(augmented)
    assert root.tag == "system-instructions"
    messages = root.findall("input-message")
    assert [message.findtext("content") for message in messages] == [
        "Follow <rules> & finish",
        "Wrap up now",
    ]


def test_run_input_preserves_files() -> None:
    result = build_run_input(
        "analyze",
        {"sender_id": "system:job", "surface": "automation", "kind": "system"},
        systems=[{"id": "system:job", "display_name": "Job"}],
        files={"/skills/x": {"content": "data"}},
    )
    assert result.get("files") == {"/skills/x": {"content": "data"}}
    assert len(result["messages"]) == 2


def test_entity_ids_must_be_namespaced() -> None:
    with pytest.raises(ValueError):
        human_input("hello", {"sender_id": "octocat", "surface": "web", "kind": "human"})
