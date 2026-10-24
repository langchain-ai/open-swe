"""Everything people write in public that Open SWE sees, embedded in pgvector and searchable.

``sources`` turns messages in public Slack channels and public Open SWE threads
into ``human_text`` rows, ``embeddings`` fills in their vectors in the
background, and ``search`` reads them back, dropping any whose channel or thread
has since gone private.
"""
