"""Telling the dashboard which cached data went stale, so it never has to poll.

A writer calls ``Topic.<NAME>.invalidate(...)`` for what it changed; ``hub``
routes each notification to this process's open streams, and ``routes`` serves
them.
"""

from agent.ui_invalidations.topics import Topic

__all__ = ["Topic"]
