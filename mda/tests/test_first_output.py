from collections.abc import Iterator
from itertools import count
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolCall
from rt.first_output import FirstOutput
from runtime_api.loop import FIRST_OUTPUT_AT_KEY, FIRST_TEXT_AT_KEY

RUN = uuid4()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ticks = count(1_000, 100)
    monkeypatch.setattr(FirstOutput, "now_ms", staticmethod(lambda: next(ticks)))
    yield


def _model(*replies: AIMessage) -> GenericFakeChatModel:
    return GenericFakeChatModel(messages=iter(replies))


@pytest.mark.usefixtures("clock")
async def test_a_streamed_reply_carries_its_first_chunk_and_first_text() -> None:
    first = FirstOutput()
    with first.watching():
        chunks = [chunk async for chunk in _model(AIMessage(content="Hello there")).astream("hi")]
    assert len(chunks) > 1
    # Heard as the chunks streamed, before the reply was whole.
    assert (first.output_at_ms, first.text_at_ms) == (1_000, 1_000)
    reply = AIMessage(content="Hello there")
    first.stamp([reply])

    assert reply.response_metadata == {FIRST_OUTPUT_AT_KEY: 1_000, FIRST_TEXT_AT_KEY: 1_000}


@pytest.mark.usefixtures("clock")
async def test_a_reply_that_did_not_stream_gave_its_output_as_it_returned() -> None:
    first = FirstOutput()
    with first.watching():
        reply = await _model(AIMessage(content="391")).ainvoke("hi")
    assert first.output_at_ms is None
    first.stamp([reply])

    assert reply.response_metadata[FIRST_OUTPUT_AT_KEY] == 1_000
    assert reply.response_metadata[FIRST_TEXT_AT_KEY] == 1_000


@pytest.mark.usefixtures("clock")
def test_a_reply_without_text_marks_only_its_output() -> None:
    first = FirstOutput()
    reply = AIMessage(content="", tool_calls=[ToolCall(name="look", args={}, id="call-1")])
    first.stamp([reply])

    assert reply.response_metadata == {FIRST_OUTPUT_AT_KEY: 1_000}


@pytest.mark.usefixtures("clock")
async def test_a_tool_call_streamed_before_the_text_marks_the_output_first() -> None:
    first = FirstOutput()
    await first.on_llm_new_token("", chunk=None, run_id=RUN)
    await first.on_stream_event(
        {"event": "content-block-delta", "delta": {"type": "text-delta", "text": "Hi"}},
        run_id=RUN,
    )
    reply = AIMessage(content="Hi")
    first.stamp([reply])

    assert reply.response_metadata == {FIRST_OUTPUT_AT_KEY: 1_000, FIRST_TEXT_AT_KEY: 1_100}


@pytest.mark.usefixtures("clock")
async def test_hidden_model_calls_are_not_the_agents_output() -> None:
    first = FirstOutput()
    await first.on_llm_new_token(
        "route",
        chunk=None,
        run_id=RUN,
        tags=["langsmith:hidden"],
    )
    chunk = AIMessageChunk(content="Hello")
    await first.on_llm_new_token(
        "Hello",
        chunk=type("Generation", (), {"message": chunk})(),
        run_id=RUN,
    )

    assert (first.output_at_ms, first.text_at_ms) == (1_000, 1_000)


@pytest.mark.usefixtures("clock")
async def test_a_failed_attempts_output_is_not_the_replys() -> None:
    first = FirstOutput()
    failed, retried = uuid4(), uuid4()
    await first.on_llm_new_token("Hello", chunk=None, run_id=failed)
    await first.on_llm_error(RuntimeError("overloaded"), run_id=failed)
    await first.on_llm_new_token("", chunk=None, run_id=retried)
    await first.on_llm_new_token("Done", chunk=None, run_id=retried)
    reply = AIMessage(content="Done")
    first.stamp([reply])

    assert reply.response_metadata == {FIRST_OUTPUT_AT_KEY: 1_100, FIRST_TEXT_AT_KEY: 1_200}
