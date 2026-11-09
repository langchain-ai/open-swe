"""Commands Robin queues for the dashboard page a person chats beside.

Each browser tab that sends a message with its page's tools is a page session. A
call to one of those tools queues a command for that session and answers with its
position; the tab hears the queue change through UI invalidations, carries the
commands out in order and records each one's result, which Robin reads with
``read_page_queue``.
"""
