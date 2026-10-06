"""Telling the dashboard which cached data went stale, so it never has to poll.

Writers call ``outbox.invalidate`` with the ``topics`` they changed; ``hub``
routes each notification to this process's open streams, and ``routes`` serves
them.
"""
