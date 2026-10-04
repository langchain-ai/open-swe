"""Telling the dashboard what changed, so it never has to poll.

Writers call ``outbox.publish`` with the ``topics`` they changed; ``hub`` routes
each notification to this process's open streams, and ``routes`` serves them.
"""
