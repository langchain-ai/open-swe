"""Helpers for LangGraph SDK / store JSON values that type as ``dict | None``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeAlias, cast

from langgraph_sdk.schema import Run, Thread, ThreadState

JsonObject: TypeAlias = dict[str, Any]
ThreadLike: TypeAlias = Thread | Mapping[str, Any]
RunLike: TypeAlias = Run | Mapping[str, Any]
ThreadStateLike: TypeAlias = ThreadState | Mapping[str, Any]


def as_json_object(value: Any) -> JsonObject:
    """Return ``value`` if it is a ``dict``, otherwise ``{}``."""
    return value if isinstance(value, dict) else {}


def thread_metadata(thread: ThreadLike) -> JsonObject:
    return as_json_object(thread.get("metadata") if isinstance(thread, Mapping) else None)


def run_metadata(run: RunLike) -> JsonObject:
    return as_json_object(run.get("metadata") if isinstance(run, Mapping) else None)


def as_thread_dict(thread: ThreadLike) -> JsonObject:
    """Normalize a SDK ``Thread`` (TypedDict) to a plain ``dict`` for helpers."""
    if isinstance(thread, dict):
        return cast(JsonObject, thread)
    return dict(thread)
