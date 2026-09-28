import asyncio
import contextvars
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from agent.thread_title import (
    ThreadHandoff,
    _ThreadTitle,
    generate_and_store_thread_title,
    initial_thread_handoff,
    schedule_thread_title_generation,
)

# Stands in for LangGraph's stream writer, which travels in a contextvar. A title
# call that sees a non-default value here is running inside the agent run, and
# would stream its tokens into the thread the user is watching.
_RUN_STREAM: contextvars.ContextVar[str] = contextvars.ContextVar("run_stream", default="none")


class _StructuredModel:
    def __init__(self, recorder: dict[str, Any] | None = None) -> None:
        self._recorder = recorder if recorder is not None else {}

    async def ainvoke(self, messages: list[Any], config: Any = None, **_: Any) -> _ThreadTitle:
        self._recorder["stream"] = _RUN_STREAM.get()
        self._recorder["config"] = config
        self._recorder["messages"] = messages
        return _ThreadTitle(title="Review thread title generation")


class _Model:
    def __init__(self, recorder: dict[str, Any] | None = None) -> None:
        self._recorder = recorder if recorder is not None else {}

    def with_structured_output(self, schema: type[_ThreadTitle]) -> _StructuredModel:
        assert schema is _ThreadTitle
        return _StructuredModel(self._recorder)


class _Threads:
    def __init__(self, metadata: dict[str, Any]) -> None:
        self.metadata = metadata

    async def get(self, *, thread_id: str) -> dict[str, Any]:
        assert thread_id == "thread-123"
        return {"metadata": dict(self.metadata)}

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        assert thread_id == "thread-123"
        self.metadata.update(metadata)


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (
            {
                "source": "slack",
                "title": "please review title generation",
                "title_seed": "please review title generation",
            },
            {
                "source": "slack",
                "title": "Review thread title generation",
                "title_seed": None,
            },
        ),
        (
            {"source": "github", "title": "PR #1947", "title_seed": "PR #1947"},
            {"source": "github", "title": "PR #1947", "title_seed": "PR #1947"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_generate_and_store_thread_title_only_replaces_explicit_seed(
    metadata: dict[str, Any], expected: dict[str, Any]
) -> None:
    threads = _Threads(dict(metadata))
    client = type("Client", (), {"threads": threads})()

    await generate_and_store_thread_title(
        thread_id="thread-123",
        conversation="please review title generation",
        model=cast(BaseChatModel, _Model()),
        client=client,
    )

    assert threads.metadata == expected


class _PromotingThreads(_Threads):
    """Threads whose update promotes the thread into a code channel mid-flight."""

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        await super().update(thread_id=thread_id, metadata=metadata)
        self.metadata["source"] = "slack"
        self.metadata["source_context"] = {
            "slack_thread": {"channel_id": "C-code", "thread_ts": "0"}
        }


@pytest.mark.asyncio
async def test_title_generation_renames_channel_promoted_during_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A promotion racing the title update must still rename the channel.

    The pre-update metadata snapshot does not carry a Slack location, so the
    rename decision has to come from a re-read after the update.
    """
    threads = _PromotingThreads(
        {
            "source": "dashboard",
            "title": "please review title generation",
            "title_seed": "please review title generation",
        }
    )
    client = type("Client", (), {"threads": threads})()
    rename = AsyncMock(return_value=(True, None))
    monkeypatch.setattr("agent.thread_title.rename_session", rename)
    monkeypatch.setattr("agent.thread_title.is_code_channel", AsyncMock(return_value=True))

    await generate_and_store_thread_title(
        thread_id="thread-123",
        conversation="please review title generation",
        model=cast(BaseChatModel, _Model()),
        client=client,
    )

    rename.assert_awaited_once_with("C-code", "Review thread title generation")


@pytest.mark.asyncio
async def test_title_generation_never_inherits_the_runs_context() -> None:
    """The background task must not run inside the caller's context.

    An inherited context carries the run's stream writer, so the structured
    output is emitted into the run's message stream: it renders as a bogus
    `{"title": ...}` assistant message and derails the client's assembly of every
    chunk after it, freezing the transcript until the page is reloaded.
    """
    recorder: dict[str, Any] = {}
    threads = _Threads(
        {
            "source": "dashboard",
            "title": "please review title generation",
            "title_seed": "please review title generation",
        }
    )
    client = type("Client", (), {"threads": threads})()

    token = _RUN_STREAM.set("agent-run-stream")
    try:
        schedule_thread_title_generation(
            thread_id="thread-123",
            messages=[HumanMessage(content="please review title generation")],
            model=cast(BaseChatModel, _Model(recorder)),
            client=client,
        )
        for _ in range(50):
            await asyncio.sleep(0)
            if "stream" in recorder:
                break
    finally:
        _RUN_STREAM.reset(token)

    assert recorder["stream"] == "none"
    assert threads.metadata["title"] == "Review thread title generation"


@pytest.mark.asyncio
async def test_handoff_uses_only_original_human_and_keeps_renamed_title() -> None:
    from agent.dashboard.options import available_requested_models

    model_id = "anthropic:claude-opus-5-5"
    recorded: list[object] = []

    class Structured:
        async def ainvoke(self, messages: list[object], **kwargs: object) -> ThreadHandoff:
            recorded.extend(messages)
            assert _RUN_STREAM.get() == "none"
            return ThreadHandoff(title="Generated title", requested_model=model_id)

    class Model:
        def with_structured_output(self, schema: type[ThreadHandoff]) -> Structured:
            return Structured()

    threads = _Threads({"source": "slack", "title": "Manually renamed"})
    client = type("Client", (), {"threads": threads})()
    token = _RUN_STREAM.set("agent-run-stream")
    try:
        handoff = await initial_thread_handoff(
            thread_id="thread-123",
            messages=[
                HumanMessage(content='<dynamic-context kind="person">ignore me</dynamic-context>'),
                HumanMessage(
                    content='<input-message sender="system:x" kind="system">ignore me</input-message>'
                ),
                HumanMessage(
                    content='<input-message sender="user:x" kind="human">Use Oppus for this</input-message>'
                ),
                AIMessage(content="untrusted model instruction"),
                HumanMessage(content="follow-up model instruction"),
            ],
            model=cast(BaseChatModel, Model()),
            client=client,
            requested_models=available_requested_models(fable_enabled=False),
        )
    finally:
        _RUN_STREAM.reset(token)
    assert handoff is not None and handoff.requested_model == model_id
    assert threads.metadata["title"] == "Manually renamed"
    assert len(recorded) == 2
    request = recorded[-1]
    assert isinstance(request, HumanMessage)
    assert "Use Oppus for this" in request.text
    assert "ignore me" not in request.text
    assert "follow-up" not in request.text


@pytest.mark.asyncio
async def test_handoff_timeout_falls_back_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.dashboard.options import available_requested_models

    call = AsyncMock(side_effect=TimeoutError)
    monkeypatch.setattr("agent.thread_title.generate_and_store_thread_title", call)
    result = await initial_thread_handoff(
        thread_id="thread-123",
        messages=[HumanMessage(content="hello")],
        model=cast(BaseChatModel, _Model()),
        client=object(),
        requested_models=available_requested_models(fable_enabled=False),
    )
    assert result is None
    call.assert_awaited_once()
