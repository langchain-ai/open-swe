import asyncio
import contextvars
from typing import Any, cast

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from agent.thread_title import (
    _normalize_title,
    _ThreadTitle,
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


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Review Devin comments on pull request 34300", "Review Devin comments"),
        ("Fix review comments for pull request 32722", "Fix review comments"),
        ("Review PR 33627 feedback", "Review feedback"),
        ("Fix issue 39727 with co-authored changes", "Fix with co-authored changes"),
        ("Spawn review run for PR 675", "Spawn review run"),
        ("Address review comments on pull request", "Address review comments on pull request"),
    ],
)
def test_normalize_title_removes_pr_and_issue_numbers_without_losing_subject(
    title: str, expected: str
) -> None:
    assert _normalize_title(title) == expected


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
        user_message="please review title generation",
        model=cast(BaseChatModel, _Model()),
        client=client,
    )

    assert threads.metadata == expected


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
async def test_title_generation_disables_inherited_callbacks() -> None:
    """Belt and braces: callbacks bound to the model itself must not stream either."""
    recorder: dict[str, Any] = {}
    threads = _Threads(
        {
            "source": "dashboard",
            "title": "please review title generation",
            "title_seed": "please review title generation",
        }
    )
    client = type("Client", (), {"threads": threads})()

    await generate_and_store_thread_title(
        thread_id="thread-123",
        user_message="please review title generation",
        model=cast(BaseChatModel, _Model(recorder)),
        client=client,
    )

    assert recorder["config"]["callbacks"] == []


@pytest.mark.asyncio
async def test_title_generation_skips_threads_past_the_first_message() -> None:
    """Only the opening message seeds a title; later turns leave it alone."""
    recorder: dict[str, Any] = {}
    threads = _Threads(
        {
            "source": "dashboard",
            "title": "please review title generation",
            "title_seed": "please review title generation",
        }
    )
    client = type("Client", (), {"threads": threads})()

    schedule_thread_title_generation(
        thread_id="thread-123",
        messages=[
            HumanMessage(content="first"),
            AIMessage(content="reply"),
            HumanMessage(content="second"),
        ],
        model=cast(BaseChatModel, _Model(recorder)),
        client=client,
    )
    for _ in range(10):
        await asyncio.sleep(0)

    assert recorder == {}
