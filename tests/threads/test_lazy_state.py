from agent.threads.lazy_state import LAZY_MARKER_KEY, split_state

BIG = "x" * 4096
IMAGE = {"type": "image", "mime_type": "image/png", "data": "iVBOR" * 200}


def _state(messages: list[dict[str, object]], **values: object) -> dict[str, object]:
    return {
        "values": {"messages": messages, **values},
        "next": [],
        "checkpoint": {"checkpoint_id": "c1"},
    }


def test_tool_results_of_every_size_are_deferred_with_status_kept() -> None:
    messages = [
        {"type": "ai", "id": "a1", "content": "hi", "tool_calls": [{"id": "small", "name": "ls"}]},
        {"type": "tool", "tool_call_id": "small", "content": "ok", "status": "success"},
        {
            "type": "tool",
            "tool_call_id": "big",
            "content": BIG,
            "status": "error",
            "artifact": {"type": "output_iframe", "html": BIG},
        },
        {"type": "tool", "tool_call_id": "empty", "content": ""},
    ]
    skeleton, parts, summary = split_state(_state(messages))

    trimmed = skeleton["values"]["messages"]
    assert trimmed[0] == messages[0]
    assert trimmed[1]["content"] == "" and trimmed[1]["status"] == "success"
    assert trimmed[1]["additional_kwargs"][LAZY_MARKER_KEY]["parts"] == ["content"]
    assert trimmed[2]["content"] == "" and "artifact" not in trimmed[2]
    assert trimmed[2]["additional_kwargs"][LAZY_MARKER_KEY]["parts"] == ["content", "artifact"]
    assert trimmed[3] == messages[3]  # nothing to defer, so no marker
    assert set(parts["tool_results"]) == {"small", "big"}
    assert parts["tool_results"]["big"]["status"] == "error"
    assert summary["deferred"] == 2
    assert summary["categories"]["tool_artifact"] > len(BIG)
    assert summary["kept_bytes"] < len(BIG)
    assert skeleton["checkpoint"] == {"checkpoint_id": "c1"}


def test_reasoning_and_response_metadata_are_deferred_but_text_and_tool_calls_stay() -> None:
    content = [
        {"type": "reasoning", "reasoning": "let me think " * 200},
        {"type": "text", "text": "Here is the answer."},
        {"type": "tool_call", "id": "c", "name": "read", "args": {"path": "a.py"}},
    ]
    message = {
        "type": "ai",
        "id": "a1",
        "content": content,
        "tool_calls": [{"id": "c", "name": "read", "args": {"path": "a.py"}}],
        "response_metadata": {
            "created_at": "2026-09-16T00:00:00Z",
            "model_name": "m",
            "usage": {"x": 1},
        },
        "usage_metadata": {"input_tokens": 10},
    }
    skeleton, parts, summary = split_state(_state([message]))

    trimmed = skeleton["values"]["messages"][0]
    # The tool_call block duplicates `tool_calls`, which is what gets read.
    assert trimmed["content"] == [{"type": "reasoning", "reasoning": ""}, content[1]]
    assert trimmed["tool_calls"] == message["tool_calls"]
    assert trimmed["usage_metadata"] == {"input_tokens": 10}
    assert trimmed["response_metadata"] == {"created_at": "2026-09-16T00:00:00Z"}
    assert trimmed["additional_kwargs"][LAZY_MARKER_KEY]["parts"] == ["reasoning"]
    assert parts["reasoning"] == {"a1": "let me think " * 200}
    assert summary["categories"]["reasoning"] > 2000
    assert summary["categories"]["response_metadata"] > 0


def test_summarization_messages_lose_their_content() -> None:
    message = {
        "type": "ai",
        "id": "s1",
        "content": BIG,
        "additional_kwargs": {"lc_source": "summarization"},
    }
    skeleton, parts, _ = split_state(_state([message]))
    trimmed = skeleton["values"]["messages"][0]
    assert trimmed["content"] == ""
    assert trimmed["additional_kwargs"]["lc_source"] == "summarization"
    assert trimmed["additional_kwargs"][LAZY_MARKER_KEY]["parts"] == ["content"]
    assert parts == {"tool_results": {}, "reasoning": {}, "images": {}}


def test_human_images_are_deferred_in_place() -> None:
    message = {
        "type": "human",
        "id": "h1",
        "content": [{"type": "text", "text": "look"}, IMAGE, {"type": "text", "text": "at this"}],
    }
    skeleton, parts, _ = split_state(_state([message]))
    trimmed = skeleton["values"]["messages"][0]
    assert trimmed["content"] == [
        {"type": "text", "text": "look"},
        {"type": "image", "mime_type": "", "data": ""},
        {"type": "text", "text": "at this"},
    ]
    assert parts["images"] == {"h1": [IMAGE]}
    plain = {"type": "human", "id": "h2", "content": "no images"}
    assert split_state(_state([plain]))[0]["values"]["messages"][0] == plain


def test_values_keep_only_messages() -> None:
    state = _state([{"type": "human", "id": "h", "content": "hi"}], files={"a": BIG}, todos=[1])
    skeleton, _, summary = split_state(state)
    assert skeleton["values"] == {"messages": state["values"]["messages"]}
    assert summary["categories"]["values"] > len(BIG)
    assert summary["deferred"] == 0


def test_signature_only_reasoning_blocks_are_dropped_without_a_marker() -> None:
    message = {
        "type": "ai",
        "id": "a1",
        "content": [
            {"type": "reasoning", "reasoning": "", "extras": {"signature": "s" * 2000}},
            {"type": "text", "text": "answer"},
        ],
    }
    skeleton, parts, summary = split_state(_state([message]))
    trimmed = skeleton["values"]["messages"][0]
    assert trimmed["content"] == [{"type": "reasoning", "reasoning": ""}, message["content"][1]]
    assert LAZY_MARKER_KEY not in (trimmed.get("additional_kwargs") or {})
    assert parts["reasoning"] == {}
    assert summary["categories"]["reasoning"] > 2000


def test_tool_call_blocks_stay_when_tool_calls_is_empty() -> None:
    message = {
        "type": "ai",
        "id": "a1",
        "content": [{"type": "tool_call", "id": "c", "name": "read", "args": {}}],
        "tool_calls": [],
    }
    skeleton, _, _ = split_state(_state([message]))
    assert skeleton["values"]["messages"][0] == message
