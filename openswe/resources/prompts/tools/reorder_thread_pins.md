Reorder the requester's pinned dashboard threads, persisting their personal sidebar order across devices.

Pass `thread_ids` in the desired order.
Every ID must already be pinned by the requester, and duplicates are rejected.
Omitted pins keep their relative order after the supplied IDs; newly pinned threads are appended.
This does not pin, unpin, share, or change access to any thread.
Only a verified triggering person can reorder their own pins; automation runs cannot do so.
