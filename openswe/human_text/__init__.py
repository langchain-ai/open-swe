"""Everything people write that Open SWE sees, embedded in pgvector and searchable.

``sources`` turns Slack messages and Open SWE thread messages into ``human_text``
rows, ``embeddings`` fills in their vectors in the background, and ``search``
reads them back for whoever is asking, hiding what they may not see.
"""
