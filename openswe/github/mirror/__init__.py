"""GitHub's view of pull requests, kept in PostgreSQL so reads never wait on GitHub.

Three writers keep it current:

- ``ingest`` writes each webhook delivery as it arrives, before anything else
  handles it, and invalidates the pull requests it touched.
- ``sync`` reads from the API what deliveries leave out: mergeability and the
  review decision GitHub computes (``refresh``), and everything at once on first
  sight, after a push and whenever a delivery may have been missed (``hydrate``).
- the sweep compares each repository's recently updated pull requests with what
  is stored, backfills new repositories, and drops CI for commits nobody shows.

``views`` answers the dashboard from the stored rows. Reads that gate a write to
GitHub (merging, approving, marking ready) stay live: a mirror a second behind is
fine to show and wrong to act on.
"""
