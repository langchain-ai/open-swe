from __future__ import annotations

from typing import Any, cast

import pytest
from langchain_core.language_models.chat_models import BaseChatModel

from agent.thread_title import _ThreadTitle, generate_and_store_thread_title


class _StructuredModel:
    async def ainvoke(self, messages: list[Any]) -> _ThreadTitle:
        return _ThreadTitle(title="Review thread title generation")


class _Model:
    def with_structured_output(self, schema: type[_ThreadTitle]) -> _StructuredModel:
        assert schema is _ThreadTitle
        return _StructuredModel()


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
                "source": "dashboard",
                "title": "please review title generation",
                "title_seed": "please review title generation",
            },
            {
                "source": "dashboard",
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
