"""The append-only transcript event log that serves a thread to the dashboard.

``engine`` is the only writer, ``snapshot`` and ``routes`` are the read path,
and ``listener`` is how a reader learns that a thread grew. ``rows`` is imported
here so the ORM mappings register with the shared declarative base.
"""

from agent.transcript import rows
from agent.transcript.engine import (
    AppendResult,
    Command,
    ThreadNotTranscribed,
    append,
    delete_transcript,
    has_transcript,
)
from agent.transcript.mirror import mirror_thread_metadata

__all__ = [
    "AppendResult",
    "Command",
    "ThreadNotTranscribed",
    "append",
    "delete_transcript",
    "has_transcript",
    "mirror_thread_metadata",
    "rows",
]
