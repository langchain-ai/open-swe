"""The append-only transcript event log that serves a thread to the web app.

``engine`` is the only writer, ``snapshot`` and ``routes`` are the read path,
and ``listener`` is how a reader learns that a thread grew.
"""
