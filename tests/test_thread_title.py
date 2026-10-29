import asyncio
import contextvars
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import AsyncMock

import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage
from langchain_anthropic import ChatAnthropic
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage

from openswe.input_messages import InputMessageContext, human_input
from openswe.thread_title import (
    MAX_TITLE_INPUT_CHARS,
    _ThreadTitle,
    _title_input,
    generate_and_store_thread_title,
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


def test_breakout_title_prioritizes_new_request_over_inherited_transcript() -> None:
    request = "Find new open bugs against langchain.mcp namespace"
    context: InputMessageContext = {
        "sender_id": "person:requester",
        "surface": "slack",
        "kind": "human",
    }
    inherited = human_input("Which beta APIs should graduate? " * MAX_TITLE_INPUT_CHARS, context)[
        "content"
    ]
    opening = human_input(request, context)["content"]
    assert isinstance(inherited, str) and isinstance(opening, str)
    messages = [HumanMessage(content=inherited), HumanMessage(content=opening)]

    conversation = _title_input(messages)

    assert conversation is not None
    assert conversation.startswith(f"{request}\n\nWhich beta APIs should graduate?")
    assert len(conversation) == MAX_TITLE_INPUT_CHARS


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


@pytest.mark.parametrize("failure", ["error", "timeout"])
async def test_title_persistence_failure_is_bounded_and_logged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: Literal["error", "timeout"],
) -> None:
    threads = _Threads({"source": "dashboard", "title": "Fix the bug", "title_seed": "Fix the bug"})
    cancelled = asyncio.Event()

    async def fail_update(*, thread_id: str, metadata: dict[str, object]) -> None:
        if failure == "error":
            raise OSError("Title storage unavailable")
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(threads, "update", fail_update)
    monkeypatch.setattr("openswe.thread_title.TITLE_GENERATION_TIMEOUT_SECONDS", 0.01)
    await asyncio.wait_for(
        generate_and_store_thread_title(
            thread_id="thread-123",
            conversation="Fix the bug",
            model=cast(BaseChatModel, _Model()),
            client=SimpleNamespace(threads=threads),
        ),
        timeout=1,
    )

    assert threads.metadata["title"] == "Fix the bug"
    assert threads.metadata["title_seed"] == "Fix the bug"
    assert cancelled.is_set() is (failure == "timeout")
    record = next(r for r in caplog.records if r.message == "Thread title persistence failed")
    assert record.__dict__["thread_id"] == "thread-123"
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], TimeoutError if failure == "timeout" else OSError)


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
    rename = AsyncMock(return_value=None)
    monkeypatch.setattr("openswe.thread_title.rename_session", rename)
    monkeypatch.setattr("openswe.thread_title.is_code_channel", AsyncMock(return_value=True))

    await generate_and_store_thread_title(
        thread_id="thread-123",
        conversation="please review title generation",
        model=cast(BaseChatModel, _Model()),
        client=client,
    )

    rename.assert_awaited_once_with("C-code", "Review thread title generation")


@pytest.mark.parametrize("suffix", [None, "", " · <https://slack/source|(source)> · <@U1>"])
async def test_generated_title_updates_only_manual_breakout_roots(
    monkeypatch: pytest.MonkeyPatch, suffix: str | None
) -> None:
    threads = _Threads(
        {
            "source": "slack",
            "title": "fix it",
            "title_seed": "fix it",
            "source_context": {
                "slack_thread": {
                    "channel_id": "C1",
                    "thread_ts": "200.0",
                    "breakout_root_suffix": suffix,
                }
            },
        }
    )
    update = AsyncMock(return_value=None)
    monkeypatch.setattr("openswe.thread_title.update_slack_message", update)
    monkeypatch.setattr(
        "openswe.thread_title.dashboard_thread_url",
        lambda thread_id: f"https://openswe.langchain.dev/agents/{thread_id}",
    )

    await generate_and_store_thread_title(
        thread_id="thread-123",
        conversation="fix it",
        model=cast(BaseChatModel, _Model()),
        client=SimpleNamespace(threads=threads),
    )

    if suffix is None:
        update.assert_not_awaited()
    else:
        update.assert_awaited_once_with(
            "C1",
            "200.0",
            f"`/breakout`: Review thread title generation{suffix} "
            "<https://openswe.langchain.dev/agents/thread-123|Open in Web>",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"`/breakout`: Review thread title generation{suffix}",
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "<https://openswe.langchain.dev/agents/thread-123|Open in Web>",
                        }
                    ],
                },
            ],
            unfurl_links=False,
            unfurl_media=False,
        )


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


async def test_title_extraction_retries_once_and_preserves_seed_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    threads = _Threads({"source": "dashboard", "title": "fix it", "title_seed": "fix it"})
    structured = SimpleNamespace(
        ainvoke=AsyncMock(
            side_effect=OutputParserException("Forced tool calling is unsupported with thinking")
        )
    )
    completion = AsyncMock(side_effect=OutputParserException("Retry failed"))
    model = SimpleNamespace(with_structured_output=lambda schema: structured, ainvoke=completion)
    mirror = AsyncMock()
    monkeypatch.setattr("openswe.thread_title.mirror_thread_metadata", mirror)

    schedule_thread_title_generation(
        thread_id="thread-123",
        messages=[HumanMessage(content="fix it")],
        model=cast(BaseChatModel, model),
        client=SimpleNamespace(threads=threads),
    )
    from openswe.thread_title import _background_tasks

    await asyncio.gather(*tuple(_background_tasks))

    assert completion.await_count == 1
    assert threads.metadata == {
        "source": "dashboard",
        "title": "fix it",
        "title_seed": "fix it",
    }
    mirror.assert_not_awaited()
    assert "Thread title generation failed" in caplog.text


async def test_haiku_empty_tool_arguments_recover_to_a_stored_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _Threads({"source": "dashboard", "title": "fix it", "title_seed": "fix it"})
    model = ChatAnthropic(model="claude-haiku-5-5", api_key="test", effort="high", max_tokens=1024)

    async def create(self: ChatAnthropic, payload: dict[str, object]) -> object:
        assert "thinking" not in payload
        content = (
            [ToolUseBlock(type="tool_use", id="tool-1", name="_ThreadTitle", input={})]
            if "tools" in payload
            else [TextBlock(type="text", text=' "Repair   thread titles." ')]
        )
        response = Message(
            id="message-1",
            type="message",
            role="assistant",
            model="claude-haiku-5-5",
            content=content,
            stop_reason="tool_use" if "tools" in payload else "end_turn",
            usage=Usage(input_tokens=10, output_tokens=10),
        )
        return SimpleNamespace(parse=lambda: response)

    monkeypatch.setattr(ChatAnthropic, "_acreate", create)
    mirror = AsyncMock()
    monkeypatch.setattr("openswe.thread_title.mirror_thread_metadata", mirror)
    await generate_and_store_thread_title(
        thread_id="thread-123",
        conversation="fix it",
        model=model,
        client=SimpleNamespace(threads=threads),
    )
    assert threads.metadata["title"] == "Repair thread titles"
    assert threads.metadata["title_seed"] is None
    mirror.assert_awaited_once_with("thread-123", {"title": "Repair thread titles"})
