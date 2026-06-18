from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.reviewer_groups import (
    _build_prompt,
    _DiffGroupingResult,
    _DiffGroupModel,
    diff_signature,
    generate_diff_groups,
    maybe_generate_and_store_diff_groups,
)

_DIFF = """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,3 @@
 a
+b
 c
diff --git a/bar.py b/bar.py
--- a/bar.py
+++ b/bar.py
@@ -1 +1,2 @@
 x
+y
"""


class _FakeStructured:
    def __init__(self, result: Any, *, raises: bool = False) -> None:
        self._result = result
        self._raises = raises
        self.with_config_calls: list[dict[str, Any]] = []

    def with_config(self, **kwargs: Any) -> _FakeStructured:
        self.with_config_calls.append(kwargs)
        return self

    async def ainvoke(self, _prompt: str) -> Any:
        if self._raises:
            raise RuntimeError("boom")
        return self._result


class _FakeModel:
    def __init__(self, result: Any, *, raises: bool = False) -> None:
        self._structured = _FakeStructured(result, raises=raises)

    def with_structured_output(self, _schema: Any) -> _FakeStructured:
        return self._structured


@pytest.mark.asyncio
async def test_generate_diff_groups_partitions() -> None:
    result = _DiffGroupingResult(
        groups=[
            _DiffGroupModel(title="Foo change", summary="Edits foo", files=["foo.py"]),
            _DiffGroupModel(title="Bar change", summary="Edits bar", files=["bar.py"]),
        ]
    )
    groups = await generate_diff_groups(diff_text=_DIFF, model=_FakeModel(result))
    assert groups == [
        {"title": "Foo change", "summary": "Edits foo", "files": ["foo.py"]},
        {"title": "Bar change", "summary": "Edits bar", "files": ["bar.py"]},
    ]


@pytest.mark.asyncio
async def test_generate_diff_groups_dedupes_and_drops_unknown() -> None:
    result = _DiffGroupingResult(
        groups=[
            _DiffGroupModel(
                title="First",
                summary="",
                files=["foo.py", "foo.py", "does-not-exist.py"],
            ),
            _DiffGroupModel(title="Second", summary="", files=["foo.py", "bar.py"]),
            _DiffGroupModel(title="", summary="ignored", files=["bar.py"]),
        ]
    )
    groups = await generate_diff_groups(diff_text=_DIFF, model=_FakeModel(result))
    # foo.py only in the first group, bar.py only in the second; the untitled
    # group and the unknown path are dropped.
    assert groups == [
        {"title": "First", "summary": "", "files": ["foo.py"]},
        {"title": "Second", "summary": "", "files": ["bar.py"]},
    ]


@pytest.mark.asyncio
async def test_generate_diff_groups_empty_diff_returns_empty() -> None:
    assert await generate_diff_groups(diff_text="", model=_FakeModel(None)) == []


@pytest.mark.asyncio
async def test_generate_diff_groups_llm_failure_returns_none() -> None:
    groups = await generate_diff_groups(diff_text=_DIFF, model=_FakeModel(None, raises=True))
    assert groups is None


@pytest.mark.asyncio
async def test_generate_diff_groups_attaches_langsmith_metadata() -> None:
    result = _DiffGroupingResult(
        groups=[_DiffGroupModel(title="Foo", summary="", files=["foo.py"])]
    )
    model = _FakeModel(result)
    await generate_diff_groups(
        diff_text=_DIFF,
        model=model,
        model_id="fireworks:accounts/fireworks/models/glm-5p2",
    )
    assert len(model._structured.with_config_calls) == 1
    call = model._structured.with_config_calls[0]
    assert call["run_name"] == "reviewer.diff_grouping"
    assert "reviewer" in call["tags"] and "diff_grouping" in call["tags"]
    metadata = call["metadata"]
    assert metadata["graph_id"]
    assert metadata["ls_provider"] == "fireworks"
    assert metadata["ls_model_name"] == "accounts/fireworks/models/glm-5p2"


def test_build_prompt_includes_files_and_diffs() -> None:
    prompt = _build_prompt(_DIFF, ["foo.py", "bar.py"])
    assert "### foo.py" in prompt
    assert "### bar.py" in prompt
    # Hunks are rendered as plain diff fences (no line-range annotations).
    assert "lines 1-3:" not in prompt
    assert "```diff" in prompt
    # The verbatim file list is included so every file can be assigned.
    assert "- foo.py" in prompt
    assert "- bar.py" in prompt


def test_diff_signature_is_stable_and_content_sensitive() -> None:
    assert diff_signature(_DIFF) == diff_signature(_DIFF)
    assert diff_signature(_DIFF) != diff_signature(_DIFF + "\n+extra")


@pytest.mark.asyncio
async def test_maybe_generate_skips_when_signature_unchanged() -> None:
    existing = {
        "diff_groups": {
            "signature": diff_signature(_DIFF),
            "groups": [{"title": "t", "summary": "", "files": ["foo.py"]}],
        }
    }
    with (
        patch(
            "agent.reviewer_groups.get_thread_metadata",
            new_callable=AsyncMock,
            return_value=existing,
        ),
        patch("agent.reviewer_groups.generate_diff_groups", new_callable=AsyncMock) as gen,
        patch(
            "agent.reviewer_groups.set_reviewer_thread_metadata",
            new_callable=AsyncMock,
        ) as set_meta,
    ):
        await maybe_generate_and_store_diff_groups(
            thread_id="t1", head_sha="h", diff_text=_DIFF, model=MagicMock()
        )
    gen.assert_not_awaited()
    set_meta.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_generate_stores_when_changed() -> None:
    groups = [{"title": "t", "summary": "s", "files": ["foo.py"]}]
    with (
        patch(
            "agent.reviewer_groups.get_thread_metadata",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "agent.reviewer_groups.generate_diff_groups",
            new_callable=AsyncMock,
            return_value=groups,
        ) as gen,
        patch(
            "agent.reviewer_groups.set_reviewer_thread_metadata",
            new_callable=AsyncMock,
        ) as set_meta,
    ):
        await maybe_generate_and_store_diff_groups(
            thread_id="t1", head_sha="head123", diff_text=_DIFF, model=MagicMock()
        )
    gen.assert_awaited_once()
    set_meta.assert_awaited_once()
    payload = set_meta.call_args.kwargs["extra"]["diff_groups"]
    assert payload["head_sha"] == "head123"
    assert payload["signature"] == diff_signature(_DIFF)
    assert payload["groups"] == groups
