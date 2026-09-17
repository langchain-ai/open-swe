from agent.threads.lazy_state import (
    DEFER_MIN_CHARS,
    LAZY_MARKER_KEY,
    PREVIEW_CHARS,
    deferred_tool_results,
    skeletonize_state,
)

BIG = "x" * (DEFER_MIN_CHARS * 4)


def _state(messages: list[dict[str, object]]) -> dict[str, object]:
    return {"values": {"messages": messages, "files": {}}, "next": [], "checkpoint": {"id": "c"}}


def test_skeleton_trims_only_large_tool_results() -> None:
    small = {"type": "tool", "tool_call_id": "small", "content": "ok", "status": "success"}
    big = {
        "type": "tool",
        "tool_call_id": "big",
        "content": BIG,
        "status": "success",
        "artifact": {"type": "output_iframe", "html": BIG},
    }
    ai = {"type": "ai", "content": "hi", "tool_calls": [{"id": "big", "name": "read"}]}
    skeleton, summary = skeletonize_state(_state([ai, small, big]))

    assert summary["deferred"] == 1
    assert summary["deferred_bytes"] > len(BIG)
    trimmed = skeleton["values"]["messages"]
    assert trimmed[0] == ai
    assert trimmed[1] == small
    assert trimmed[2]["content"] == "x" * PREVIEW_CHARS
    assert trimmed[2]["status"] == "success"
    assert "artifact" not in trimmed[2]
    assert trimmed[2]["additional_kwargs"][LAZY_MARKER_KEY]["truncated"] is True
    # The rest of the snapshot is untouched.
    assert skeleton["next"] == []
    assert skeleton["values"]["files"] == {}


def test_skeleton_previews_block_content_as_text() -> None:
    blocks = [{"type": "text", "text": "a" * DEFER_MIN_CHARS}, {"type": "text", "text": "tail"}]
    skeleton, summary = skeletonize_state(
        _state([{"type": "tool", "tool_call_id": "t", "content": blocks}])
    )
    assert summary["deferred"] == 1
    assert skeleton["values"]["messages"][0]["content"] == "a" * PREVIEW_CHARS


def test_skeleton_returns_input_when_nothing_is_deferred() -> None:
    state = _state([{"type": "human", "content": "hello"}])
    skeleton, summary = skeletonize_state(state)
    assert skeleton is state
    assert summary == {"deferred": 0, "deferred_bytes": 0}


def test_deferred_tool_results_match_what_the_skeleton_trims() -> None:
    state = _state(
        [
            {"type": "tool", "tool_call_id": "small", "content": "ok"},
            {
                "type": "tool",
                "tool_call_id": "big",
                "content": BIG,
                "status": "error",
                "artifact": None,
            },
            {"type": "tool", "content": BIG},  # no tool_call_id: cannot be addressed
        ]
    )
    results = deferred_tool_results(state)
    assert set(results) == {"big"}
    assert results["big"] == {"content": BIG, "artifact": None, "status": "error"}
