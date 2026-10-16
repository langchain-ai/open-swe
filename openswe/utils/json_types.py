"""Helpers for LangGraph SDK / store JSON values that type as ``dict | None``."""

from collections.abc import Mapping
from typing import Any, cast

from langgraph_sdk.schema import Run, Thread, ThreadState

type JsonObject = dict[str, Any]
type ThreadLike = Thread | Mapping[str, Any]
type RunLike = Run | Mapping[str, Any]
type ThreadStateLike = ThreadState | Mapping[str, Any]


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
