from openswe.agui.translator import AgUiTranslator


def _message(data: dict[str, object]) -> dict[str, object]:
    return {"method": "messages", "params": {"namespace": [], "timestamp": 1, "data": data}}


def test_interrupted_stream_closes_everything_it_opened_and_skips_known_messages() -> None:
    translator = AgUiTranslator({"already-shown"})
    stream = [
        _message({"event": "message-start", "role": "ai", "id": "already-shown"}),
        _message(
            {"event": "content-block-start", "index": 0, "content": {"type": "text", "text": "dup"}}
        ),
        _message({"event": "message-start", "role": "ai", "id": "a1"}),
        _message(
            {
                "event": "content-block-start",
                "index": 0,
                "content": {"type": "reasoning", "reasoning": "r"},
            }
        ),
        _message(
            {
                "event": "content-block-start",
                "index": 1,
                "content": {"type": "tool_call_chunk", "id": None, "name": None, "args": '{"p"'},
            }
        ),
        _message(
            {
                "event": "content-block-delta",
                "index": 1,
                "delta": {
                    "type": "block-delta",
                    "fields": {
                        "type": "tool_call_chunk",
                        "id": "c1",
                        "name": "ls",
                        "args": '{"p":1}',
                    },
                },
            }
        ),
        _message(
            {
                "event": "content-block-delta",
                "index": 0,
                "delta": {"type": "reasoning-delta", "reasoning": "!"},
            }
        ),
    ]
    events = [event for raw in stream for event in translator.translate(raw)] + translator.close()
    wire = [event.model_dump(by_alias=True, exclude_none=True) for event in events]

    assert all(event.get("messageId") != "already-shown" for event in wire)
    assert [event["type"] for event in wire] == [
        "REASONING_START",
        "REASONING_MESSAGE_START",
        "REASONING_MESSAGE_CONTENT",
        "TEXT_MESSAGE_START",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "REASONING_MESSAGE_CONTENT",
        "REASONING_MESSAGE_END",
        "REASONING_END",
        "TOOL_CALL_END",
        "TEXT_MESSAGE_END",
    ]
    assert wire[5]["delta"] == '{"p":1}'
