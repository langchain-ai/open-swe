"""The deployment switch that decides who serves a thread to the dashboard.

Off by default, and about reading only. A deployment with PostgreSQL records
every new thread into the event log either way, so the log is populated and
verifiable before anything reads from it; this flag decides whether the UI is
pointed at it. Flipping it off is therefore a full rollback and not a one-way
door: a thread that was recorded is served from LangGraph state again, which
the graph writes regardless, and its transcript keeps growing for the next
attempt.
"""

from agent.config import ENV


def transcript_serving_enabled() -> bool:
    """Whether a recorded thread is served from the event log rather than state."""
    return ENV.TRANSCRIPT_EVENT_LOG.get_bool()
