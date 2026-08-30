import json

from agent.dashboard import thread_live


def _sse(event: dict[str, object]) -> str:
    return f"data: {json.dumps(event)}\n\n"


async def test_snapshot_stream_skips_checkpoint_history_and_forwards_live(monkeypatch) -> None:
    old_checkpoint = {
        "type": "event",
        "event_id": "old-checkpoint",
        "method": "checkpoints",
        "params": {"namespace": [], "data": {"id": "cp-9", "step": 9}},
    }
    old_values = {
        "type": "event",
        "event_id": "old-values",
        "method": "values",
        "params": {"namespace": [], "data": {"messages": [{"id": "old-user"}]}},
    }
    live_message = {
        "type": "event",
        "event_id": "live-message",
        "method": "messages",
        "params": {"namespace": ["agent"], "data": {"event": "message-start"}},
    }

    class FakeResponse:
        status_code = 200
        reason_phrase = "OK"

        async def aiter_lines(self):
            for line in (_sse(old_checkpoint) + _sse(old_values) + _sse(live_message)).splitlines():
                yield line

    class FakeContext:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *args: object) -> None:
            pass

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def stream(self, *args: object, **kwargs: object) -> FakeContext:
            return FakeContext()

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(thread_live.httpx, "AsyncClient", FakeClient)

    async def get_state() -> dict[str, object]:
        return {
            "values": {"messages": [{"id": "old-user"}, {"id": "old-ai"}]},
            "checkpoint": {"checkpoint_id": "cp-9"},
            "metadata": {"step": 9},
        }

    stream = await thread_live.snapshot_live_events(
        "thread-1", get_state, upstream_url="http://graph", headers={}
    )
    frames = [json.loads(chunk) async for chunk in stream]

    assert frames[0]["type"] == "snapshot"
    assert [frame.get("event", {}).get("event_id") for frame in frames] == [
        None,
        "live-message",
        None,
    ]
