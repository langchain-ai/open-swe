"""The deployment switch that decides whether the transcript event log is live.

Off by default: a deployment carrying the event log code still serves every
thread from LangGraph state until ``TRANSCRIPT_EVENT_LOG`` is set. The flag is
read on both sides of the log, so flipping it off is a full rollback —
no new thread is recorded, and an already recorded one is served from
LangGraph state again (the graph writes that state either way).
"""

from agent.config import ENV
from agent.database import postgres


def transcript_enabled() -> bool:
    """Whether threads are recorded into, and served from, the event log.

    Requires PostgreSQL: without it there is nowhere to keep a transcript, so a
    thread must not be stamped as one — the UI would switch to a read path with
    no rows behind it.
    """
    return ENV.TRANSCRIPT_EVENT_LOG.get_bool() and postgres.configured()
