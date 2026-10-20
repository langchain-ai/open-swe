"""snake_case because these keys are the wire payload: the Node runtime, the
Python runtime and the Rust CLI share one warehouse table, and no serializer
sits in between for the spellings to drift apart in.
"""

from __future__ import annotations

from typing import TypedDict


class TelemetryEvent(TypedDict):
    event_id: str
    event_type: str
    schema_version: str
    created_at: str
    distinct_id: str
